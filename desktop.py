"""MCP-facing desktop client. Fixed local helper, private pipes, no shell or socket."""
from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import selectors
import subprocess
import threading
import time

from security import minimal_environment
from diagnostics import redact


class DesktopClient:
    def __init__(self):
        self.process = None
        self.lock = threading.RLock()
        self.sequence = 0
        self.diagnostic_tail = ''

    def _read_diagnostics(self, pipe):
        try:
            for line in iter(lambda: pipe.readline(4096), b''):
                self.diagnostic_tail = (self.diagnostic_tail + redact(line.decode(errors='replace')))[-6000:]
        except (OSError, ValueError):
            pass

    def close(self):
        with self.lock:
            p, self.process = self.process, None
            if p is None:
                return
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=3)
            for pipe in (p.stdin, p.stdout, p.stderr):
                if pipe:
                    pipe.close()

    def capture_existing(self):
        """Reuse the helper's authorized monitor; never start/restore consent here."""
        return self.call('capture_existing')

    def call(self, operation: str, **arguments):
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                self.close()
                self.process = subprocess.Popen(
                    ['/usr/bin/python3', '-B', str(Path(__file__).with_name('desktop_worker.py'))],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    env=minimal_environment(desktop=True), bufsize=0,
                )
                self.diagnostic_tail = ''
                threading.Thread(target=self._read_diagnostics, args=(self.process.stderr,), daemon=True).start()
            self.sequence += 1
            p = self.process
            payload = json.dumps({'id': self.sequence, 'operation': operation, 'arguments': arguments})
            try:
                p.stdin.write((payload + '\n').encode())
                result = bytearray()
                deadline = time.monotonic() + 25
                with selectors.DefaultSelector() as selector:
                    selector.register(p.stdout, selectors.EVENT_READ)
                    while b'\n' not in result:
                        if not selector.select(max(0, deadline - time.monotonic())):
                            raise TimeoutError('Desktop helper timed out; action outcome may be unknown. Observe before retrying.')
                        chunk = os.read(p.stdout.fileno(), 65536)
                        if not chunk:
                            raise RuntimeError('Desktop helper stopped; observe before retrying any action.')
                        result.extend(chunk)
                        if len(result) > 20_000_000:
                            raise RuntimeError('Desktop observation exceeded the response limit.')
                response = json.loads(result)
                if response['id'] != self.sequence:
                    raise RuntimeError('Desktop response sequence mismatch.')
            except Exception:
                self.close()
                raise
            if not response.get('ok'):
                message = response.get('error', 'Desktop operation failed')
                if self.diagnostic_tail:
                    message += '\nDesktop diagnostics: ' + self.diagnostic_tail
                raise RuntimeError(message)
            result = response['result']
            if self.diagnostic_tail and (operation == 'status' or result.get('ok') is False
                                         or (operation == 'observe' and (not result.get('windows') or result.get('inaccessible_nodes')))):
                result['diagnostics'] = self.diagnostic_tail
            return result


client = DesktopClient()
atexit.register(client.close)
