"""Durable jobs supervised by independent systemd user units.

The MCP client owns no process/pipes. Receipts and job IDs are committed before launch.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

from operation_store import configured_directory, identity
from security import host_environment

MAX_STREAM = 4 * 1024 * 1024
RETENTION = 7 * 86400
MAX_JOBS = 256


def atomic_json(path, value):
    fd, name = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        if os.path.exists(name): os.unlink(name)


def read_json(path):
    if path.is_symlink(): raise ValueError('Symlink state rejected')
    return json.loads(path.read_text())


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


@contextmanager
def lock(path):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally: os.close(fd)


def system_environment():
    env = host_environment()
    env['XDG_RUNTIME_DIR'] = '/run/user/' + str(os.getuid())
    env['DBUS_SESSION_BUS_ADDRESS'] = 'unix:path=' + env['XDG_RUNTIME_DIR'] + '/bus'
    return env


class Completion:
    def __init__(self, job): self.job = job
    def is_set(self): return self.job.state()['execution_state'] not in ('accepted','running')
    def wait(self, timeout=None):
        end = time.monotonic() + timeout if timeout is not None else float('inf')
        while not self.is_set():
            if time.monotonic() >= end: return False
            time.sleep(.025)
        return True


class PersistentJob:
    def __init__(self, directory):
        self.directory = directory
        self.meta = read_json(directory / 'meta.json')
        self.cwd = self.meta['cwd']
        self.done = Completion(self)

    def state(self):
        try: value = read_json(self.directory / 'state.json')
        except FileNotFoundError:
            return dict(self.meta, execution_state='unknown', receipt_warning='Dispatch incomplete; do not replay')
        if value['execution_state'] in ('accepted','running'):
            if value.get('boot_id') != boot_id() or identity(value.get('owner_pid')) != value.get('owner_birth'):
                value = dict(value, execution_state='unknown', receipt_warning='Worker unavailable; inspect effects before retrying')
        return value

    @property
    def cleanup_warning(self): return self.state().get('receipt_warning')

    def stop(self):
        if self.done.is_set(): return
        atomic_json(self.directory / 'cancel.json', {'requested_at':time.time()})
        result = subprocess.run(['/usr/bin/systemctl','--user','stop',self.meta['unit']],
                                env=system_environment(), capture_output=True, timeout=10)
        if result.returncode:
            raise ValueError('Cancellation not confirmed; inspect the job, do not replay')
        self.done.wait(3)

    def read(self, out_offset=0, err_offset=0, limit=64000):
        if min(out_offset, err_offset) < 0 or not 1 <= limit <= 262144:
            raise ValueError('Invalid output cursor/limit')
        state = self.state()
        result = {'running':not self.done.is_set(), 'returncode':state.get('returncode'),
            'execution_state':state['execution_state'], 'automatic_retry':False,
            'receipt_warning':state.get('receipt_warning'), 'cleanup_warning':state.get('receipt_warning'),
            'timed_out':state.get('timed_out',False), 'cwd':self.cwd, 'started_at':self.meta['started_at'],
            'history':{}, 'persistent':True, 'cursor_unit':'bytes', 'output_truncated':False,
            **({k:state[k] for k in ('validation_reference','validation_warning') if k in state})}
        for stream, offset in (('stdout',out_offset),('stderr',err_offset)):
            path = self.directory / (stream+'.bin')
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd,'rb') as source:
                    source.seek(offset); data = source.read(limit)
                    size = os.fstat(source.fileno()).st_size
                # Keep UTF-8 boundaries across incremental byte cursors.
                import codecs
                decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
                value = decoder.decode(data, final=not result['running'] and offset+len(data)>=size)
                pending, _ = decoder.getstate()
                cursor = offset + len(data) - len(pending)
            except FileNotFoundError:
                value, cursor, size = '', offset, 0
            result.update({stream:value, stream+'_offset':cursor, stream+'_more':cursor<size})
            result['output_truncated'] |= state.get(stream+'_truncated',False)
        result['output_pending'] = result['stdout_more'] or result['stderr_more']
        return result


class PersistentSessions:
    def __init__(self, directory=None): self.directory = Path(directory) if directory else None
    def root(self, config=None):
        return (self.directory or configured_directory(config or {})) / 'jobs'

    def start(self, workspace, cwd, config, command, timeout=0, history=None, environment=None, validation=None):
        root = self.root(config)
        if validation:
            from validation_evidence import directory as validation_directory
            validation={**validation,'directory':str(validation_directory(config))}
        if root.is_symlink(): raise ValueError('Symlink jobs directory rejected')
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root,0o700)
        with lock(root / '.lock'):
            existing = [p for p in root.iterdir() if p.is_dir() and not p.is_symlink()]
            active = 0
            for p in existing:
                try:
                    job = PersistentJob(p); state = job.state()
                    active += state['execution_state'] in ('accepted','running')
                    # Preserve unknown jobs for operator inspection. Never delete receipts.
                    if state['execution_state'] in ('completed','failed','cancelled','timed_out') and time.time()-state.get('finished_at',time.time()) > RETENTION:
                        shutil.rmtree(p)
                except (OSError,ValueError): pass
            if active >= 64 or len(list(root.glob('*/meta.json'))) >= MAX_JOBS:
                raise ValueError('Job storage capacity reached; stop jobs or remove inspected terminal results')
            key = uuid.uuid4().hex
            directory = root/key; directory.mkdir(mode=0o700)
            unit = 'bridge-persistent-' + key + '.service'
            meta = {'session_id':key,'unit':unit,'cwd':str(cwd),'started_at':time.time()}
            atomic_json(directory/'meta.json',meta)
            atomic_json(directory/'state.json',dict(meta,execution_state='accepted',boot_id=boot_id(),
                owner_pid=os.getpid(),owner_birth=identity(os.getpid())))
            request_id = getattr(history,'request_id',None)
            if history:
                history.store.attach(request_id,key)
            atomic_json(directory/'request.json',{'command':command,'environment':environment or host_environment(),
                'timeout':timeout,'operations':str(root.parent),'request_id':request_id,
                'memory_config':config.get('memory',{}), 'filesystem':config.get('filesystem',{}),
                'memory_disabled':os.environ.get('BRIDGE_MEMORY_DISABLED') == '1',
                **({'validation':validation} if validation else {})})
            launched = subprocess.run(['/usr/bin/systemd-run','--user','--quiet','--collect',
                '--unit='+unit,'--service-type=exec','--property=KillMode=control-group',
                '--property=TimeoutStopSec=3','--property=UMask=0077',
                '--property=StandardOutput=null','--property=StandardError=null',
                sys.executable,'-B',str(Path(__file__).with_name('persistent_job_worker.py')),str(directory)],
                env=system_environment(),capture_output=True,timeout=12)
            if launched.returncode:
                state=read_json(directory/'state.json')
                atomic_json(directory/'state.json',dict(state,execution_state='unknown',receipt_warning='Unit start unconfirmed'))
                raise ValueError('Job dispatch not confirmed; consult its request receipt')
        job = PersistentJob(directory)
        # Wait for ownership transfer or completion before releasing the old gate.
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            state=read_json(directory/'state.json')
            if state.get('owner_pid') != os.getpid() or state['execution_state'] not in ('accepted','running'): break
            time.sleep(.02)
        if state.get('owner_pid') == os.getpid() and state['execution_state'] in ('accepted','running'):
            atomic_json(directory/'state.json',dict(state,execution_state='unknown',receipt_warning='Worker startup not confirmed; do not replay'))
            raise ValueError('Worker startup not confirmed; inspect the attached session')
        if history and history.fd is not None:
            os.close(history.fd); history.fd=None
        return key

    def get(self,key):
        if not re.fullmatch('[a-f0-9]{32}',key): raise ValueError('Unknown command session')
        directory=self.root()/key
        if not directory.is_dir() or directory.is_symlink(): raise ValueError('Unknown command session')
        return PersistentJob(directory)

    def list(self):
        root=self.root()
        if not root.exists(): return []
        results=[]
        for p in sorted(root.iterdir()):
            if re.fullmatch('[a-f0-9]{32}',p.name) and p.is_dir() and not p.is_symlink():
                job=PersistentJob(p); state=job.state()
                results.append({'session_id':p.name,'running':not job.done.is_set(),
                    'execution_state':state['execution_state'],'returncode':state.get('returncode'),
                    'cwd':job.cwd,'started_at':job.meta['started_at'],'persistent':True})
        return results

    def close(self):
        """Disconnect only. Explicit stop(session_id) owns cancellation."""


sessions=PersistentSessions()
