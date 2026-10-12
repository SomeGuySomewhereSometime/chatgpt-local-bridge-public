"""Input dispatch regressions using fake portal/window providers; no physical input."""
import copy
import time
import unittest
from unittest.mock import Mock, patch
try:
    from desktop_worker import Desktop
except ModuleNotFoundError as exc:
    raise unittest.SkipTest('Run worker tests with system Python for GI') from exc

class PointerWorkerTests(unittest.TestCase):
    def setUp(self):
        self.desktop = Desktop.__new__(Desktop)
        self.desktop.portal = Mock(size=[1000,800])
        self.desktop.gnome_windows = Mock()
        self.window = dict(id='one', pid=12, title='Fixture', active=True,
                           minimized=False, frame_bounds=[0,0,1000,800])
        self.desktop.gnome_windows.windows.return_value = [copy.deepcopy(self.window)]
        self.desktop.browser_pointer_session = dict(id='cal', session_id='sid',
            window=self.window, image=dict(logical_position=[0,0],logical_size=[1000,800]),
            started=time.monotonic(), last_position=[400,400])
        patcher = patch('desktop_worker.pump')
        patcher.start()
        self.addCleanup(patcher.stop)

    def click(self):
        return self.desktop.browser_pointer('click', 'sid', calibration_id='cal', position=[400,400])

    def test_single_click_consumes_binding(self):
        self.assertTrue(self.click()['executed'])
        with self.assertRaises(ValueError): self.click()
        self.desktop.portal.perform.assert_called_once()

    def test_lost_delivery_response_cannot_replay(self):
        self.desktop.portal.perform.side_effect = RuntimeError('response lost')
        with self.assertRaises(RuntimeError): self.click()
        with self.assertRaises(ValueError): self.click()
        self.desktop.portal.perform.assert_called_once()

    def test_window_mutations_send_zero_clicks(self):
        for field,value in [('id','other'),('title','other'),('active',False),
                            ('minimized',True),('frame_bounds',[10,0,1000,800])]:
            with self.subTest(field=field):
                state = copy.deepcopy(self.desktop.browser_pointer_session)
                current = copy.deepcopy(self.window)
                current[field] = value
                self.desktop.gnome_windows.windows.return_value = [current]
                with self.assertRaises(ValueError): self.click()
                self.desktop.portal.perform.assert_not_called()
                self.desktop.browser_pointer_session = state

    def test_expired_calibration_sends_zero_clicks(self):
        self.desktop.browser_pointer_session['started'] -= 31
        with self.assertRaises(ValueError): self.click()
        self.desktop.portal.perform.assert_not_called()

    def test_changed_monitor_sends_zero_clicks(self):
        self.desktop.portal.size = [800,600]
        with self.assertRaises(ValueError): self.click()
        self.desktop.portal.perform.assert_not_called()

    def test_unmeasured_destination_sends_zero_clicks(self):
        self.desktop.browser_pointer_session['last_position'] = [401,400]
        with self.assertRaises(ValueError): self.click()
        self.desktop.portal.perform.assert_not_called()
