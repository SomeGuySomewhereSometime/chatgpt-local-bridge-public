"""Single desktop owner shared by MCP connections; private user socket."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from persistent_jobs import lock, system_environment


def endpoint():
    root = Path(__file__).resolve().parent
    suffix = hashlib.sha256(str(root).encode()).hexdigest()[:12]
    runtime = Path('/run/user') / str(os.getuid()) / ('bridge-desktop-'+suffix)
    return runtime, 'bridge-desktop-'+suffix+'.service'


class SharedDesktopClient:
    def call(self, operation, **arguments):
        directory, unit = endpoint()
        if directory.is_symlink(): raise ValueError('Invalid desktop runtime directory')
        directory.mkdir(mode=0o700,exist_ok=True)
        os.chmod(directory,0o700)
        path=directory/'control.sock'
        with lock(directory/'startup.lock'):
            probe=socket.socket(socket.AF_UNIX)
            try:
                probe.settimeout(.2);probe.connect(str(path))
                ready=True
            except OSError: ready=False
            finally: probe.close()
            if not ready:
                result=subprocess.run(['/usr/bin/systemd-run','--user','--quiet','--collect',
                    '--unit='+unit,'--service-type=exec','--property=KillMode=control-group',
                    '--property=UMask=0077','--property=StandardOutput=null','--property=StandardError=null',
                    sys.executable,'-B',str(Path(__file__).with_name('desktop_daemon.py'))],
                    env=system_environment(),capture_output=True,timeout=10)
                if result.returncode: raise RuntimeError('Desktop service start not confirmed')
                deadline=time.monotonic()+5
                while not path.exists() and time.monotonic()<deadline:time.sleep(.025)
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(32);connection.connect(str(path))
            request=json.dumps({'operation':operation,'arguments':arguments}).encode()+b'\n'
            if len(request)>1000000:raise ValueError('Desktop request too large')
            connection.sendall(request)
            with connection.makefile('rb') as stream:raw=stream.readline(21000000)
            response=json.loads(raw)
            if not response.get('ok'):raise RuntimeError(response.get('error','Desktop operation failed'))
            return response['result']

    def capture_existing(self): return self.call('capture_existing')
    def close(self): pass  # Disconnecting an MCP must not revoke another connection's session.


client=SharedDesktopClient()
