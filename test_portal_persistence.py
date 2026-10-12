"""RemoteDesktop wire options, response handling and capture reuse, no live consent."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

try:
    from desktop_worker import Portal
except ModuleNotFoundError as exc:
    raise unittest.SkipTest('Portal tests require system Python GI') from exc

from portal_restore import PortalPersistence, RestoreTokenStore


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = RestoreTokenStore(Path(self.tmp.name) / 'grants')
        self.addCleanup(self.store.release)
        p = self.portal = Portal.__new__(Portal)
        p.persistence = PortalPersistence(2, self.store)
        p.state, p.session_id, p.streams = 'closed', '', []
        p.pipeline, p.fd, p.error, p.pending = None, None, None, None
        p.last_activity, p.generation, p.closed_match, p.session = time.monotonic(), 0, None, None
        p.parent_window, p.parent_surface = None, None
        p.parent_handle, p.parent_raw_handle = '', ''
        p.obj = Mock()
        p.bus, p.dbus = Mock(), Mock()
        p.bus.get_unique_name.return_value = ":1.99"
        for name in ('String', 'ObjectPath', 'UInt32', 'Boolean'):
            getattr(p.dbus, name).side_effect = lambda value: value
        self.calls = []
        def prepare(callback):
            p.parent_handle = 'wayland:test-parent'
            callback()
        p._prepare_parent_window = prepare
        self.start_response = {'devices': 3, 'streams': [(7, {'logical_size': [100, 100]})],
                               'restore_token': 'new-grant'}
        def request(interface, method, args, options, callback):
            self.calls.append((interface, method, args, options))
            if method == 'CreateSession':
                callback({'session_handle': '/session'})
            elif method in ('SelectDevices', 'SelectSources'):
                callback({})
            elif method == 'Start':
                callback(self.start_response)
        p._request = request

    def test_combined_session_places_persistence_only_on_remote_devices(self):
        self.store.begin(); self.store.save('old-grant'); self.store.release()
        result = self.portal.start()
        devices = next(c[3] for c in self.calls if c[1] == 'SelectDevices')
        self.assertEqual(devices, {'types': 3, 'persist_mode': 2, 'restore_token': 'old-grant'})
        cast = next(c[3] for c in self.calls if c[1] == 'SelectSources')
        self.assertNotIn('persist_mode', cast)
        self.assertNotIn('restore_token', cast)
        self.assertEqual(result['state'], 'active')
        self.assertNotIn('old-grant', json.dumps(result))
        self.assertNotIn('new-grant', json.dumps(result))
        self.assertEqual(self.store.begin(), 'new-grant'); self.store.release()

    def test_repeated_start_preserves_active_session_and_stop_preserves_grant(self):
        self.portal.start()
        count = len(self.calls)
        self.portal.start()
        self.assertEqual(len(self.calls), count)
        self.portal.close()
        self.assertTrue(self.store.has_token())

    def test_unsupported_api_uses_original_consent(self):
        self.portal.persistence = PortalPersistence(1, self.store)
        self.portal.start()
        options = next(c[3] for c in self.calls if c[1] == 'SelectDevices')
        self.assertEqual(options['persist_mode'], 0)
        self.assertNotIn('restore_token', options)
        self.assertFalse(self.store.directory.exists())

    def test_pending_timeout_invalidates_callbacks_and_releases_reservation(self):
        self.portal._request = Mock()
        self.portal.start()
        generation = self.portal.generation
        self.portal.started = time.monotonic() - 91
        self.portal.tick()
        self.assertEqual(self.portal.state, 'error')
        self.assertGreater(self.portal.generation, generation)
        self.assertIsNone(self.store.lock_fd)

    def test_single_monitor_and_devices_still_required(self):
        for response in ({'devices': 1, 'streams': []},
                         {'devices': 3, 'streams': [(7, {'size': [0, 10]})]}):
            self.start_response = response
            # Real _request catches callback errors and closes the session.
            self.assertEqual(self.portal.start()['state'], 'error')
            self.portal.close()

    def test_capture_read_never_starts_or_restores_a_session(self):
        self.portal.capture = Mock()
        self.assertFalse(self.portal.capture_existing()['available'])
        self.portal.capture.assert_not_called()
        self.portal.state = 'active'
        self.portal.capture.return_value = {'session_id': 'current'}
        self.assertEqual(self.portal.capture_existing()['capture_scope'], 'selected-monitor')
        self.portal.capture.assert_called_once_with()
        self.assertEqual(self.calls, [])

    def real_request(self, response_code=None, error=None):
        p = self.portal
        p.generation += 1
        p.persistence.begin()
        interface = Mock()
        p.dbus.Interface.return_value = interface
        if error:
            interface.SelectDevices.side_effect = error
        p.start = Mock(return_value={})
        callback = Mock()
        Portal._request(p, p.REMOTE, 'SelectDevices', ['/session'], {'persist_mode': 2}, callback)
        response = p.bus.add_signal_receiver.call_args.args[0]
        if response_code is not None:
            response(response_code, {})
        return callback, response

    def test_cancelled_consent_is_never_retried(self):
        callback, _ = self.real_request(1)
        self.portal.start.assert_not_called()
        self.assertEqual(self.portal.state, 'error')
        self.assertIsNone(self.store.lock_fd)
        callback.assert_not_called()

    def test_explicit_persistence_rejection_has_one_transient_fallback(self):
        callback, _ = self.real_request(2)
        self.portal.start.assert_called_once_with(transient=True)
        callback.assert_not_called()
        # The transient attempt cannot request persistence or fall back again.
        self.portal.persistence.begin(transient=True)
        self.assertEqual(self.portal.persistence.requested_mode, 0)

    def test_dbus_unsupported_persistence_falls_back(self):
        error = RuntimeError('unsupported')
        error.get_dbus_name = lambda: 'org.freedesktop.portal.Error.NotSupported'
        self.real_request(error=error)
        self.portal.start.assert_called_once_with(transient=True)

    def test_callback_after_close_is_ignored(self):
        callback, response = self.real_request()
        self.portal.close()
        response(0, {'restore_token': 'must-not-be-used'})
        callback.assert_not_called()
        self.assertFalse(self.store.has_token())

    def test_closed_signal_preserves_grant_but_invalidates_input(self):
        self.portal.start()
        session_id = self.portal.session_id
        self.portal._closed()
        self.assertTrue(self.store.has_token())
        with self.assertRaises(ValueError):
            self.portal.require(session_id)


if __name__ == '__main__':
    unittest.main()
