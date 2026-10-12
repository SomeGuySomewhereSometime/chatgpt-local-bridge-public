"""Desktop protocol validation without reading or changing a user's desktop."""
import copy
import time
import unittest
from unittest.mock import Mock, patch

try:
    from desktop_worker import Desktop, Portal
except ModuleNotFoundError as exc:
    raise unittest.SkipTest('Run desktop protocol tests with /usr/bin/python3 for GI bindings') from exc


class Node:
    def __init__(self, text='initial', role='entry', active=True, focused=False, editable=True, caret=None, children=None):
        self.text = text
        self.role = role
        self.active = active
        self.focused = focused
        self.editable = editable
        self.caret = caret
        self.children = list(children or [])
        self.writes = []

    def get_editable_text_iface(self):
        return self

    def set_text_contents(self, text):
        self.writes.append(text)
        self.text = text
        return True

    def get_text_iface(self):
        return self

    def get_text(self, start, end):
        return self.text[start:end]

    def get_character_count(self):
        return len(self.text)

    def get_role(self):
        return 0

    def get_child_count(self):
        return len(self.children)

    def get_child_at_index(self, index):
        return self.children[index]


class FakeDesktop(Desktop):
    def describe(self, node):
        interfaces = ['EditableText', 'Text'] if node.editable else ['Text']
        return {'text': node.text, 'role': node.role, 'interfaces': interfaces,
                'states': ['showing', 'sensitive'] + (['editable'] if node.editable else [])
                          + (['active'] if node.active else []) + (['focused'] if node.focused else [])}

    def observe(self, **kwargs):
        self.snapshot = {'snapshot_id': 'next', 'captured_at': time.time(), 'elements': []}
        return self.snapshot


