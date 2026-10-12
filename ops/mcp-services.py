#!/home/bridge-user/.local/share/uv/tools/blender-mcp/bin/python
"""Bounded local service operations and read-only readiness checks."""
import argparse
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import urllib.request

PROFILES = {"bridge": ("chatgpt-local-bridge-NOBUBBLEWARP", 8090),
            "unity": ("chatgpt-unity", 8081), "blender": ("chatgpt-blender", 8082),
            "browser": ("chatgpt-browser", 8083), "godot": ("chatgpt-godot", 8084), "libresprite": ("chatgpt-libresprite", 8085)}
PROJECT = Path("/home/bridge-user/projects/unity-project")


def probe(url):
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return {"ok": response.status == 200, "status": response.status}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}


@asynccontextmanager
async def http_streams(url, timeout):
    """Use the installed SDK's transport without assuming v1 names or tuples."""
    from mcp.client import streamable_http as transport
    modern = getattr(transport, 'streamable_http_client', None)
    if modern is not None:
        http = getattr(transport, 'httpx2', None) or getattr(transport, 'httpx', None)
        if http is None:
            raise RuntimeError('MCP HTTP client implementation unavailable')
        async with http.AsyncClient(timeout=http.Timeout(timeout), trust_env=False) as client:
            async with modern(url, http_client=client) as streams:
                yield streams[:2]
    else:
        async with transport.streamablehttp_client(url, timeout=timeout) as streams:
            yield streams[:2]


def sdk_field(value, modern, legacy):
    if hasattr(value, modern):
        return getattr(value, modern)
    return getattr(value, legacy)


async def unity_probe():
    from mcp import ClientSession
    cfg = json.loads((PROJECT / "UserSettings/AI-Game-Developer-Config.json").read_text())
    # Only derive routing from non-secret fields. Never return the full config.
    pin = hashlib.sha256(str(PROJECT).rstrip("/\\").lower().encode()).hexdigest()[:8]
    url = cfg["host"].rstrip("/") + "/p/" + pin
    async with http_streams(url, timeout=5) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            if "editor-application-get-state" not in names:
                return {"mcp": True, "application": False, "reason": "editor state tool disabled", "tools": len(names)}
            state = await session.call_tool("editor-application-get-state", {})
            scenes = await session.call_tool("scene-list-opened", {})
            return {"mcp": True, "application": not sdk_field(state, 'is_error', 'isError') and not sdk_field(scenes, 'is_error', 'isError'),
                    "server_version": sdk_field(init, 'server_info', 'serverInfo').version, "tools": len(names), "endpoint": url,
                    "editor_state": sdk_field(state, 'structured_content', 'structuredContent'),
                    "scenes": sdk_field(scenes, 'structured_content', 'structuredContent')}


def blender_probe():
    with socket.create_connection(("127.0.0.1", 9876), timeout=3) as sock:
        sock.settimeout(4)
        sock.sendall(json.dumps({"type": "get_addon_info", "params": {}}).encode())
        data = b""
        while len(data) < 65536:
            chunk = sock.recv(8192)
            if not chunk:
                raise RuntimeError("addon disconnected")
            data += chunk
            try:
                reply = json.loads(data)
                result = reply.get("result", {})
                return {"application": reply.get("status") == "success",
                        "blender_version": result.get("blender_version"),
                        "protocol": result.get("protocol_version"),
                        "addon_version": result.get("addon_version")}
            except json.JSONDecodeError:
                continue
        raise RuntimeError("addon response too large")


async def browser_probe():
    from mcp import ClientSession
    # Listing tools does not instantiate Playwright's browser backend. Calling
    # browser_tabs here could launch Chrome or show an extension approval dialog.
    async with http_streams('http://127.0.0.1:8931/mcp', timeout=4) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            catalogue = await session.list_tools()
            names = {t.name for t in catalogue.tools}
            return {'mcp': {'browser_tabs', 'browser_fill_form', 'browser_snapshot'} <= names,
                    'tools': len(names),
                    'chrome_extension': {'connected': None, 'state': 'not_probed',
                                         'reason': 'Use a browser action for live verification; health checks do not launch Chrome.'}}


