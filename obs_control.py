"""Bounded OBS WebSocket v5 recording control; no generic RPC or shell tool."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
import time
import uuid

from websockets.sync.client import connect
from websockets.exceptions import WebSocketException


class OBSError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _hash(value: str) -> str:
    return base64.b64encode(hashlib.sha256(value.encode()).digest()).decode()


class OBSConnection:
    def __init__(self, ws, deadline: float):
        self.ws, self.deadline = ws, deadline
        self.mutation_sent = False
        self.mutation_accepted = False

    def receive(self, opcode: int) -> dict:
        for _ in range(32):
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            frame = json.loads(self.ws.recv(timeout=remaining))
            if not isinstance(frame, dict) or not isinstance(frame.get('d'), dict):
                raise OBSError('protocol_error', 'Invalid OBS protocol response')
            if frame.get('op') == 5:
                continue
            if frame.get('op') != opcode:
                raise OBSError('protocol_error', 'Unexpected OBS protocol response')
            return frame['d']
        raise OBSError('protocol_error', 'Too many unexpected OBS events')

    def request(self, name: str, *, mutation: bool = False) -> dict:
        # This client intentionally supports only fixed, parameterless requests.
        if name not in {'GetVersion', 'GetRecordStatus', 'GetRecordDirectory',
                        'GetSceneList', 'StartRecord', 'StopRecord'}:
            raise OBSError('unsupported_operation', 'Unsupported OBS operation')
        request_id = uuid.uuid4().hex
        if mutation:
            # Even a failed send may have reached OBS; never replay automatically.
            self.mutation_sent = True
        self.ws.send(json.dumps({'op': 6, 'd': {'requestType': name, 'requestId': request_id}}))
        response = self.receive(7)
        if response.get('requestId') != request_id or response.get('requestType') != name:
            raise OBSError('protocol_error', 'OBS response does not match the request')
        status = response.get('requestStatus', {})
        if not isinstance(status, dict):
            raise OBSError('protocol_error', 'Invalid OBS request status')
        if status.get('result') is not True:
            if mutation:
                self.mutation_sent = False  # OBS explicitly rejected this request.
            code = status.get('code')
            code = code if type(code) is int else 'unknown'
            # Never echo server comments: they may contain private configuration.
            raise OBSError('obs_rejected', f'OBS rejected {name} (code {code})')
        if mutation:
            self.mutation_accepted = True
        data = response.get('responseData', {})
        if not isinstance(data, dict):
            raise OBSError('protocol_error', 'Invalid OBS response data')
        return data


class OBSController:
    def __init__(self, config: dict, path_check):
        self.config, self.path_check = config, path_check
        self.lock = threading.Lock()

    def _settings(self):
        config = self.config.get('obs', {})
        if not isinstance(config, dict):
            raise OBSError('configuration_error', 'Invalid OBS connection configuration')
        host = config.get('host', '127.0.0.1')
        port = config.get('port', 4455)
        if host not in ('127.0.0.1', '::1') or type(port) is not int or not 1 <= port <= 65535:
            raise OBSError('configuration_error', 'OBS must use a numeric loopback address and valid port')
        secret = config.get('password_file', str(Path.home() / '.config/mcp-integration/obs-password'))
        if not isinstance(secret, str) or not Path(secret).is_absolute():
            raise OBSError('configuration_error', 'OBS password_file must be an absolute path')
        try:
            fd = os.open(secret, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd) as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise OBSError('configuration_error', 'OBS password file must be private and owned by this user')
                password = stream.read(4097).rstrip('\r\n')
                if not password or len(password) > 4096:
                    raise OBSError('configuration_error', 'Invalid OBS password file')
        except OSError:
            raise OBSError('configuration_error', 'OBS password file is missing or inaccessible') from None
        address = '[' + host + ']' if host == '::1' else host
        return f'ws://{address}:{port}', password

    @contextmanager
    def connection(self):
        uri, password = self._settings()
        # No proxies, no redirects/reconnect iterator, bounded messages and waits.
        with connect(uri, proxy=None, open_timeout=3, close_timeout=1,
                     max_size=262144, max_queue=8, compression=None) as ws:
            connection = OBSConnection(ws, time.monotonic() + 12)
            hello = connection.receive(0)
            auth = hello.get('authentication')
            if not isinstance(auth, dict):
                raise OBSError('authentication_required', 'Enable OBS WebSocket authentication before using the Bridge')
            salt, challenge = auth.get('salt'), auth.get('challenge')
            if not isinstance(salt, str) or not isinstance(challenge, str):
                raise OBSError('protocol_error', 'Invalid OBS authentication challenge')
            ws.send(json.dumps({'op': 1, 'd': {'rpcVersion': 1, 'eventSubscriptions': 0,
                       'authentication': _hash(_hash(password + salt) + challenge)}}))
            if connection.receive(2).get('negotiatedRpcVersion') != 1:
                raise OBSError('protocol_error', 'Unsupported OBS RPC version')
            yield connection

    @staticmethod
    def _recording(connection):
        data = connection.request('GetRecordStatus')
        if type(data.get('outputActive')) is not bool or type(data.get('outputPaused')) is not bool:
            raise OBSError('protocol_error', 'OBS recording state is incomplete')
        return {key: data[key] for key in ('outputActive', 'outputPaused', 'outputTimecode',
                                         'outputDuration', 'outputBytes') if key in data}

    def _snapshot(self, connection):
        version = connection.request('GetVersion')
        scenes = connection.request('GetSceneList')
        directory = connection.request('GetRecordDirectory').get('recordDirectory')
        return {'connected': True, 'obs_version': version.get('obsVersion'),
                'websocket_version': version.get('obsWebSocketVersion'),
                'current_scene': scenes.get('currentProgramSceneName'),
                'scenes': [s.get('sceneName') for s in scenes.get('scenes', [])[:100] if isinstance(s, dict)],
                'record_directory': directory, 'recording': self._recording(connection)}

    def execute(self, operation: str) -> dict:
        if operation not in ('status', 'start', 'stop'):
            return {'ok': False, 'code': 'unsupported_operation', 'message': 'Unsupported OBS operation'}
        if not self.lock.acquire(timeout=1):
            return {'ok': False, 'code': 'busy', 'message': 'Another OBS operation is running', 'mutation_sent': False}
        connection = None
        try:
            with self.connection() as connection:
                if operation == 'status':
                    return {'ok': True, **self._snapshot(connection)}
                before = self._recording(connection)
                target = operation == 'start'
                if before['outputActive'] == target:
                    return {'ok': True, 'connected': True, 'changed': False,
                            'recording': before, 'output_path': None,
                            'message': 'Already recording' if target else 'Already stopped; no recording path recovered'}
                if target:
                    directory = connection.request('GetRecordDirectory').get('recordDirectory')
                    if not isinstance(directory, str) or not Path(directory).is_absolute():
                        raise OBSError('configuration_error', 'OBS recording directory must be absolute')
                    self.path_check(directory, write=True)
                result = connection.request('StartRecord' if target else 'StopRecord', mutation=True)
                after = self._recording(connection)
                # Recording startup/finalization can complete after the acknowledgement.
                for _ in range(10):
                    if after['outputActive'] == target:
                        break
                    time.sleep(0.15)
                    after = self._recording(connection)
                verified = after['outputActive'] == target
                return {'ok': verified, 'connected': True, 'changed': True,
                        'mutation_sent': True, 'mutation_accepted': True, 'verified': verified,
                        'code': 'ok' if verified else 'state_unconfirmed',
                        'recording': after, 'output_path': result.get('outputPath'),
                        'message': 'Recording state verified' if verified else 'Check obs_status before any further action; do not replay automatically'}
        except Exception as exc:
            sent = bool(connection and connection.mutation_sent)
            accepted = bool(connection and connection.mutation_accepted)
            if isinstance(exc, OBSError):
                code, message = exc.code, str(exc)
            elif isinstance(exc, (OSError, TimeoutError, WebSocketException)):
                code, message = 'unavailable', 'Cannot communicate with OBS; open OBS and check its authenticated WebSocket server'
            else:
                # Includes filesystem denials and malformed responses. Do not leak raw errors.
                code, message = 'operation_failed', 'OBS operation failed or recording directory is not allowed'
            if sent:
                code, message = 'outcome_unknown', 'OBS may have processed the recording command; check obs_status and do not replay automatically'
            return {'ok': False, 'code': code, 'message': message,
                    'mutation_sent': sent, 'mutation_accepted': accepted}
        finally:
            self.lock.release()