class DesktopTests(unittest.TestCase):
    def setUp(self):
        p = patch('desktop_worker.Atspi.Text.get_text', side_effect=lambda node, start, end: node.get_text(start, end))
        p.start(); self.addCleanup(p.stop)
        p = patch('desktop_worker.Atspi.Text.get_character_count', side_effect=lambda node: node.get_character_count())
        p.start(); self.addCleanup(p.stop)
        p = patch('desktop_worker.Atspi.Text.get_n_selections', return_value=0)
        p.start(); self.addCleanup(p.stop)
        p = patch('desktop_worker.Atspi.Text.get_caret_offset', side_effect=lambda node: node.caret if node.caret is not None else len(node.text))
        p.start(); self.addCleanup(p.stop)
        self.d = FakeDesktop(Mock())
        self.node = Node()
        self.window = Node(role='window')
        self.d.snapshot = {'snapshot_id': 'now', 'captured_at': time.time()}
        self.d.entries['e1'] = (self.node, self.d.signature(self.d.describe(self.node)), self.window, 'test')

    def test_stale_snapshot_never_mutates(self):
        with self.assertRaisesRegex(ValueError, 'Stale snapshot'):
            self.d.act('old', [{'kind': 'set_text', 'element': 'e1', 'text': 'bad'}])
        self.assertEqual(self.node.writes, [])

    def test_stale_element_and_inactive_window_are_rejected(self):
        self.node.text = 'changed elsewhere'
        with self.assertRaisesRegex(ValueError, 'Stale element'):
            self.d.resolve('e1')
        self.node.text = 'initial'
        self.window.active = False
        with self.assertRaisesRegex(ValueError, 'not active'):
            self.d.resolve('e1')

    def test_invalid_later_action_prevents_first_mutation(self):
        actions = [{'kind': 'set_text', 'element': 'e1', 'text': 'ok'}, {'kind': 'click', 'x': 2, 'y': .5}]
        with self.assertRaises(ValueError):
            self.d.act('now', actions)
        self.assertEqual(self.node.writes, [])

    @patch('desktop_worker.pump')
    def test_text_is_verified_by_readback_and_snapshot_is_consumed(self, _):
        r = self.d.act('now', [{'kind': 'set_text', 'element': 'e1', 'text': 'Olá, ação'}], wait_ms=0)
        self.assertTrue(r['ok'])
        self.assertTrue(r['results'][0]['verified'])
        with self.assertRaisesRegex(ValueError, 'Stale snapshot'):
            self.d.act('now', [{'kind': 'set_text', 'element': 'e1', 'text': 'again'}])

    @patch('desktop_worker.pump')
    def test_partial_batch_stops_when_target_changes(self, _):
        r = self.d.act('now', [{'kind': 'set_text', 'element': 'e1', 'text': 'first'},
                               {'kind': 'set_text', 'element': 'e1', 'text': 'second'}], wait_ms=0)
        self.assertFalse(r['ok'])
        self.assertEqual(r['completed'], 1)
        self.assertEqual(self.node.writes, ['first'])

    def test_raw_input_requires_matching_observed_monitor(self):
        with self.assertRaisesRegex(ValueError, 'screenshot observation'):
            self.d.act('now', [{'kind': 'click', 'x': .5, 'y': .5}], session_id='session')
        self.d.portal.perform.assert_not_called()

    @patch('desktop_worker.pump')
    def test_type_text_on_unique_focused_editable_does_not_require_portal(self, _):
        self.node.focused = True
        self.d.entries['e1'] = (self.node, self.d.signature(self.d.describe(self.node)), self.window, 'test')
        receipt = {'kind': 'type_text', 'executed': True, 'verified': True,
                   'backend': 'AT-SPI EditableText', 'evidence': 'readback'}
        with patch.object(self.d, 'insert_focused_text', return_value=receipt):
            result = self.d.act('now', [{'kind': 'type_text', 'text': 'Olá'}], wait_ms=0)
        self.assertTrue(result['ok'])
        self.assertTrue(result['results'][0]['verified'])
        self.d.portal.require.assert_not_called()
        self.d.portal.perform.assert_not_called()

    def test_focused_wrapper_uses_single_sane_editable_descendant(self):
        child = Node(text='conteúdo', focused=False, caret=8)
        self.node.focused = True
        self.node.caret = -1
        self.node.children = [child]
        self.d.entries['e1'] = (self.node, self.d.signature(self.d.describe(self.node)), self.window, 'test')
        plan = self.d.focused_text_plan()
        self.assertTrue(plan['semantic'])
        self.assertIs(plan['node'], child)
        self.assertEqual(plan['backend_detail'], 'focused editable descendant')

    def test_invalid_caret_whitespace_descendant_gets_semantic_replace_plan(self):
        child = Node(text='   ', focused=False, caret=-1)
        self.node.focused = True
        self.node.caret = -1
        self.node.children = [child]
        self.d.entries['e1'] = (self.node, self.d.signature(self.d.describe(self.node)), self.window, 'test')
        plan = self.d.focused_text_plan()
        self.assertTrue(plan['semantic'])
        self.assertIs(plan['node'], child)
        self.assertEqual((plan['start'], plan['end']), (0, len(child.text)))
        self.assertTrue(plan['replace_empty_whitespace'])
        self.assertEqual(plan['backend_detail'], 'focused empty editable descendant')

    @patch('desktop_worker.pump')
    def test_semantic_insert_uses_character_length_for_unicode(self, _):
        self.node.focused = True
        self.node.text = ''
        self.node.caret = 0
        self.d.entries['e1'] = (self.node, self.d.signature(self.d.describe(self.node)), self.window, 'test')

        def insert(editable, position, text, length):
            self.assertEqual(length, len(text))
            editable.text = editable.text[:position] + text + editable.text[position:]
            return True

        with patch('desktop_worker.Atspi.EditableText.insert_text', side_effect=insert), \
             patch('desktop_worker.Atspi.Text.set_caret_offset', return_value=True):
            result = self.d.insert_focused_text('ã—')
        self.assertTrue(result['verified'])
        self.assertEqual(self.node.text, 'ã—')

    def test_expired_observation_rejected(self):
        self.d.snapshot['captured_at'] -= 121
        with self.assertRaisesRegex(ValueError, 'expired'):
            self.d.act('now', [{'kind': 'set_text', 'element': 'e1', 'text': 'bad'}])

    def test_payload_limits_and_no_code_field(self):
        for action in [{'kind': 'click', 'x': float('nan'), 'y': 0},
                       {'kind': 'click', 'x': True, 'y': 0},
                       {'kind': 'key', 'keys': ['CTRL', 'CTRL']},
                       {'kind': 'key', 'keys': ['not-a-key']},
                       {'kind': 'set_text', 'element': 'e1', 'text': 'x'*4001},
                       {'kind': 'focus', 'element': 'e1', 'code': 'bad'}]:
            with self.subTest(action=str(action)[:80]), self.assertRaises(ValueError):
                self.d.validate_actions([action])

    def test_portal_start_uses_exported_parent_handle(self):
        p = Portal.__new__(Portal)
        p.state, p.session_id, p.streams = 'closed', '', []
        p.pipeline, p.fd, p.error, p.pending = None, None, None, None
        p.last_activity, p.generation, p.closed_match, p.session = time.monotonic(), 0, None, None
        p.parent_window, p.parent_surface = None, None
        p.parent_handle, p.parent_raw_handle = '', ''
        from portal_restore import PortalPersistence
        p.persistence = PortalPersistence(1)
        p.bus, p.dbus = Mock(), Mock()
        p.dbus.String.side_effect = lambda value: value
        p.dbus.ObjectPath.side_effect = lambda value: value
        p.dbus.UInt32.side_effect = lambda value: value
        p.dbus.Boolean.side_effect = lambda value: value
        p.close = Mock(return_value={})
        calls = []

        def prepare(callback):
            p.parent_handle = 'wayland:test-parent'
            callback()

        def request(interface, method, args, options, callback):
            calls.append((method, args))
            if method == 'CreateSession':
                callback({'session_handle': '/session'})
            elif method in ('SelectDevices', 'SelectSources'):
                callback({})
            elif method == 'Start':
                callback({'devices': 3, 'streams': [(7, {'logical_size': [100, 100]})]})

        p._prepare_parent_window = prepare
        p._request = request
        result = p.start()
        start_args = next(args for method, args in calls if method == 'Start')
        self.assertEqual(start_args[1], 'wayland:test-parent')
        self.assertEqual(result['state'], 'active')

    def test_portal_revocation_and_session_identity(self):
        p = Portal.__new__(Portal)
        p.state, p.session_id = 'closed', 'a'
        with self.assertRaises(ValueError):
            p.require('a')
        p.state = 'active'
        with self.assertRaises(ValueError):
            p.require('b')

    @patch('desktop_worker.pump')
    def test_type_text_falls_back_to_portal_for_invalid_semantic_caret_without_screenshot(self, _):
        self.node.focused = True
        self.d.entries['e1'] = (self.node, self.d.signature(self.d.describe(self.node)), self.window, 'test')
        with patch('desktop_worker.Atspi.Text.get_caret_offset', return_value=-1):
            result = self.d.act('now', [{'kind': 'type_text', 'text': 'Olá €'}],
                                wait_ms=0, session_id='session')
        self.assertTrue(result['ok'])
        self.assertEqual(result['results'][0]['backend'], 'portal keyboard after semantic focus')
        self.d.portal.require.assert_called_with('session')
        self.d.portal.perform.assert_called_once()

    def test_keys_unicode_and_modifiers(self):
        self.assertEqual(Portal.keysyms(['CTRL', 's']), [0xffe3, 115])
        self.assertEqual(Portal.keysyms(['€']), [0x01000000 | ord('€')])
        self.assertEqual(Portal.keysyms(['ã']), [0x01000000 | ord('ã')])
        self.d.validate_actions([{'kind': 'type_text', 'text': 'Olá €'}])


