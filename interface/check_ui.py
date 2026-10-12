"""Render the native UI and verify navigation without host mutations."""
import argparse
from pathlib import Path
import sys

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Graphene', '1.0')
from gi.repository import Gdk, GLib, Graphene, Gtk
sys.path.insert(0, str(Path(__file__).parent))
from app import Application


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='/tmp/bridge-example')
    args = parser.parse_args()
    output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    application = Application(demo=True)
    outcome = []

    keys = ['overview', 'integrations', 'projects', 'operations', 'reservations', 'connection', 'continuity']
    current = [0]

    def capture():
        try:
            window = application.get_active_window()
            assert window is not None
            assert not window.start_button.get_sensitive()
            assert not window.stop_button.get_sensitive()
            assert not window.restart_button.get_sensitive()
            width, height = window.get_width(), window.get_height()
            assert width >= 860 and height >= 650
            paintable = Gtk.WidgetPaintable.new(window)
            snapshot = Gtk.Snapshot.new()
            paintable.snapshot(snapshot, width, height)
            node = snapshot.to_node()
            assert node is not None
            renderer = window.get_renderer()
            texture = renderer.render_texture(node, Graphene.Rect().init(0, 0, width, height))
            assert texture.save_to_png(str(output / (keys[current[0]] + '.png')))
            current[0] += 1
            if current[0] < len(keys):
                window.navigation.select_row(window.navigation.get_row_at_index(current[0]))
                assert window.stack.get_visible_child_name() == keys[current[0]]
                GLib.timeout_add(200, capture)
                return False
            outcome.append(True)
            print(f'PASS: native GTK window {width}x{height}, seven pages rendered, absent-service controls disabled, preview without mutations')
        except BaseException as exc:
            outcome.append(False)
            print(type(exc).__name__ + ': ' + str(exc), file=sys.stderr)
        application.quit()
        return False

    application.connect('activate', lambda *_: GLib.timeout_add(900, capture))
    application.run([])
    return 0 if outcome == [True] else 1


if __name__ == '__main__':
    raise SystemExit(main())
