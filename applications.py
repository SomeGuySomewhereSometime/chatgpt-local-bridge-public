"""Desktop applications owned by independent user services, never by the tunnel."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import time
import uuid

from fastapi import HTTPException


APP_ENV_KEYS = {
    "HOME", "USER", "LOGNAME", "PATH", "LANG", "PYTHONDONTWRITEBYTECODE",
    "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS", "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP",
    "XDG_DATA_HOME",
}


@dataclass
class Application:
    unit: str
    pid: int
    environment: dict[str, str]
    returncode: int | None = None

    def state(self) -> dict[str, str]:
        result = subprocess.run(
            ["/usr/bin/systemctl", "--user", "show", self.unit,
             "-p", "LoadState", "-p", "ActiveState", "-p", "MainPID",
             "-p", "ExecMainStatus"],
            env=self.environment, capture_output=True, text=True, timeout=5)
        fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if result.returncode and fields.get("LoadState") != "not-found":
            raise HTTPException(503, "Cannot query the application user service")
        return fields

    def poll(self) -> int | None:
        if self.returncode is None:
            fields = self.state()
            if fields.get("ActiveState") in {"active", "activating", "deactivating"}:
                return None
            self.returncode = int(fields.get("ExecMainStatus", "0"))
        return self.returncode

    def _signal(self, signal: str) -> None:
        # A graceful close sends TERM only. Escalation to KILL remains explicit.
        result = subprocess.run(
            ["/usr/bin/systemctl", "--user", "kill", "--kill-whom=all",
             "--signal=" + signal, self.unit],
            env=self.environment, capture_output=True, text=True, timeout=5)
        if result.returncode and self.poll() is None:
            raise HTTPException(503, "Cannot signal the application user service")

    def terminate(self) -> None:
        self._signal("SIGTERM")

    def kill(self) -> None:
        self._signal("SIGKILL")

    def wait(self, timeout: float) -> int:
        deadline = time.monotonic() + timeout
        while self.poll() is None:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.unit, timeout)
            time.sleep(0.05)
        return self.returncode


def launch_application(argv: list[str], cwd: Path, environment: dict[str, str]) -> Application:
    env = {k: v for k, v in environment.items() if k in APP_ENV_KEYS}
    unit = "mcp-app-" + uuid.uuid4().hex + ".service"
    command = [
        "/usr/bin/systemd-run", "--user", "--quiet", "--collect", "--no-ask-password",
        "--unit=" + unit, "--description=Local Dev Bridge application",
        "--service-type=exec", "--property=ExitType=cgroup", "--property=Restart=no",
        "--property=StandardOutput=null", "--property=StandardError=null",
        "--working-directory=" + str(cwd), "--expand-environment=no",
        # systemd's manager has its own environment; scrub it inside the new unit.
        "--", "/usr/bin/env", "-i", "--",
        *[f"{k}={v}" for k, v in sorted(env.items())], *argv,
    ]
    try:
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        # No fallback to Popen: that would reintroduce tunnel-owned applications.
        raise HTTPException(503, "Cannot start an independent application user service") from exc
    if result.returncode:
        raise HTTPException(503, "Application user service failed to start; inspect systemd status")
    app = Application(unit, 0, env)
    fields = app.state()
    app.pid = int(fields.get("MainPID", "0"))
    return app
