"""One helper owns desktop consent, snapshots and input for all MCP clients."""
import fcntl
import json
import os
import signal
import socket
from shared_desktop import endpoint
from desktop import DesktopClient


def main():
    os.umask(0o077)
    directory,_=endpoint()
    directory.mkdir(mode=0o700,exist_ok=True)
    fd=os.open(directory/'owner.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    path=directory/'control.sock'
    if path.exists():path.unlink()
    helper=DesktopClient()
    def terminate(*_):raise SystemExit(0)
    signal.signal(signal.SIGTERM,terminate)
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(path));os.chmod(path,0o600);listener.listen(8)
        try:
            while True:
                conn,_=listener.accept()
                with conn:
                    conn.settimeout(30)
                    try:
                        with conn.makefile('rb') as stream:raw=stream.readline(1000001)
                        if not raw:continue
                        if len(raw)>1000000:raise ValueError('Request limit exceeded')
                        message=json.loads(raw)
                        result=helper.call(message['operation'],**message['arguments'])
                        reply={'ok':True,'result':result}
                    except Exception as exc:reply={'ok':False,'error':str(exc)[:1000]}
                    try:conn.sendall(json.dumps(reply).encode()+b'\n')
                    except (OSError,TimeoutError):pass  # Effects may already exist; never retry input.
        finally:
            helper.close();path.unlink(missing_ok=True);os.close(fd)


if __name__=='__main__':main()
