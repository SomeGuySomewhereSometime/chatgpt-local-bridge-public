"""Conservative LibreSprite lifecycle; preserve every open document."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request

APP = "/home/bridge-user/Applications/LibreSprite-1.1/LibreSprite-x86_64.AppImage"
MCP_UNIT = "libresprite-mcp.service"
APP_UNIT = "libresprite-app.service"
TUNNEL_UNIT = "mcp-tunnel-chatgpt-libresprite.service"
PROFILE = Path("/home/bridge-user/.config/tunnel-client/chatgpt-libresprite.yaml")


def fetch(path):
    try:
        with urllib.request.urlopen("http://127.0.0.1:9091/" + path, timeout=6) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            return json.load(exc)
        except (ValueError, OSError):
            return {"ready": False}
    except (OSError, ValueError):
        return {"ready": False}


def editor_pids():
    found = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            executable = os.readlink(proc / "exe")
            if Path(executable).name.lower().startswith("libresprite"):
                found.append(int(proc.name))
        except OSError:
            pass
    return found


def systemctl(action, unit):
    subprocess.run(["systemctl", "--user", action, unit], check=True,
                   capture_output=True, timeout=25)


def run_app():
    lock = open(Path(os.environ["XDG_RUNTIME_DIR"]) / "libresprite-editor-app.lock", "a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if editor_pids():
        return
    # Launch AppImage only when no LibreSprite exists; no document is selected.
    child = subprocess.Popen([APP])
    raise SystemExit(child.wait())


def recover(open_application=False):
    actions = []
    # Do not replace an unmanaged server. The managed entry also refuses occupied ports.
    if not fetch("live").get("live"):
        systemctl("start", MCP_UNIT)
        actions.append("start_mcp")
    if open_application and not editor_pids():
        systemctl("start", APP_UNIT)
        actions.append("start_editor")
    state = fetch("ready")
    if PROFILE.is_file():
        systemctl("start", TUNNEL_UNIT)
        actions.append("ensure_tunnel")
    state.update(actions=actions, application_pids=editor_pids(), tunnel_configured=PROFILE.is_file())
    if not state.get("ready"):
        state["recovery_note"] = "Application preserved. Open mcp.js and click Connect if needed; do not replay pending scripts."
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["recover", "run-app"])
    parser.add_argument("--open-app", action="store_true")
    args = parser.parse_args()
    if args.action == "run-app":
        run_app()
    else:
        try:
            print(json.dumps(recover(args.open_app), ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({"ready": False, "error": type(exc).__name__}))
            raise SystemExit(1)

if __name__ == "__main__":
    main()
