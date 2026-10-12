"""Exercise actual native-panel callbacks against disposable project/state only."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gio, GLib
sys.path.insert(0, str(Path(__file__).parent))
from app import Application
from backend import registry_snapshot


def main():
    with tempfile.TemporaryDirectory(prefix='bridge-panel-actions-') as temporary:
        root = Path(temporary)
        for name in ('work/project', 'control', 'private'):
            (root / name).mkdir(parents=True)
        config = root / 'control/config.json'
        config.write_text(json.dumps({'filesystem': {'denied_paths': [str(root / 'private')],
            'read_only_paths': [str(root / 'control')]}}))
        os.environ.update(BRIDGE_WORKSPACE=str(root), BRIDGE_CONFIG=str(config),
                          BRIDGE_OPERATIONS_DIR=str(root / 'private/operations'), BRIDGE_MEMORY_DISABLED='1')
        application = Application()
        application.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
        phase = [0]
        success = []
        deadline = time.monotonic() + 90

        def check():
            try:
                if time.monotonic() > deadline:
                    raise AssertionError('Native panel callback deadline exceeded')
                window = application.get_active_window()
                if window is None or window.busy or window.snapshot_data is None:
                    return True
                assert not window.feedback.has_css_class('feedback-error'), window.feedback.get_text()
                if phase[0] == 0:
                    window.project.set_text(str(root / 'work/project'))
                    window.navigation.select_row(window.navigation.get_row_at_index(2))
                    window.project_action('status')
                elif phase[0] == 1:
                    assert str(root / 'work/project') in window.project_details.get_text()
                    assert not (root / 'private/operations').exists()
                    window.project_action('prepare')
                elif phase[0] == 2:
                    assert (root / 'private/operations/projects').is_dir()
                    window.lease_action('acquire')
                elif phase[0] == 3:
                    assert window.token and window.reserved_project
                    assert registry_snapshot(root / 'private/operations')['leases']
                    window.lease_action('renew')
                elif phase[0] == 4:
                    assert window.lease_expiry > time.time()
                    window.lease_action('release')
                elif phase[0] == 5:
                    assert not window.token and not window.reserved_project
                    assert not registry_snapshot(root / 'private/operations')['leases']
                    window.submit({'action': 'diagnose'})
                else:
                    assert 'Servidor MCP local verificado:' in window.local_test_text.get_text()
                    assert 'ferramentas' in window.local_test_text.get_text()
                    success.append(True)
                    print('PASS: native callbacks, read-only environment, cache preparation, acquire/renew/release and real MCP diagnosis; disposable state only')
                    application.quit()
                    return False
                phase[0] += 1
                return True
            except BaseException as exc:
                print(type(exc).__name__ + ': ' + str(exc), file=sys.stderr)
                application.quit()
                return False

        application.connect('activate', lambda *_: GLib.timeout_add(150, check))
        application.run([])
        return 0 if success == [True] else 1


if __name__ == '__main__':
    raise SystemExit(main())
