import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const XML = `<node><interface name="org.localdevbridge.Windows">
  <method name="Ping"><arg type="s" direction="out"/></method>
  <method name="ListWindows"><arg name="pid" type="u" direction="in"/>
    <arg type="s" direction="out"/></method>
  <method name="Focus"><arg name="id" type="s" direction="in"/>
    <arg name="pid" type="u" direction="in"/><arg name="title" type="s" direction="in"/>
    <arg type="b" direction="out"/></method>
</interface></node>`;

export default class BridgeWindows extends Extension {
    enable() {
        this._epoch = GLib.uuid_string_random();
        this._api = {
            Ping: () => 'local-dev-bridge-windows/1',
            ListWindows: pid => JSON.stringify(this._windows(pid).map(w => ({
                id: this._id(w), pid: w.get_pid(), title: w.get_title() ?? '',
                active: global.display.focus_window === w, minimized: w.minimized,
                frame_bounds: (() => {
                    const r = w.get_frame_rect();
                    return [r.x, r.y, r.width, r.height];
                })(),
            }))),
            Focus: (id, pid, title) => {
                const candidates = this._windows(pid).filter(w =>
                    this._id(w) === id && (w.get_title() ?? '') === title);
                if (candidates.length !== 1)
                    throw new Error('Window identity changed; observe again');
                const window = candidates[0];
                if (window.minimized)
                    window.unminimize();
                Main.activateWindow(window, global.get_current_time());
                // This is acceptance only. The Bridge must verify focus independently.
                return true;
            },
        };
        this._dbus = Gio.DBusExportedObject.wrapJSObject(XML, this._api);
        this._dbus.export(Gio.DBus.session, '/org/localdevbridge/Windows');
    }

    _id(window) {
        return `${this._epoch}:${window.get_stable_sequence()}`;
    }

    _windows(pid) {
        if (!Number.isInteger(pid) || pid <= 0)
            throw new Error('A positive process ID is required');
        if (Main.sessionMode.isLocked || Main.sessionMode.isGreeter)
            throw new Error('Desktop session is locked');
        return global.get_window_actors().map(actor => actor.meta_window)
            .filter(window => window && window.get_pid() === pid);
    }

    disable() {
        this._dbus?.unexport();
        this._dbus = null;
        this._api = null;
        this._epoch = null;
    }
}
