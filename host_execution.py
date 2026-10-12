"""Host commands owned by transient user units; no filesystem/IPC sandbox."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import uuid


def process_identity(pid: int) -> str | None:
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        return None


class HostProcess:
    """Popen-compatible output/wait plus signals for this exact owned unit."""
    def __init__(self, process, unit, environment, temporary):
        self._process, self.unit = process, unit
        self.environment, self.temporary = environment, temporary
        self.stdin, self.stdout, self.stderr = process.stdin, process.stdout, process.stderr
        self.pid, self.args = process.pid, process.args

    @property
    def returncode(self):
        return self._process.returncode

    def poll(self):
        return self._process.poll()

    def wait(self, timeout=None):
        return self._process.wait(timeout=timeout)

    def signal_owned(self, sig):
        # Never signal a PID discovered from arbitrary host state.
        subprocess.run(['/usr/bin/systemctl', '--user', 'kill', '--kill-whom=all',
                        '--signal=' + signal.Signals(sig).name, self.unit],
                       env=self.environment, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)

    def cleanup(self):
        self.temporary.cleanup()


def launch_host(argv: list[str], cwd: Path, environment: dict[str, str], *, input_pipe=False) -> HostProcess:
    """Start a command with a readiness handshake and owner-death cleanup."""
    if not all(Path(p).is_file() for p in ('/usr/bin/systemd-run', '/usr/bin/systemctl')):
        raise OSError('Host execution requires the user systemd manager')
    environment = dict(environment)
    runtime = Path(f'/run/user/{os.getuid()}')
    if runtime.is_dir():
        environment.setdefault('XDG_RUNTIME_DIR', str(runtime))
        environment.setdefault('DBUS_SESSION_BUS_ADDRESS', f'unix:path={runtime}/bus')
    owner = process_identity(os.getpid())
    if owner is None:
        raise OSError('Cannot identify the command owner')
    temporary = tempfile.TemporaryDirectory(prefix='bridge-nobubblewarp-job-')
    ready = Path(temporary.name) / 'ready'
    unit = 'bridge-nobubblewarp-job-' + uuid.uuid4().hex + '.service'
    worker = Path(__file__).with_name('host_job_worker.py')
    command = ['/usr/bin/systemd-run', '--user', '--quiet', '--wait', '--pipe', '--collect',
               '--no-ask-password', '--unit=' + unit, '--service-type=exec',
               '--property=KillMode=control-group', '--property=TimeoutStopSec=2',
               '--description=NoBubblewarp owned command', '--working-directory=' + str(cwd),
               '--expand-environment=no', '--', '/usr/bin/env', '-i', '--',
               *[f'{k}={v}' for k, v in sorted(environment.items())],
               sys.executable, '-B', str(worker), str(os.getpid()), owner, str(ready), *argv]
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE if input_pipe else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=environment, start_new_session=True)
    except BaseException:
        temporary.cleanup()
        raise
    owned = HostProcess(process, unit, environment, temporary)
    deadline = time.monotonic() + 10
    while not ready.exists() and process.poll() is None:
        if time.monotonic() >= deadline:
            subprocess.run(['/usr/bin/systemctl', '--user', 'stop', unit], env=environment,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5)
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
            process.stdout.close(); process.stderr.close()
            if process.stdin is not None:
                process.stdin.close()
            temporary.cleanup()
            raise OSError('Host command startup did not complete; inspect its receipt before retrying')
        time.sleep(.01)
    return owned
