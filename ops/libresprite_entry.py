"""Transport/lifecycle adapter around installed LibreSprite MCP 0.1.3."""
import asyncio
import fcntl
import importlib.metadata
import os
from pathlib import Path
import socket
import threading
import time
import uuid

class QuietContext:
    # Upstream invokes async Context logging synchronously. No logs are needed
    # for transport health; tool console output still comes back unchanged.
    def info(self, *args): pass
    def warning(self, *args): pass
    def error(self, *args): pass

class Readiness:
    def __init__(self, proxy):
        self.proxy = proxy
        self.guard = threading.Lock()
        self.pending = None
        self.last_success = 0
        self.version = None

    def _probe(self, done):
        marker = "BRIDGE_HEALTH_" + uuid.uuid4().hex + "="
        try:
            output = self.proxy.run_script('console.log("' + marker + '" + app.version)', QuietContext())
            if isinstance(output, str) and output.strip().startswith(marker):
                self.version = output.strip()[len(marker):]
                self.last_success = time.monotonic()
        except Exception:
            self.last_success = 0
        finally:
            done.set()

    def check(self, wait_seconds=4):
        with self.guard:
            if time.monotonic() - self.last_success > 5:
                if self.pending is None or self.pending.is_set():
                    self.pending = threading.Event()
                    threading.Thread(target=self._probe, args=(self.pending,), daemon=True).start()
                pending = self.pending
            else:
                pending = None
        if pending:
            pending.wait(wait_seconds)
        ready = self.last_success > 0 and time.monotonic() - self.last_success <= 5
        return {"mcp": True, "ready": ready, "script_connected": ready,
                "application_version": self.version if ready else None,
                "probe_pending": bool(self.pending and not self.pending.is_set()),
                "script_busy": self.proxy.serial.locked(),
                "reason": None if ready else "No recent script response; preserve pending work and connect mcp.js"}


def main():
    if importlib.metadata.version("mcp") != "1.30.0":
        raise SystemExit("LibreSprite MCP requires the pinned mcp==1.30.0 (mcp<2).")
    from libresprite_mcp.libresprite_proxy import LibrespriteProxy
    from libresprite_mcp.mcp_server import MCPServer
    from starlette.responses import JSONResponse
    lock = open(Path(os.environ["XDG_RUNTIME_DIR"]) / "libresprite-mcp.lock", "a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    for port in (64823, 9091):
        with socket.socket() as check:
            check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            check.bind(("127.0.0.1", port))

    class SerializedProxy(LibrespriteProxy):
        def __init__(self):
            super().__init__(64823)
            self.serial = threading.Lock()
        def run_script(self, script, ctx):
            if not self.serial.acquire(blocking=False):
                raise RuntimeError("A script is pending; do not replay it or restart the app")
            try:
                return super().run_script(script, QuietContext())
            finally:
                self.serial.release()

    proxy = SerializedProxy()
    proxy.start()
    server = MCPServer(proxy)
    server.mcp.settings.host = "127.0.0.1"
    server.mcp.settings.port = 9091
    health = Readiness(proxy)
    # Preserve the upstream tool/schema/resources. Only adapt blocking execution
    # to HTTP so health remains responsive while the original relay waits.
    tool = server.mcp._tool_manager.get_tool("run_script")
    original = tool.fn
    async def threaded_script(script, ctx):
        try:
            return await asyncio.wait_for(asyncio.to_thread(original, script, ctx), 25)
        except asyncio.TimeoutError:
            raise RuntimeError("Script outcome unknown; still pending. Do not retry or restart the app.") from None
    tool.fn = threaded_script
    tool.is_async = True

    @server.mcp.custom_route("/live", methods=["GET"])
    async def live(request):
        return JSONResponse({"live": True, "mcp_process_present": True,
                             "relay_thread_alive": proxy._server_thread.is_alive()})

    @server.mcp.custom_route("/ready", methods=["GET"])
    async def ready(request):
        state = await asyncio.to_thread(health.check)
        return JSONResponse(state, status_code=200 if state["ready"] else 503)
    server.run(transport="streamable-http")

if __name__ == "__main__":
    main()