def status(selected):
    result = {}
    for name in selected:
        profile, port = PROFILES[name]
        unit = "mcp-tunnel-" + profile + ".service"
        service = subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True, text=True, timeout=5)
        item = {"service": service.stdout.strip(), "tunnel_live": probe(f"http://127.0.0.1:{port}/healthz"),
                "tunnel_ready": probe(f"http://127.0.0.1:{port}/readyz")}
        if name == 'bridge':
            from bridge_watchdog import read_status, store_from_config, poll_health
            item['connection'] = dict(read_status(store_from_config()), **poll_health())
            item['connection']['process_alive'] = service.returncode == 0
        if name == 'godot':
            from godot_control import fetch, editor_pids
            live, readiness = fetch('live'), fetch('ready')
            item['tunnel_transport_ready'] = item.pop('tunnel_ready')
            item.update(readiness)
            item['mcp_process_present'] = live.get('live', False)
            item['editor_pids'] = editor_pids()
            item['integration_ready'] = bool(readiness.get('ready'))
            item['tunnel_ready'] = {'ok': bool(readiness.get('ready') and item['tunnel_transport_ready']['ok'])}
            item['chatgpt_available'] = None
            item['chatgpt_verification'] = 'Requires a successful call from the separate Godot connection in ChatGPT'
            result[name] = item
            continue
        if name == 'libresprite':
            from libresprite_control import fetch, editor_pids
            live, readiness = fetch('live'), fetch('ready')
            item['tunnel_transport_ready'] = item.pop('tunnel_ready')
            item.update(readiness)
            item['mcp_process_present'] = live.get('live', False)
            item['application_pids'] = editor_pids()
            item['application'] = bool(item['application_pids'])
            item['relay'] = probe('http://127.0.0.1:64823/ping')
            item['integration_ready'] = bool(readiness.get('ready'))
            item['tunnel_ready'] = {'ok': bool(readiness.get('ready') and item['tunnel_transport_ready']['ok'])}
            item['chatgpt_available'] = None
            item['chatgpt_verification'] = 'Requires a successful call from the separate LibreSprite connection in ChatGPT'
            result[name] = item
            continue
        if name == 'browser':
            browser_service = subprocess.run(
                ['systemctl', '--user', 'is-active', 'playwright-browser-mcp.service'],
                capture_output=True, text=True, timeout=5)
            item['browser_service'] = browser_service.stdout.strip()
        if name in {"bridge", "blender"}:
            from supervise import has_stdio_child
            marker = b'/chatgpt-local-bridge-NOBUBBLEWARP/mcp_server.py' if name == 'bridge' else b'/mcp-integration/blender_entry.py'
            backend = False
            for proc in Path('/proc').iterdir():
                if not proc.name.isdigit():
                    continue
                try:
                    argv = (proc / 'cmdline').read_bytes().split(b'\0')
                    if argv[0] and Path(os.fsdecode(argv[0])).name == 'tunnel-client' and profile.encode() in argv:
                        backend = has_stdio_child(int(proc.name), marker)
                        break
                except OSError:
                    continue
            item['mcp_process_present'] = backend
        try:
            if name == "unity":
                item.update(asyncio.run(asyncio.wait_for(unity_probe(), 15)))
            elif name == "blender":
                item.update(blender_probe())
            elif name == "browser":
                item.update(asyncio.run(asyncio.wait_for(browser_probe(), 6)))
            else:
                item["application"] = "filesystem backend; use system_info/get_session_context in ChatGPT for end-to-end verification"
        except Exception as exc:
            item.update({"application": False, "probe_error": type(exc).__name__})
        if name == "unity":
            try:
                profile_text = (Path("/home/bridge-user/.config/tunnel-client") / (profile + ".yaml")).read_text()
                pin = hashlib.sha256(str(PROJECT).lower().encode()).hexdigest()[:8]
                item["profile_pin_matches_project"] = "/p/" + pin in profile_text
            except OSError:
                item["profile_pin_matches_project"] = False
        result[name] = item
    return result


def readiness(name, item):
    """Separate local application/MCP readiness from hosted tunnel readiness."""
    if name in {'godot', 'libresprite'}:
        return bool(item.get('integration_ready'))
    if name == 'browser':
        return item.get('mcp') is True
    return item.get('application') is True


def prepare(component):
    if component not in PROFILES or component == 'bridge':
        raise ValueError('Select one application integration; the current bridge cannot prepare itself')
    before = status([component])[component]
    units = []
    backend = {'browser': 'playwright-browser-mcp.service', 'godot': 'godot-editor-mcp.service',
               'libresprite': 'libresprite-mcp.service'}.get(component)
    if backend:
        units.append(backend)
    units.append('mcp-tunnel-' + PROFILES[component][0] + '.service')
    started, unavailable = [], []
    for unit in units:
        active = subprocess.run(['systemctl', '--user', 'is-active', unit],
                                capture_output=True, text=True, timeout=5)
        if active.returncode == 0:
            continue
        loaded = subprocess.run(['systemctl', '--user', 'show', '--property=LoadState', '--value', unit],
                                capture_output=True, text=True, timeout=5)
        if loaded.returncode or loaded.stdout.strip() != 'loaded':
            unavailable.append(unit)
            continue
        result = subprocess.run(['systemctl', '--user', 'start', unit],
                                capture_output=True, text=True, timeout=25)
        if result.returncode:
            unavailable.append(unit)
        else:
            started.append(unit)
    after = status([component])[component]
    return {'ok': not unavailable, 'component': component, 'before': before, 'after': after,
            'services_started': started, 'unavailable_services': unavailable,
            'integration_ready': readiness(component, after),
            'application_launch_requested': False, 'chatgpt_available': None}


def main():
    logging.disable(logging.CRITICAL)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "prepare", "start", "stop", "restart"])
    parser.add_argument("component", nargs="?", choices=["all", *PROFILES], default="all")
    args = parser.parse_args()
    if args.action == 'prepare':
        if args.component in {'all', 'bridge'}:
            parser.error('prepare requires one application component')
        print(json.dumps(prepare(args.component), ensure_ascii=False))
        return
    selected = list(PROFILES) if args.component == "all" else [args.component]
    if args.action != "status":
        if 'godot' in selected and args.action in {'start', 'restart'}:
            subprocess.run(['systemctl', '--user', 'start', 'godot-editor-mcp.service'], check=True, timeout=25)
        if 'libresprite' in selected and args.action in {'start', 'restart'}:
            subprocess.run(['systemctl', '--user', 'start', 'libresprite-mcp.service'], check=True, timeout=25)
        units = ["mcp-tunnel-" + PROFILES[n][0] + ".service" for n in selected]
        subprocess.run(["systemctl", "--user", args.action, *units], check=True, timeout=45)
    print(json.dumps(status(selected), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
