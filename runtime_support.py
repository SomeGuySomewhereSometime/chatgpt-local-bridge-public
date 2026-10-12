"""Cooperative host coordination, durable mutations and project tool caches.

These guards coordinate bridge clients; they do not contain host commands or editors.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import time
import uuid

from fastapi import HTTPException
from bounded_io import version


class RuntimeSupport:
    def __init__(self, store):
        self.store = store

    @contextmanager
    def lock(self, name):
        self.store.prepare()
        key = hashlib.sha256(name.encode()).hexdigest()
        fd = os.open(self.store.directory / (key + '.lock'),
                     os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            # Refuse contention promptly instead of tying up a tool thread.
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise HTTPException(409, 'Resource busy; inspect the other operation before retrying') from None
            yield
        finally:
            os.close(fd)

    @staticmethod
    def overlap(a, b):
        if a.startswith('path:') and b.startswith('path:'):
            x, y = Path(a[5:]), Path(b[5:])
            return x == y or x.is_relative_to(y) or y.is_relative_to(x)
        return a == b

    def _leases(self, db):
        db.execute('''CREATE TABLE IF NOT EXISTS leases (
            resource TEXT PRIMARY KEY, owner TEXT NOT NULL, token TEXT NOT NULL,
            expires REAL NOT NULL)''')
        db.execute('DELETE FROM leases WHERE expires<=?', (time.time(),))
        return db.execute('SELECT * FROM leases').fetchall()

    def lease(self, action, resource='', owner='', token='', ttl=300):
        if action not in ('list', 'acquire', 'renew', 'release'):
            raise HTTPException(400, 'Invalid lease action')
        if action != 'list' and (not resource or not owner or len(owner) > 128):
            raise HTTPException(400, 'Resource and a stable chat owner_id are required')
        if not 30 <= ttl <= 3600:
            raise HTTPException(400, 'Lease ttl must be 30..3600 seconds')
        if token:
            try:
                self.store.validate_id(token)
            except ValueError:
                raise HTTPException(400, 'Invalid lease_token') from None
        with self.lock('mutations'):
            with self.store.connection() as db:
                rows = self._leases(db)
                if action == 'list':
                    return {'leases': [{'resource': r['resource'], 'owner_id': r['owner'],
                                        'expires_at': r['expires']} for r in rows]}
                row = next((r for r in rows if r['resource'] == resource), None)
                if action == 'acquire':
                    conflicts = [r for r in rows if self.overlap(resource, r['resource'])]
                    if conflicts:
                        # Repeated acquire can renew only with the original opaque token.
                        if row is None or row['owner'] != owner or row['token'] != token or len(conflicts) != 1:
                            raise HTTPException(409, 'Resource leased; use another project or wait for release/expiry')
                    token = token or uuid.uuid4().hex
                    expires = time.time() + ttl
                    db.execute('INSERT OR REPLACE INTO leases VALUES(?,?,?,?)', (resource, owner, token, expires))
                else:
                    if row is None or row['owner'] != owner or row['token'] != token:
                        raise HTTPException(409, 'Lease missing, expired or owned by another chat')
                    expires = time.time() + ttl
                    if action == 'renew':
                        db.execute('UPDATE leases SET expires=? WHERE resource=?', (expires, resource))
                    else:
                        db.execute('DELETE FROM leases WHERE resource=?', (resource,))
                return {'ok': True, 'resource': resource, 'owner_id': owner,
                        'lease_token': token, 'expires_at': expires if action != 'release' else None}

    def check_leases(self, resources, owner='', token=''):
        with self.store.connection() as db:
            for row in self._leases(db):
                if any(self.overlap(r, row['resource']) for r in resources):
                    if row['owner'] != owner or row['token'] != token:
                        raise HTTPException(409, 'Resource leased by another chat; supply its owner_id and lease_token')

    @contextmanager
    def guard(self, resources, owner='', token=''):
        # One bridge-wide short mutation lock makes path overlap deterministic,
        # even across renames and separate stdio server processes.
        with self.lock('mutations'):
            self.check_leases(resources, owner, token)
            yield

    def mutate(self, operation, scope, parameters, action, *, request_id='',
               resources=(), owner='', token=''):
        request_id = request_id or uuid.uuid4().hex
        gate = None
        try:
            gate = self.store.gate()
            _, fresh = self.store.reserve(request_id, scope, operation, parameters)
            if not fresh:
                receipt = self.store.get(request_id, scope)
                return dict(receipt.get('result') or {}, receipt=receipt,
                            ok=receipt['state'] == 'completed' and receipt.get('result', {}).get('ok', True),
                            state=receipt['state'], request_id=request_id, replayed=True, automatic_retry=False)
            try:
                with self.guard(resources, owner, token):
                    self.store.starting(request_id)
                    result = action()
                    # Persist only metadata, never file content, screenshots, input text or credentials.
                    metadata = {k: result[k] for k in (
                        'ok', 'path', 'version', 'bytes_written', 'cwd', 'source', 'destination',
                        'handle', 'pid', 'app', 'project', 'timestamp', 'completed', 'verified',
                        'executed', 'click_sent', 'click_status', 'recording', 'output_path', 'component',
                        'integration_ready', 'ready', 'status', 'session_id', 'returncode',
                        'timed_out', 'cleanup_warning') if k in result}
                    for key in ('project_root', 'project_id', 'state_directory', 'cache_directory',
                                'tools_directory', 'python_environment', 'managed_python_ready',
                                'prepared', 'home_preserved'):
                        if key in result:
                            metadata[key] = result[key]
                    # A partial GUI batch can have effects despite ok=false.
                    uncertain = result.get('ok') is False
                    self.store.finish_mutation(request_id, metadata, unknown=uncertain)
                    return dict(result, request_id=request_id, replayed=False,
                                receipt_scope=scope, state='unknown' if uncertain else 'completed',
                                deduplication_protected=bool(parameters.get('request_id')))
            except HTTPException as exc:
                self.store.finish_mutation(request_id, {}, unknown=exc.status_code >= 500 or exc.status_code == 408,
                                           failed=exc.status_code < 500 and exc.status_code != 408)
                raise
            except BaseException:
                # Effects may have happened before an OS/helper exception.
                self.store.finish_mutation(request_id, {}, unknown=True)
                raise
        except (ValueError, sqlite3.Error, OSError) as exc:
            if isinstance(exc, ValueError):
                raise HTTPException(409, str(exc)) from None
            raise HTTPException(503, 'Operational storage/cleanup failed; inspect effects before retrying') from None
        finally:
            if gate is not None:
                os.close(gate)

    def environment(self, project, base, *, prepare=True):
        project = Path(project).resolve()
        key = hashlib.sha256(str(project).encode()).hexdigest()[:32]
        root = self.store.directory / 'projects' / key
        cache, tools, venv = root / 'cache', root / 'tools', root / 'venv'
        if prepare:
            self.store.prepare()
            for directory in (root, cache, tools / 'bin', root / 'build'):
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        env = dict(base)
        env.update(XDG_CACHE_HOME=str(cache), PIP_CACHE_DIR=str(cache / 'pip'),
                   npm_config_cache=str(cache / 'npm'), npm_config_prefix=str(tools),
                   CARGO_TARGET_DIR=str(root / 'build' / 'cargo'),
                   BRIDGE_PROJECT_ROOT=str(project), BRIDGE_PROJECT_ID=key)
        local_venv = project / '.venv'
        active = venv if (venv / 'bin/python').exists() else local_venv if (local_venv / 'bin/python').exists() else None
        paths = ([str(active / 'bin')] if active else []) + [str(tools / 'bin'), env['PATH']]
        env['PATH'] = ':'.join(paths)
        if active:
            env['VIRTUAL_ENV'] = str(active)
        return env, {'project_root': str(project), 'project_id': key, 'state_directory': str(root),
                     'cache_directory': str(cache), 'tools_directory': str(tools),
                     'python_environment': str(active) if active else None,
                     'managed_python_ready': (venv / 'bin/python').exists(),
                     'prepared': root.exists(), 'home_preserved': True}


def file_version(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return 'missing'
    if not stat.S_ISREG(info.st_mode):
        raise HTTPException(400, 'Atomic writes require a regular file; symlinks are not replaced')
    return version(info)


def atomic_write(path, content, path_check, expected_version=''):
    """Replace one file atomically, with compare-before-replace and fsync.

    Cooperative bridge writes serialize externally. An unrelated host writer can still
    race the final comparison; this is not an OS-level compare-and-swap primitive.
    """
    path = Path(path)
    path_check(str(path), write=True, follow_leaf=False)
    original = file_version(path)
    if expected_version and expected_version != original:
        raise HTTPException(409, 'File changed; read the current version before writing')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.bridge-write-', dir=path.parent)
    tmp = Path(temporary)
    try:
        with os.fdopen(fd, 'wb') as stream:
            if original != 'missing':
                os.fchmod(stream.fileno(), stat.S_IMODE(path.stat().st_mode))
            stream.write(content.encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        # Validate the parent again immediately before replacement.
        if path_check(str(path), write=True, follow_leaf=False) != path:
            raise HTTPException(409, 'File parent changed during write')
        if file_version(path) != original:
            raise HTTPException(409, 'File changed during write; read it again')
        if original == 'missing':
            # Creation must not overwrite a concurrently created file.
            try:
                os.link(tmp, path, follow_symlinks=False)
            except FileExistsError:
                raise HTTPException(409, 'File was created concurrently') from None
            tmp.unlink()
        else:
            os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return {'ok': True, 'version': file_version(path), 'bytes_written': len(content.encode('utf-8'))}
    finally:
        tmp.unlink(missing_ok=True)
