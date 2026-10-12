"""OBS protocol and failure tests against an isolated authenticated WebSocket server."""
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from websockets.sync.server import serve
from obs_control import OBSController, _hash


class Fixture:
    def __init__(self, directory, mode='normal'):
        self.directory, self.mode = directory, mode
        self.active = False
        self.requests = []
        self.identities = []
        self.start_count = self.stop_count = 0

    def handle(self, ws):
        hello = {'rpcVersion': 1, 'authentication': {'salt': 'salt', 'challenge': 'challenge'}}
        if self.mode == 'noauth':
            hello.pop('authentication')
        ws.send(json.dumps({'op': 0, 'd': hello}))
        if self.mode == 'noauth':
            return
        identity = json.loads(ws.recv())['d']
        self.identities.append(identity)
        if identity['authentication'] != _hash(_hash('test-password' + 'salt') + 'challenge'):
            ws.close(4009, 'private diagnostic must not leak')
            return
        ws.send(json.dumps({'op': 2, 'd': {'negotiatedRpcVersion': 1}}))
        try:
            for raw in ws:
                request = json.loads(raw)['d']
                name = request['requestType']
                self.requests.append(name)
                data = {}
                status = {'result': True, 'code': 100}
                if name == 'GetVersion':
                    data = {'obsVersion': 'fixture', 'obsWebSocketVersion': '5'}
                elif name == 'GetSceneList':
                    data = {'currentProgramSceneName': 'Fixture', 'scenes': [{'sceneName': 'Fixture'}]}
                elif name == 'GetRecordDirectory':
                    data = {'recordDirectory': self.directory}
                elif name == 'GetRecordStatus':
                    data = {'outputActive': self.active, 'outputPaused': False, 'outputDuration': 100}
                    if self.mode == 'incomplete':
                        data = {}
                elif name == 'StartRecord':
                    self.start_count += 1
                    if self.mode == 'reject':
                        status = {'result': False, 'code': 701, 'comment': 'private-secret'}
                    else:
                        self.active = True
                    if self.mode == 'lost-receipt':
                        ws.close()
                        return
                elif name == 'StopRecord':
                    self.stop_count += 1
                    self.active = False
                    data = {'outputPath': self.directory + '/fixture.mkv'}
                request_id = 'wrong' if self.mode == 'wrong-id' else request['requestId']
                ws.send(json.dumps({'op': 7, 'd': {'requestId': request_id, 'requestType': name,
                        'requestStatus': status, 'responseData': data}}))
        except Exception:
            # Client disconnects deliberately in failure tests.
            pass


class OBSTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bridge-obs-test-')
        self.addCleanup(self.tmp.cleanup)
        self.secret = Path(self.tmp.name) / 'password'
        self.secret.write_text('test-password\n')
        self.secret.chmod(0o600)
        self.path_check = Mock()

    @contextmanager
    def fixture(self, mode='normal'):
        fixture = Fixture(self.tmp.name, mode)
        with serve(fixture.handle, '127.0.0.1', 0) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            controller = OBSController({'obs': {'port': server.socket.getsockname()[1],
                        'password_file': str(self.secret)}}, self.path_check)
            try:
                yield controller, fixture
            finally:
                server.shutdown()
                thread.join(3)

    def test_authenticated_status_start_stop_and_noop(self):
        with self.fixture() as (controller, fixture):
            result = controller.execute('status')
            self.assertTrue(result['ok'])
            self.assertFalse(result['recording']['outputActive'])
            self.assertEqual(result['current_scene'], 'Fixture')
            started = controller.execute('start')
            self.assertTrue(started['verified'])
            self.assertFalse(controller.execute('start')['changed'])
            self.assertEqual(fixture.start_count, 1)
            stopped = controller.execute('stop')
            self.assertTrue(stopped['verified'])
            self.assertTrue(stopped['output_path'].endswith('/fixture.mkv'))
            self.assertFalse(controller.execute('stop')['changed'])
            self.assertEqual(fixture.stop_count, 1)
            self.path_check.assert_called_once_with(self.tmp.name, write=True)
            self.assertTrue(all(i['eventSubscriptions'] == 0 for i in fixture.identities))

    def test_lost_receipt_is_unknown_and_not_replayed(self):
        with self.fixture('lost-receipt') as (controller, fixture):
            result = controller.execute('start')
            self.assertFalse(result['ok'])
            self.assertEqual(result['code'], 'outcome_unknown')
            self.assertTrue(result['mutation_sent'])
            self.assertEqual(fixture.start_count, 1)
            self.assertTrue(controller.execute('status')['recording']['outputActive'])
            self.assertEqual(fixture.start_count, 1)

    def test_rejection_is_known_and_server_comment_is_redacted(self):
        with self.fixture('reject') as (controller, fixture):
            result = controller.execute('start')
            self.assertEqual(result['code'], 'obs_rejected')
            self.assertFalse(result['mutation_sent'])
            self.assertNotIn('private-secret', json.dumps(result))

    def test_denied_directory_never_starts_but_stop_still_works(self):
        self.path_check.side_effect = PermissionError('private-path')
        with self.fixture() as (controller, fixture):
            result = controller.execute('start')
            self.assertFalse(result['ok'])
            self.assertEqual(fixture.start_count, 0)
            self.assertNotIn('private-path', json.dumps(result))
            fixture.active = True
            self.assertTrue(controller.execute('stop')['ok'])

    def test_remote_endpoint_refused_before_network(self):
        controller = OBSController({'obs': {'host': 'example.com'}}, self.path_check)
        with patch('obs_control.connect') as connect:
            self.assertEqual(controller.execute('status')['code'], 'configuration_error')
            connect.assert_not_called()

    def test_insecure_password_file_refused(self):
        self.secret.chmod(0o644)
        with self.fixture() as (controller, fixture):
            self.assertEqual(controller.execute('status')['code'], 'configuration_error')
            self.assertEqual(fixture.requests, [])

    def test_authentication_required(self):
        with self.fixture('noauth') as (controller, fixture):
            self.assertEqual(controller.execute('start')['code'], 'authentication_required')
            self.assertEqual(fixture.start_count, 0)

    def test_wrong_password_is_redacted(self):
        self.secret.write_text('wrong-password')
        with self.fixture() as (controller, fixture):
            result = controller.execute('status')
            self.assertFalse(result['ok'])
            self.assertNotIn('private diagnostic', json.dumps(result))
            self.assertNotIn('wrong-password', json.dumps(result))

    def test_incomplete_state_never_starts(self):
        with self.fixture('incomplete') as (controller, fixture):
            self.assertEqual(controller.execute('start')['code'], 'protocol_error')
            self.assertEqual(fixture.start_count, 0)

    def test_mismatched_response_refused(self):
        with self.fixture('wrong-id') as (controller, fixture):
            self.assertEqual(controller.execute('status')['code'], 'protocol_error')

    def test_mcp_catalogue_and_errors(self):
        import mcp_server
        tools = asyncio.run(__import__('test_profile_helpers').desktop_tools())
        names = {t.name for t in tools}
        self.assertTrue({'obs_status', 'obs_start_recording', 'obs_stop_recording'} <= names)
        with patch.object(mcp_server.bridge.obs_controller, 'execute', return_value={
                'ok': False, 'code': 'outcome_unknown', 'mutation_sent': True}) as execute:
            result = mcp_server.obs_start_recording()
            self.assertTrue(result.is_error)
            self.assertTrue(result.structured_content['mutation_sent'])
            execute.assert_called_once_with('start')
        self.assertNotIn('obs_toggle', names)


if __name__ == '__main__':
    unittest.main()
