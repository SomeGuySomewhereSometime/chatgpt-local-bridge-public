"""Own a single tunnel; fail the unit when its required stdio child disappears."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def has_stdio_child(pid: int, marker: bytes) -> bool:
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            stat = (process / 'stat').read_text()
            ppid = int(stat[stat.rfind(')') + 2:].split()[1])
            if ppid == pid and marker in (process / 'cmdline').read_bytes():
                return True
        except (OSError, ValueError):
            continue
    return False


def supervise(command: list[str], env: dict, mode: str) -> int:
    markers = {'tunnel-bridge': b'/chatgpt-local-bridge-NOBUBBLEWARP/mcp_server.py',
               'tunnel-blender': b'/mcp-integration/blender_entry.py'}
    marker = markers.get(mode)
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    child = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL)
    started = time.monotonic()
    missing_since = None
    exit_code = 1
    watchdog = None
    if mode == 'tunnel-bridge':
        from bridge_watchdog import Watchdog, store_from_config, poll_health
        watchdog = Watchdog(store_from_config())
    next_probe = 0
    last_state = None
    try:
        while not stopping:
            if child.poll() is not None:
                print('Tunnel exited; requesting service restart.', file=sys.stderr, flush=True)
                break
            if watchdog and time.monotonic() >= next_probe:
                state, restart = watchdog.tick(poll_health(), child.poll() is None,
                                               has_stdio_child(child.pid, marker))
                next_probe = time.monotonic() + 10
                if state['recovery_state'] != last_state:
                    last_state = state['recovery_state']
                    print('Bridge connection: ' + last_state, file=sys.stderr, flush=True)
                if restart:
                    print('Stale Bridge connection; idle recovery gate acquired.', file=sys.stderr, flush=True)
                    break
            if marker and watchdog is None:
                if has_stdio_child(child.pid, marker):
                    missing_since = None
                elif missing_since is None:
                    missing_since = time.monotonic()
                elif time.monotonic() - started > 20 and time.monotonic() - missing_since > 4:
                    print('Required stdio MCP child disappeared; requesting service restart.', file=sys.stderr, flush=True)
                    break
            time.sleep(1)
        if stopping:
            exit_code = 0
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)
    # Recovery gate FD is intentionally held until process exit.
    return exit_code
