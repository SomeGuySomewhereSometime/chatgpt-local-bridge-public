"""Run the installed Godot MCP once, adding health routes, never domain tools."""
import asyncio
import fcntl
import os
from pathlib import Path
import socket

PROJECT = "/home/bridge-user/Transferências/Keyboard Warriors/warriors"

async def readiness(bridge):
    state = {"mcp": True, "bridge_connected": bool(bridge.connected), "application": False,
             "ready": False, "expected_project": PROJECT}
    if not bridge.connected:
        state["reason"] = "editor_disconnected"
        return state
    try:
        response = await asyncio.wait_for(bridge.send("cmd_get_project_info", timeout=2.0), 3.0)
        if not response.ok or not response.result:
            state["reason"] = "editor_probe_failed"
            return state
        data = response.result
        state.update(application=True, project_path=data.get("project_path"),
                     project_name=data.get("name"), godot_version=data.get("godot_version"))
        state["project_matches"] = str(data.get("project_path", "")).rstrip("/") == PROJECT
        state["ready"] = state["project_matches"]
        if not state["ready"]:
            state["reason"] = "different_project_no_automatic_switch"
    except Exception as exc:
        state["reason"] = type(exc).__name__
    return state


def main():
    from mcp_server.bridge import Bridge
    from mcp_server.config import ServerConfig
    from mcp_server.logging_setup import configure_logging
    from mcp_server.server import create_server
    from starlette.responses import JSONResponse
    # The lock covers this managed process. Port checks also refuse a foreign MCP;
    # upstream otherwise logs a bind failure and starts a disconnected second MCP.
    lock = open(Path(os.environ["XDG_RUNTIME_DIR"]) / "godot-mcp.lock", "a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    for port in (9080, 9090):
        with socket.socket() as check:
            check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            check.bind(("127.0.0.1", port))
    config = ServerConfig.from_env()
    config.validate_http_auth()
    configure_logging(config.log_level)
    bridge = Bridge(config.bridge)
    server = create_server(config, bridge=bridge)

    @server.custom_route("/live", methods=["GET"])
    async def live(request):
        return JSONResponse({"live": True, "mcp_process_present": True})

    @server.custom_route("/ready", methods=["GET"])
    async def ready(request):
        data = await readiness(bridge)
        return JSONResponse(data, status_code=200 if data["ready"] else 503)

    server.run(transport="http", host=config.host, port=config.port, show_banner=False)

if __name__ == "__main__":
    main()
