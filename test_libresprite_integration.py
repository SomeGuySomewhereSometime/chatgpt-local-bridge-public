"""LibreSprite readiness cannot be inferred from an open relay port."""
import importlib.util
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import Mock, patch

def load(name):
    spec=importlib.util.spec_from_file_location(name, Path(__file__).parent/'ops'/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
entry=load('libresprite_entry')
control=load('libresprite_control')

class Probe:
    def __init__(self, output=True):
        self.serial=threading.Lock();self.calls=0;self.release=threading.Event();self.output=output
    def run_script(self, script, ctx):
        self.calls+=1
        self.release.wait(1)
        marker=script.split('"')[1]
        return marker+'1.1-dev\n' if self.output else 'unrelated output'

class HealthTests(unittest.TestCase):
    def test_pending_probe_not_duplicated(self):
        proxy=Probe(); health=entry.Readiness(proxy)
        first=health.check(.01);second=health.check(.01)
        self.assertFalse(first['ready']);self.assertTrue(second['probe_pending'])
        self.assertEqual(proxy.calls,1)
        proxy.release.set();health.pending.wait(1)
        self.assertTrue(health.check(.01)['script_connected'])
        self.assertEqual(health.version,'1.1-dev')
    def test_wrong_response_not_ready(self):
        proxy=Probe(False);proxy.release.set();health=entry.Readiness(proxy)
        self.assertFalse(health.check(.1)['ready'])
    def test_recent_result_reused_without_new_script(self):
        proxy=Probe();proxy.release.set();health=entry.Readiness(proxy)
        self.assertTrue(health.check(.1)['ready']);health.check(.1)
        self.assertEqual(proxy.calls,1)
    def test_old_result_not_reported_ready(self):
        proxy=Probe();proxy.release.set();health=entry.Readiness(proxy)
        self.assertTrue(health.check(.1)['ready'])
        health.last_success=time.monotonic()-10;proxy.release.clear()
        self.assertFalse(health.check(.01)['ready']);proxy.release.set()
    def test_existing_application_is_not_restarted(self):
        with patch.object(control,'fetch',side_effect=[{'live':True},{'ready':False,'probe_pending':True}]), patch.object(control,'editor_pids',return_value=[1]), patch.object(control,'PROFILE',Mock(is_file=Mock(return_value=False))), patch.object(control,'systemctl') as ctl:
            state=control.recover(True);ctl.assert_not_called();self.assertTrue(state['probe_pending'])
    def test_closed_application_only_opens_on_request(self):
        for enabled in (False,True):
            with patch.object(control,'fetch',side_effect=[{'live':True},{'ready':False}]), patch.object(control,'editor_pids',return_value=[]), patch.object(control,'PROFILE',Mock(is_file=Mock(return_value=False))), patch.object(control,'systemctl') as ctl:
                control.recover(enabled);self.assertEqual(ctl.call_count,int(enabled))
