"""Private MCP facade retaining one Playwright session between frontend calls."""
import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import signal

import httpx2
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel import Server
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.routing import Mount

FRONT_PORT = 8931
BACK_PORT = 8932


class BackendState:
    session = None
    catalogue = None
    lock = None
    failed = False


def node_environment():
    names = ('HOME', 'USER', 'LOGNAME', 'PATH', 'LANG', 'DISPLAY', 'WAYLAND_DISPLAY',
             'XAUTHORITY', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS', 'XDG_SESSION_TYPE',
             'XDG_CURRENT_DESKTOP', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME',
             'PLAYWRIGHT_MCP_EXTENSION_TOKEN')
    return {key: os.environ[key] for key in names if key in os.environ}


@asynccontextmanager
async def run_backend(state):
    runtime = Path.home()/'.local/share/chatgpt-browser-mcp'
    node = (Path.home()/'.local/bin/node').resolve()
    cli = runtime/'node_modules/@playwright/mcp/cli.js'
    try:
        _, writer = await asyncio.open_connection('127.0.0.1', BACK_PORT)
    except OSError:
        pass
    else:
        writer.close(); await writer.wait_closed()
        raise RuntimeError('Browser backend port occupied; existing process preserved')
    command = [str(node), str(cli), '--extension', '--host', '127.0.0.1',
               '--allowed-hosts', f'localhost:{BACK_PORT},127.0.0.1:{BACK_PORT}',
               '--port', str(BACK_PORT), '--caps', 'vision,pdf,devtools',
               '--output-dir', str(runtime/'output')]
    child = await asyncio.create_subprocess_exec(*command, cwd=runtime, env=node_environment(),
                                                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    stopping = False
    monitor = None
    state.failed = False
    async def watch_child():
        await child.wait()
        if not stopping:
            state.failed = True
            os.kill(os.getpid(), signal.SIGTERM)
    try:
        for attempt in range(60):
            if child.returncode is not None:
                raise RuntimeError('Browser backend exited during startup')
            try:
                _, writer = await asyncio.open_connection('127.0.0.1', BACK_PORT)
                writer.close(); await writer.wait_closed()
                break
            except OSError:
                await asyncio.sleep(0.1)
        else:
            raise RuntimeError('Browser backend startup timed out')
        async with httpx2.AsyncClient(timeout=httpx2.Timeout(15, read=1800), trust_env=False) as client:
            async with streamable_http_client(f'http://127.0.0.1:{BACK_PORT}/mcp', http_client=client) as (read, write):
                async with ClientSession(read, write, client_info=types.Implementation(name='Browser MCP relay', version='1.0')) as session:
                    await asyncio.wait_for(session.initialize(), 15)
                    state.catalogue = await asyncio.wait_for(session.list_tools(), 15)
                    state.session = session
                    state.lock = asyncio.Lock()
                    monitor = asyncio.create_task(watch_child())
                    yield
    finally:
        stopping = True
        state.session = None
        if child.returncode is None:
            child.terminate()
            try:
                await asyncio.wait_for(child.wait(), 5)
            except TimeoutError:
                child.kill(); await child.wait()
        if monitor is not None:
            await monitor


def create_app(state, *, start_backend=False):
    async def list_tools(context, params):
        return state.catalogue

    async def call_tool(context, params):
        if params.name not in {tool.name for tool in state.catalogue.tools}:
            return types.CallToolResult(content=[types.TextContent(type='text', text='Unknown Browser tool')], is_error=True)
        # Keep the browser cursor between frontend sessions; never replay a call.
        async with state.lock:
            return await state.session.call_tool(params.name, params.arguments)

    server = Server('Playwright Browser MCP', version='0.0.83-local',
                    on_list_tools=list_tools, on_call_tool=call_tool)
    mcp_app = server.streamable_http_app(
        json_response=True, stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[f'localhost:{FRONT_PORT}', f'127.0.0.1:{FRONT_PORT}'],
            allowed_origins=[f'http://localhost:{FRONT_PORT}', f'http://127.0.0.1:{FRONT_PORT}']))

    @asynccontextmanager
    async def lifecycle(application):
        if start_backend:
            async with run_backend(state):
                async with mcp_app.router.lifespan_context(mcp_app):
                    yield
        else:
            async with mcp_app.router.lifespan_context(mcp_app):
                yield

    return Starlette(routes=[Mount('/', app=mcp_app)], lifespan=lifecycle,
                     middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=['localhost','127.0.0.1'])])


state = BackendState()
app = create_app(state, start_backend=True)


if __name__ == '__main__':
    import logging
    import uvicorn
    logging.getLogger('httpx2').setLevel(logging.WARNING)
    uvicorn.run(app, host='127.0.0.1', port=FRONT_PORT, access_log=False, log_level='warning')
    if state.failed:
        raise SystemExit(1)
