#!/usr/bin/python3
"""Launch fixed MCP programs without inheriting transport credentials."""
import os
from pathlib import Path
import sys

ROOT = Path.home()
BRIDGE_ROOT = Path(__file__).resolve().parent.parent
env = {"HOME": str(ROOT), "USER": os.environ.get("USER", ROOT.name), "LOGNAME": os.environ.get("LOGNAME", ROOT.name),
       "PATH": "/usr/local/bin:/usr/bin:/bin:/snap/bin", "LANG": "C.UTF-8",
       "PYTHONDONTWRITEBYTECODE": "1"}
for key in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR",
            "DBUS_SESSION_BUS_ADDRESS", "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "SSH_AUTH_SOCK",
            "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "JAVA_HOME", "DOTNET_ROOT"):
    if key in os.environ:
        env[key] = os.environ[key]
mode = sys.argv[1] if len(sys.argv) == 2 else ""
if mode in {"bridge", "core", "desktop"}:
    env["BRIDGE_PROFILE"] = "desktop" if mode == "desktop" else "core"
    env["BRIDGE_WORKSPACE"] = os.environ.get("BRIDGE_WORKSPACE", str(ROOT))
    for key in ("BRIDGE_CONFIG", "BRIDGE_OPERATIONS_DIR", "BRIDGE_MEMORY_DISABLED"):
        if key in os.environ:
            env[key] = os.environ[key]
    command = [str(BRIDGE_ROOT / ".venv/bin/python"), "-B",
               str(BRIDGE_ROOT / "mcp_server.py")]
elif mode == "blender":
    env.update({"BLENDER_MCP_DISABLE_TELEMETRY": "1", "BLENDER_HOST": "127.0.0.1", "BLENDER_PORT": "9876"})
    command = [str(ROOT / ".local/share/uv/tools/blender-mcp/bin/python"), "-B",
               str(ROOT / ".local/lib/mcp-integration/blender_entry.py")]
elif mode in {"tunnel-bridge", "tunnel-unity", "tunnel-blender"}:
    profile = {"tunnel-bridge": "chatgpt-local-bridge-NOBUBBLEWARP", "tunnel-unity": "chatgpt-unity",
               "tunnel-blender": "chatgpt-blender"}[mode]
    command = [str(ROOT / ".local/bin/tunnel-client"), "run", "--profile-dir",
               str(ROOT / ".config/tunnel-client"), "--profile", profile]
else:
    raise SystemExit("Usage: launch.py bridge|blender")
os.chdir(BRIDGE_ROOT if mode in {"bridge", "core", "desktop"} else ROOT)
if mode.startswith("tunnel-"):
    from supervise import supervise
    raise SystemExit(supervise(command, env, mode))
os.execve(command[0], command, env)
