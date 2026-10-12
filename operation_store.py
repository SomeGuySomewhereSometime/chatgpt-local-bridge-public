"""Durable command/mutation receipts and a cross-process recovery gate."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid

DEFAULT_DIRECTORY = Path.home() / '.local/state/chatgpt-local-bridge-NOBUBBLEWARP/operations'


def configured_directory(config):
    return Path(os.environ.get('BRIDGE_OPERATIONS_DIR', config.get('operations', {}).get('directory', str(DEFAULT_DIRECTORY)))).expanduser().resolve()


def identity(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        return None


class OperationStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / 'requests.sqlite3'

    def prepare(self):
        if self.directory.is_symlink():
            raise ValueError('Operational directory must not be a symlink')
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)

    def gate(self, exclusive=False):
        self.prepare()
        fd = os.open(self.directory / 'execution.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            return fd
        except BaseException:
            os.close(fd)
            raise

    @contextmanager
    def connection(self):
        self.prepare()
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                raise ValueError('Unsupported operational schema')
            db.execute("""CREATE TABLE IF NOT EXISTS requests (
                request_id TEXT PRIMARY KEY, signature TEXT NOT NULL, scope TEXT NOT NULL, operation TEXT NOT NULL,
                state TEXT NOT NULL, owner_pid INTEGER, owner_birth TEXT,
                created_at REAL, updated_at REAL, returncode INTEGER,
                timed_out INTEGER DEFAULT 0, session_id TEXT)""")
            db.execute('PRAGMA user_version=1')
            columns = {row[1] for row in db.execute('PRAGMA table_info(requests)')}
            if 'result_json' not in columns:
                db.execute('ALTER TABLE requests ADD COLUMN result_json TEXT')
            for row in db.execute("SELECT DISTINCT owner_pid,owner_birth FROM requests WHERE state IN ('accepted','running')").fetchall():
                if identity(row['owner_pid']) != row['owner_birth']:
                    db.execute("UPDATE requests SET state='unknown',updated_at=? WHERE owner_pid=? AND owner_birth=? AND state IN ('accepted','running')",
                               (time.time(), row['owner_pid'], row['owner_birth']))
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def validate_id(request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', request_id):
            raise ValueError('request_id must be 1-128 ASCII letters, digits, or _.:-')

    @staticmethod
    def public(row):
        result = {k: row[k] for k in ('request_id','scope','operation','state','created_at','updated_at','returncode','timed_out','session_id')}
        if row['result_json']:
            result['result'] = json.loads(row['result_json'])
        return result

    def reserve(self, request_id, scope, operation, parameters):
        self.validate_id(request_id)
        signature = hashlib.sha256(json.dumps([scope, operation, parameters], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        with self.connection() as db:
            row = db.execute('SELECT * FROM requests WHERE request_id=?', (request_id,)).fetchone()
            if row:
                if row['signature'] != signature:
                    raise ValueError('request_id conflict: already used for different parameters')
                return self.public(row), False
            # Never evict retry keys silently: a full store fails before dispatch.
            if db.execute('SELECT count(*) FROM requests').fetchone()[0] >= 100000:
                raise ValueError('Operational receipt capacity reached; operator maintenance required')
            now = time.time()
            db.execute("INSERT INTO requests(request_id,signature,scope,operation,state,owner_pid,owner_birth,created_at,updated_at) VALUES(?,?,?,?,'accepted',?,?,?,?)",
                       (request_id, signature, scope, operation, os.getpid(), identity(os.getpid()), now, now))
            return {'request_id': request_id, 'state': 'accepted'}, True

    def starting(self, request_id):
        with self.connection() as db:
            db.execute("UPDATE requests SET state='running',updated_at=? WHERE request_id=? AND state='accepted'", (time.time(), request_id))

    def progress(self, request_id, metadata):
        with self.connection() as db:
            db.execute('UPDATE requests SET result_json=?,updated_at=? WHERE request_id=?',
                       (json.dumps(metadata), time.time(), request_id))

    def transfer(self, request_id, session_id):
        """Worker takes receipt ownership before dispatching the command."""
        with self.connection() as db:
            row = db.execute('SELECT state,session_id FROM requests WHERE request_id=?', (request_id,)).fetchone()
            if row is None or row['session_id'] != session_id or row['state'] not in ('accepted','running','unknown'):
                raise ValueError('Receipt cannot be transferred')
            db.execute("UPDATE requests SET owner_pid=?,owner_birth=?,state='running',updated_at=? WHERE request_id=?",
                       (os.getpid(), identity(os.getpid()), time.time(), request_id))

    def finish(self, request_id, returncode, timed_out=False, unknown=False):
        with self.connection() as db:
            db.execute('UPDATE requests SET state=?,returncode=?,timed_out=?,updated_at=? WHERE request_id=?',
                       ('unknown' if unknown else 'completed', returncode, int(timed_out), time.time(), request_id))

    def attach(self, request_id, session_id):
        with self.connection() as db:
            db.execute('UPDATE requests SET session_id=? WHERE request_id=?', (session_id, request_id))

    def finish_mutation(self, request_id, result, *, unknown=False, failed=False):
        with self.connection() as db:
            db.execute('UPDATE requests SET state=?,result_json=?,updated_at=? WHERE request_id=?',
                       ('unknown' if unknown else 'failed' if failed else 'completed',
                        json.dumps(result, ensure_ascii=False), time.time(), request_id))

    def get(self, request_id, scope):
        self.validate_id(request_id)
        with self.connection() as db:
            row = db.execute('SELECT * FROM requests WHERE request_id=? AND scope=?', (request_id, scope)).fetchone()
            if row is None:
                raise ValueError('Operation request not found in this scope')
            return self.public(row)

    def resolve_unknown(self, request_id, scope):
        """Operator-only acknowledgement AFTER inspecting effects; never executes anything."""
        self.validate_id(request_id)
        with self.connection() as db:
            row = db.execute('SELECT state FROM requests WHERE request_id=? AND scope=?', (request_id, scope)).fetchone()
            if row is None or row['state'] not in ('unknown', 'resolved'):
                raise ValueError('Only an unknown request may be acknowledged')
            db.execute("UPDATE requests SET state='resolved',updated_at=? WHERE request_id=? AND scope=?", (time.time(), request_id, scope))

    def transport_blockers(self):
        """Persistent workers survive MCP restarts; unknown outcomes still block recovery."""
        with self.connection() as db:
            result = {}
            for row in db.execute("SELECT state,operation,result_json FROM requests WHERE state NOT IN ('completed','resolved','failed')"):
                metadata = json.loads(row['result_json'] or '{}')
                if row['state'] == 'running' and row['operation'] == 'run_command' and metadata.get('persistent') is True:
                    continue
                result[row['state']] = result.get(row['state'], 0) + 1
            return result

    def blockers(self):
        with self.connection() as db:
            return dict(db.execute("SELECT state,count(*) FROM requests WHERE state NOT IN ('completed','resolved','failed') GROUP BY state").fetchall())


class TrackedHistory:
    """Hold the recovery gate until execution ends, even if the tool reply is lost."""
    def __init__(self, store, request_id, memory, gate):
        self.store, self.request_id, self.memory, self.fd = store, request_id, memory, gate
        self.warning = None

    def begin(self, cwd):
        return self.memory.begin(cwd)

    def finish(self, cwd, receipt, returncode, timed_out=False, unknown=False):
        try:
            self.memory.finish(cwd, receipt, returncode, timed_out, unknown)
        finally:
            self.complete(returncode, timed_out, unknown)

    def complete(self, returncode, timed_out=False, unknown=False):
        if self.fd is None:
            return
        try:
            self.store.finish(self.request_id, returncode, timed_out, unknown)
        except Exception:
            self.warning = 'Receipt update failed; command may have executed. Do not replay.'
        finally:
            os.close(self.fd)
            self.fd = None


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Acknowledge an unknown operation only after inspecting its effects; never replay it.')
    parser.add_argument('action', choices=['resolve'])
    parser.add_argument('request_id')
    parser.add_argument('--scope', required=True)
    args = parser.parse_args()
    config = json.loads(Path(__file__).with_name('bridge_config.json').read_text())
    store = OperationStore(configured_directory(config))
    scope = str(Path(args.scope).expanduser().resolve())
    store.resolve_unknown(args.request_id, scope)
    print(json.dumps(store.get(args.request_id, scope)))
