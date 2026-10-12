"""Bounded project memory. Stored text is evidence/context, never instructions."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

from diagnostics import redact


class MemoryError(ValueError):
    pass


def process_identity(pid):
    """Linux start time distinguishes PID reuse; no command line is read."""
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        return None


class MemoryStore:
    def __init__(self, path, *, retention_days=30, max_commands=5000, max_entries=2000):
        self.path = Path(path).expanduser().absolute()
        if not 1 <= retention_days <= 3650 or not 1 <= max_commands <= 100000 or not 1 <= max_entries <= 100000:
            raise MemoryError('Invalid memory retention/capacity')
        self.retention_days, self.max_commands, self.max_entries = retention_days, max_commands, max_entries
        self.pid, self.birth = os.getpid(), process_identity(os.getpid())
        self.lock = threading.RLock()
        self.ready = False

    @contextmanager
    def bounded_lock(self):
        if not self.lock.acquire(timeout=2):
            raise MemoryError('Memory busy; retry the memory request with the same request_id')
        try:
            yield
        finally:
            self.lock.release()

    @staticmethod
    def _enable_wal(db):
        # journal_mode can report SQLITE_BUSY immediately during concurrent first
        # opens, even with busy_timeout set. Only this idempotent setup is retried.
        deadline = time.monotonic() + 2
        while True:
            try:
                mode = db.execute('PRAGMA journal_mode=WAL').fetchone()[0]
                if mode != 'wal':
                    raise MemoryError('Memory storage does not support WAL')
                return
            except sqlite3.OperationalError as exc:
                if getattr(exc, 'sqlite_errorcode', 0) & 255 not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or time.monotonic() >= deadline:
                    raise
                time.sleep(.02)

    @contextmanager
    def connection(self):
        # Serialize this process; SQLite transactions also serialize other processes.
        with self.bounded_lock():
            if self.path.parent.is_symlink() or self.path.is_symlink():
                raise MemoryError('Memory path must not be a symlink')
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.path.parent, 0o700)
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            os.fchmod(fd, 0o600)
            os.close(fd)
            db = sqlite3.connect(self.path, timeout=2)
            db.row_factory = sqlite3.Row
            try:
                db.execute('PRAGMA foreign_keys=ON')
                db.execute('PRAGMA secure_delete=ON')
                if not self.ready:
                    self._enable_wal(db)
                    db.execute('BEGIN IMMEDIATE')
                    version = db.execute('PRAGMA user_version').fetchone()[0]
                    if version not in (0, 1):
                        raise MemoryError('Unsupported memory schema; restore compatible code or backup')
                    if version == 0:
                        for sql in SCHEMA:
                            db.execute(sql)
                        db.execute('PRAGMA user_version=1')
                    self._recover(db)
                    db.commit()
                    self.ready = True
                db.execute('BEGIN IMMEDIATE')
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

    def _recover(self, db):
        for row in db.execute("SELECT DISTINCT owner_pid,owner_birth FROM entries WHERE kind='command' AND state='running'").fetchall():
            if process_identity(row['owner_pid']) != row['owner_birth']:
                db.execute("UPDATE entries SET state='unknown',updated_at=?,version=version+1 WHERE kind='command' AND state='running' AND owner_pid=? AND owner_birth=?",
                           (time.time(), row['owner_pid'], row['owner_birth']))

    @staticmethod
    def _row(db, project, entry_id):
        row = db.execute('SELECT * FROM entries WHERE project=? AND id=?', (project, entry_id)).fetchone()
        if row is None:
            raise MemoryError('Memory entry not found in this project')
        return {k: row[k] for k in row.keys() if k not in ('owner_pid', 'owner_birth')}

    def save(self, project, *, title, body, source, request_id, kind='note', entry_id='', expected_version=None, priority=0):
        if kind not in ('note', 'checkpoint') or not isinstance(priority, int) or not 0 <= priority <= 2:
            raise MemoryError('Invalid memory kind or priority')
        for value, maximum in ((title, 200), (body, 8000), (source, 500), (request_id, 128)):
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise MemoryError('Memory fields must be nonempty and within their size limits')
        if bool(entry_id) != (expected_version is not None):
            raise MemoryError('Updates require entry_id and expected_version together')
        title, body, source = map(redact, (title, body, source))
        signature = hashlib.sha256(json.dumps([kind, title, body, source, entry_id, expected_version, priority], ensure_ascii=False).encode()).hexdigest()
        now = time.time()
        with self.connection() as db:
            prior = db.execute('SELECT * FROM requests WHERE project=? AND request_id=?', (project, request_id)).fetchone()
            if prior:
                if prior['signature'] != signature:
                    raise MemoryError('request_id already used for different content')
                return dict(self._row(db, project, prior['entry_id']), replayed=True)
            if entry_id:
                old = self._row(db, project, entry_id)
                if old['kind'] != kind or old['version'] != expected_version:
                    raise MemoryError('Memory version or kind conflict; read current entry first')
                if db.execute('SELECT count(*) FROM requests WHERE entry_id=?', (entry_id,)).fetchone()[0] >= 1000:
                    raise MemoryError('Entry revision limit reached; save a replacement and delete this entry')
                db.execute('UPDATE entries SET title=?,body=?,source=?,priority=?,updated_at=?,version=version+1 WHERE id=?',
                           (title, body, source, priority, now, entry_id))
            else:
                if db.execute("SELECT count(*) FROM entries WHERE project=? AND kind!='command'", (project,)).fetchone()[0] >= self.max_entries:
                    raise MemoryError('Project memory capacity reached; remove obsolete entries')
                entry_id = uuid.uuid4().hex
                db.execute('INSERT INTO entries(id,project,kind,title,body,source,priority,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                           (entry_id, project, kind, title, body, source, priority, now, now))
            db.execute('INSERT INTO requests VALUES(?,?,?,?)', (project, request_id, signature, entry_id))
            return dict(self._row(db, project, entry_id), replayed=False)

    def get(self, project, entry_id):
        with self.connection() as db:
            return self._row(db, project, entry_id)

    def search(self, project, query='', kind='', limit=10):
        if kind not in ('', 'note', 'checkpoint', 'command') or not 1 <= limit <= 50 or len(query) > 500:
            raise MemoryError('Invalid search filter or limit')
        tokens = re.findall(r'\w+', query, re.UNICODE)[:20]
        if query.strip() and not tokens:
            return []
        with self.connection() as db:
            self._recover(db)
            self._prune(db)
            sql = 'SELECT e.* FROM entries e'
            params = []
            if tokens:
                sql += ' JOIN entries_fts f ON f.rowid=e.rowid'
            sql += ' WHERE e.project=?'
            params.append(project)
            if kind:
                sql += ' AND e.kind=?'
                params.append(kind)
            if tokens:
                sql += ' AND entries_fts MATCH ?'
                params.append(' AND '.join('"' + t + '"' for t in tokens))
            sql += ' ORDER BY ' + ('bm25(entries_fts), ' if tokens else '') + ('' if kind == 'checkpoint' else 'e.priority DESC,') + 'e.updated_at DESC,e.id LIMIT ?'
            params.append(limit)
            return [{k: (row[k][:400] if k == 'body' else row[k]) for k in row.keys() if k not in ('owner_pid', 'owner_birth')}
                    for row in db.execute(sql, params)]

    def context(self, project):
        checkpoint = self.search(project, kind='checkpoint', limit=1)
        if checkpoint:
            checkpoint[0]['body'] = self.get(project, checkpoint[0]['id'])['body'][:3000]
        return {'status': 'available', 'project': project, 'untrusted_context': True,
                'checkpoint': checkpoint,
                'notes': self.search(project, kind='note', limit=5)}

    def delete(self, project, entry_id, expected_version):
        with self.connection() as db:
            old = db.execute('SELECT version FROM entries WHERE project=? AND id=?', (project, entry_id)).fetchone()
            if old is None:
                return {'deleted': False, 'id': entry_id}
            if old['version'] != expected_version:
                raise MemoryError('Memory version conflict; read current entry first')
            db.execute('DELETE FROM entries WHERE project=? AND id=?', (project, entry_id))
            return {'deleted': True, 'id': entry_id}

    def _prune(self, db):
        db.execute("DELETE FROM entries WHERE kind='command' AND state!='running' AND updated_at<?", (time.time() - self.retention_days * 86400,))
        db.execute("DELETE FROM entries WHERE id IN (SELECT id FROM entries WHERE kind='command' AND state!='running' ORDER BY updated_at DESC LIMIT -1 OFFSET ?)", (self.max_commands,))

    def command_start(self, project):
        entry_id, now = uuid.uuid4().hex, time.time()
        with self.connection() as db:
            self._recover(db)
            self._prune(db)
            db.execute("INSERT INTO entries(id,project,kind,title,body,source,state,created_at,updated_at,owner_pid,owner_birth) VALUES(?,?,'command','Command execution','','bridge','running',?,?,?,?)",
                       (entry_id, project, now, now, self.pid, self.birth))
        return entry_id

    def command_finish(self, project, entry_id, returncode, timed_out=False, unknown=False):
        with self.connection() as db:
            db.execute("UPDATE entries SET state=?,returncode=?,timed_out=?,updated_at=?,version=version+1 WHERE project=? AND id=? AND kind='command'",
                       ('unknown' if unknown else 'completed', returncode, int(timed_out), time.time(), project, entry_id))
            self._prune(db)

    def backup(self, destination):
        """Local operator API only, never an arbitrary-path MCP tool."""
        destination = Path(destination)
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        # backup must not run inside our write transaction.
        with self.connection():
            pass
        with self.bounded_lock():
            src = sqlite3.connect(self.path)
            dst = sqlite3.connect(destination)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
        return str(destination)


SCHEMA = [
    """CREATE TABLE entries(id TEXT PRIMARY KEY, project TEXT NOT NULL, kind TEXT NOT NULL,
       title TEXT NOT NULL, body TEXT NOT NULL, source TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 0,
       state TEXT NOT NULL DEFAULT 'active', version INTEGER NOT NULL DEFAULT 1,
       created_at REAL NOT NULL, updated_at REAL NOT NULL, owner_pid INTEGER, owner_birth TEXT,
       returncode INTEGER, timed_out INTEGER NOT NULL DEFAULT 0)""",
    'CREATE INDEX entries_project ON entries(project,kind,updated_at)',
    'CREATE TABLE requests(project TEXT NOT NULL, request_id TEXT NOT NULL, signature TEXT NOT NULL, entry_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE, PRIMARY KEY(project,request_id))',
    "CREATE VIRTUAL TABLE entries_fts USING fts5(title,body,source,content='entries',content_rowid='rowid',tokenize='unicode61 remove_diacritics 2')",
    'CREATE TRIGGER entries_ai AFTER INSERT ON entries BEGIN INSERT INTO entries_fts(rowid,title,body,source) VALUES(new.rowid,new.title,new.body,new.source); END',
    "CREATE TRIGGER entries_ad AFTER DELETE ON entries BEGIN INSERT INTO entries_fts(entries_fts,rowid,title,body,source) VALUES('delete',old.rowid,old.title,old.body,old.source); END",
    "CREATE TRIGGER entries_au AFTER UPDATE ON entries BEGIN INSERT INTO entries_fts(entries_fts,rowid,title,body,source) VALUES('delete',old.rowid,old.title,old.body,old.source); INSERT INTO entries_fts(rowid,title,body,source) VALUES(new.rowid,new.title,new.body,new.source); END",
]


class MemoryService:
    """Optional persistence: errors never imply that a shell action did not execute."""
    def __init__(self, config):
        settings = config.get('memory', {})
        self.enabled = settings.get('enabled', False) is True and os.environ.get('BRIDGE_MEMORY_DISABLED') != '1'
        self.store = None
        if self.enabled:
            path = Path(settings.get('path', '~/.local/state/chatgpt-local-bridge-NOBUBBLEWARP/memory/memory.sqlite3')).expanduser().resolve()
            # DB must be inside an existing denied subtree, inaccessible to shell/file tools.
            if not any(path.is_relative_to(Path(p).resolve()) for p in config.get('filesystem', {}).get('denied_paths', [])):
                raise MemoryError('Memory database must be inside a denied filesystem subtree')
            self.store = MemoryStore(path, retention_days=settings.get('retention_days', 30),
                                     max_commands=settings.get('max_commands', 5000), max_entries=settings.get('max_entries_per_project', 2000))

    def require(self):
        if not self.enabled:
            raise MemoryError('Bridge memory is disabled')
        return self.store

    def context(self, project):
        if not self.enabled:
            return {'status': 'disabled'}
        try:
            return self.store.context(project)
        except (OSError, sqlite3.Error, MemoryError):
            return {'status': 'unavailable', 'warning': 'Memory could not be read; inspect local storage'}

    def begin(self, project):
        receipt = {'status': 'disabled'}
        if self.enabled:
            try:
                receipt = {'status': 'recorded', 'id': self.store.command_start(project)}
            except (OSError, sqlite3.Error, MemoryError):
                receipt = {'status': 'unavailable', 'warning': 'Execution history not recorded; do not retry the command for this reason'}
        return receipt

    def finish(self, project, receipt, returncode, timed_out=False, unknown=False):
        if receipt['status'] == 'recorded':
            try:
                self.store.command_finish(project, receipt['id'], returncode, timed_out, unknown)
            except (OSError, sqlite3.Error, MemoryError):
                receipt.update(status='unavailable', warning='Command outcome exists but history update failed; do not replay')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Back up configured Bridge memory to a NEW private file')
    parser.add_argument('--backup', required=True, metavar='DESTINATION')
    args = parser.parse_args()
    # Read only the configured Bridge storage settings, without importing servers.
    config_path = Path(os.environ.get('BRIDGE_CONFIG', str(Path(__file__).with_name('bridge_config.json'))))
    service = MemoryService(json.loads(config_path.read_text()))
    print(service.require().backup(args.backup))
