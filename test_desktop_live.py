"""Opt-in host test. Reads and acts only on the disposable bridge-desktop-test app."""
import json
import os
from pathlib import Path
import subprocess
import time
import unittest


@unittest.skipUnless(os.environ.get('BRIDGE_DESKTOP_TESTS') == '1', 'requires the local GTK desktop fixture')
class DesktopLiveTests(unittest.TestCase):
    def test_own_gtk_fixture(self):
        from desktop import DesktopClient
        client = DesktopClient()
        p = subprocess.Popen(['/usr/bin/python3', '-B', str(Path(__file__).with_name('desktop_test_window.py'))],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(1)
            r = client.call('observe', application='bridge-desktop-test', max_elements=100)
            self.assertEqual(len(r['windows']), 1)
            field = next(e for e in r['elements'] if e['name'] == 'Test name')
            a = client.call('act', snapshot_id=r['snapshot_id'],
                            actions=[{'kind': 'set_text', 'element': field['id'], 'text': 'Olá, Bridge!'}], wait_ms=100)
            self.assertTrue(a['ok'], a)
            self.assertTrue(a['results'][0]['verified'])
            r = a['observation']
            button = next(e for e in r['elements'] if e['name'] == 'Apply test')
            a = client.call('act', snapshot_id=r['snapshot_id'],
                            actions=[{'kind': 'activate', 'element': button['id']}], wait_ms=150)
            self.assertTrue(a['ok'], a)
            self.assertTrue(any('Applied: Olá, Bridge!' in str(e) for e in a['observation']['elements']))
            r = a['observation']
            checkbox = next(e for e in r['elements'] if e['name'] == 'Test checkbox')
            a = client.call('act', snapshot_id=r['snapshot_id'],
                            actions=[{'kind': 'activate', 'element': checkbox['id']}], wait_ms=100)
            self.assertTrue(a['ok'], a)
            checked = next(e for e in a['observation']['elements'] if e['name'] == 'Test checkbox')
            self.assertIn('checked', checked['states'])
            with self.assertRaisesRegex(RuntimeError, 'Stale snapshot'):
                client.call('act', snapshot_id=r['snapshot_id'], actions=[{'kind': 'activate', 'element': checkbox['id']}])
            print('LIVE_ATSPI_FIXTURE_PASS: Unicode readback, button result, checkbox state, stale-snapshot refusal')
        finally:
            client.close()
            p.terminate()
            p.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
