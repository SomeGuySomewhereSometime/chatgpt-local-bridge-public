"""Screenshot reuse respects grant, selected monitor, request semantics and fallback."""
import base64
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bridge


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.png = b'\x89PNG\r\n\x1a\n' + b'\x00' * 24
        self.source = self.root / 'source.png'
        self.source.write_bytes(self.png)
        self.frame = {'available': True, 'image': {'data': base64.b64encode(self.png).decode(),
                                                'session_id': 'authorized', 'width': 10, 'height': 20}}
        for key, value in (('WORKSPACE', self.root),
                           ('BRIDGE_CONFIG', {'desktop': {'enabled': True}})):
            p = patch.object(bridge, key, value); p.start(); self.addCleanup(p.stop)

    def capture(self, **kwargs):
        return bridge.screen_capture(bridge.ScreenCaptureRequest(path='target.png', **kwargs))

    def test_reuses_live_stream_without_screenshot_dialog(self):
        with patch.object(bridge.desktop_client, 'capture_existing', return_value=self.frame) as read, \
             patch.object(bridge, '_portal_capture') as legacy:
            result = self.capture()
        read.assert_called_once_with(); legacy.assert_not_called()
        self.assertEqual(result['backend'], 'xdg-remote-desktop-pipewire')
        self.assertEqual(result['capture_scope'], 'selected-monitor')
        self.assertEqual(result['session_id'], 'authorized')
        self.assertEqual((self.root / 'target.png').read_bytes(), self.png)

    def test_inactive_stream_invalid_frame_or_pipewire_error_preserves_fallback(self):
        for response in ({'available': False}, {'available': True, 'image': {'data': 'invalid'}}):
            with patch.object(bridge.desktop_client, 'capture_existing', return_value=response), \
                 patch.object(bridge, '_portal_capture', return_value=self.source.as_uri()):
                self.assertEqual(self.capture()['backend'], 'xdg-desktop-portal')
        with patch.object(bridge.desktop_client, 'capture_existing', side_effect=RuntimeError('stream closed')), \
             patch.object(bridge, '_portal_capture', return_value=self.source.as_uri()):
            self.assertEqual(self.capture()['backend'], 'xdg-desktop-portal')

    def test_interactive_and_without_cursor_keep_original_semantics(self):
        for options in ({'interactive': True}, {'include_cursor': False}):
            with patch.object(bridge.desktop_client, 'capture_existing') as read, \
                 patch.object(bridge, '_portal_capture', return_value=self.source.as_uri()):
                self.assertEqual(self.capture(**options)['backend'], 'xdg-desktop-portal')
            read.assert_not_called()

    def test_disabled_desktop_never_spawns_helper(self):
        with patch.object(bridge, 'BRIDGE_CONFIG', {}), \
             patch.object(bridge.desktop_client, 'capture_existing') as read, \
             patch.object(bridge, '_portal_capture', return_value=self.source.as_uri()):
            self.capture()
        read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
