"""Optional compositor window focus. AT-SPI remains the action/readback authority."""
import json
from gi.repository import Gio, GLib


class GnomeWindows:
    def _call(self, method, signature=None, arguments=()):
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync('org.gnome.Shell', '/org/localdevbridge/Windows',
                              'org.localdevbridge.Windows', method,
                              GLib.Variant(signature, arguments) if signature else None,
                              None, Gio.DBusCallFlags.NO_AUTO_START, 750, None)
        return reply.unpack()[0]

    def status(self):
        try:
            return {'available': self._call('Ping') == 'local-dev-bridge-windows/1',
                    'backend': 'GNOME Shell window focus'}
        except Exception:
            return {'available': False, 'backend': 'GNOME Shell window focus',
                    'hint': 'Enable local-dev-bridge-windows@local; first installation may need logout/login.'}

    def windows(self, pid):
        try:
            return json.loads(self._call('ListWindows', '(u)', (pid,)))
        except Exception:
            return []

    def focus(self, target):
        return self._call('Focus', '(sus)', (target['id'], target['pid'], target['title']))


def match_window(windows, pid, title):
    """Do not guess among identically titled windows in one process."""
    candidates = [w for w in windows if w.get('pid') == pid and w.get('title') == title]
    return candidates[0] if len(candidates) == 1 else None