if __name__ == '__main__':
    unittest.main()


class CompositorWindowTests(unittest.TestCase):
    def desktop(self):
        desktop = Desktop(None)
        desktop.gnome_windows = Mock()
        desktop.gnome_windows.status.return_value = {'available': True}
        self.target = {'id': 'epoch:7', 'pid': 123, 'title': 'Game', 'active': False, 'minimized': True}
        return desktop

    def test_focus_uses_compositor_readback_without_atspi(self):
        desktop = self.desktop()
        active = dict(self.target, active=True, minimized=False)
        desktop.gnome_windows.windows.side_effect = [[self.target], [active]]
        with patch('desktop_worker.pump'):
            result = desktop.window_control(123, 'epoch:7', 'Game', True)
        self.assertTrue(result['verified'])
        self.assertFalse(result['input_sent'])
        desktop.gnome_windows.focus.assert_called_once_with(self.target)

    def test_wrong_identity_never_focuses(self):
        desktop = self.desktop()
        desktop.gnome_windows.windows.return_value = [self.target]
        with self.assertRaises(ValueError):
            desktop.window_control(123, 'stale', 'Game', True)
        desktop.gnome_windows.focus.assert_not_called()

    def test_accepted_without_active_readback_is_not_verified(self):
        desktop = self.desktop()
        desktop.gnome_windows.windows.return_value = [self.target]
        with patch('desktop_worker.pump'):
            result = desktop.window_control(123, 'epoch:7', 'Game', True)
        self.assertFalse(result['verified'])
