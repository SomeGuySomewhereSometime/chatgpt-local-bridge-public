"""File-tool policy and explicit host execution for the NOBUBBLEWARP variant."""
from __future__ import annotations

import os
import pwd
import subprocess
import threading
from pathlib import Path

from fastapi import HTTPException


def policy_paths(config: dict, section: str) -> list[Path]:
    values = config.get("filesystem", {}).get(section, [])
    if not isinstance(values, list) or any(not isinstance(x, str) or not Path(x).is_absolute() for x in values):
        raise RuntimeError(f"filesystem.{section} must contain absolute paths")
    return sorted({Path(x).resolve() for x in values}, key=lambda x: (len(x.parts), str(x)))


def validate_filesystem_policy(config: dict) -> None:
    filesystem = config.get("filesystem")
    if not isinstance(filesystem, dict):
        raise RuntimeError("Required filesystem policy is missing or invalid")
    for section in ("denied_paths", "read_only_paths"):
        if section not in filesystem or not policy_paths(config, section):
            raise RuntimeError(f"Required filesystem.{section} must be a nonempty list")


def checked_path(workspace: Path, config: dict, value: str, *, write: bool = False,
                 follow_leaf: bool = True, tree: bool = False) -> Path:
    raw = workspace / value
    candidate = raw.resolve() if follow_leaf or raw.name in {"", ".", ".."} else raw.parent.resolve() / raw.name
    if not candidate.is_relative_to(workspace):
        raise HTTPException(403, "DENIED: path outside workspace")
    denied = policy_paths(config, "denied_paths")
    protected = denied + (policy_paths(config, "read_only_paths") if write else [])
    for root in protected:
        if candidate.is_relative_to(root) or (tree and root.is_relative_to(candidate)):
            raise HTTPException(403, "DENIED: protected filesystem path")
    if write and candidate == workspace:
        raise HTTPException(403, "DENIED: cannot modify workspace root")
    return candidate


def minimal_environment(*, desktop: bool = False) -> dict[str, str]:
    account = pwd.getpwuid(os.getuid())
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin:/snap/bin",
        "HOME": account.pw_dir,
        "USER": account.pw_name,
        "LOGNAME": account.pw_name,
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if desktop:
        for key in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR",
                    "DBUS_SESSION_BUS_ADDRESS", "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP"):
            if key in os.environ:
                env[key] = os.environ[key]
    return env



def host_environment(*, read_only: bool = False) -> dict[str, str]:
    """Forward session references and tool paths, never the transport's secrets."""
    env = minimal_environment(desktop=not read_only)
    home = Path(env['HOME'])
    env['PATH'] = ':'.join(str(x) for x in (home / '.local/bin', home / '.hermes/node/bin',
                                           home / '.cargo/bin')) + ':' + env['PATH']
    if not read_only:
        for key in ('SSH_AUTH_SOCK', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME',
                    'JAVA_HOME', 'DOTNET_ROOT'):
            if key in os.environ:
                env[key] = os.environ[key]
    else:
        # Fixed read tools must not load host credential helpers/global hooks.
        env.update(GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_SYSTEM='/dev/null',
                   GIT_TERMINAL_PROMPT='0')
    return env


def run_host(workspace: Path, cwd: Path, config: dict, argv: list[str], *,
             timeout: int = 60, input_text: str | None = None, read_only: bool = False,
             environment: dict | None = None) -> subprocess.CompletedProcess:
    """Execute on the host. read_only selects a clean environment, not a sandbox."""
    from host_execution import launch_host
    from command_sessions import Job
    process = launch_host(argv, cwd, environment if environment is not None else host_environment(read_only=read_only),
                          input_pipe=input_text is not None)
    job = Job(process, max(1, min(timeout, 300)), cwd)
    if input_text is not None:
        def feed():
            try:
                process.stdin.write(input_text.encode())
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            finally:
                process.stdin.close()
        threading.Thread(target=feed, daemon=True).start()
    job.done.wait()
    result = job.read(limit=262144)
    if job.timed_out:
        raise HTTPException(408, {'error': 'Operation exceeded its execution deadline',
                                 'stdout': result['stdout'], 'stderr': result['stderr'],
                                 'cleanup_warning': result['cleanup_warning']})
    if result['cleanup_warning']:
        raise HTTPException(503, {'error': result['cleanup_warning'],
                                  'stdout': result['stdout'], 'stderr': result['stderr']})
    out, err = result['stdout'], result['stderr']
    if result['output_truncated']:
        out = '[earlier output truncated]\n' + out
    return subprocess.CompletedProcess(argv, process.returncode, out, err)
