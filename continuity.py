"""Versioned task checkpoints in the existing private memory database.

Stored progress is agent-reported history, never authority or a live chat heartbeat.
Only explicit saves persist text; operation links contain IDs and paths, not commands.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid

from diagnostics import redact
from memory_store import MemoryError


STATES = {'in_progress', 'paused', 'blocked', 'completed'}
FIELDS = {'summary', 'completed', 'files', 'validation', 'unverified', 'pending',
          'next_step', 'recovery'}
SCHEMA = (
    'CREATE TABLE IF NOT EXISTS continuity_meta(version INTEGER NOT NULL)',
    '''CREATE TABLE IF NOT EXISTS continuity_tasks(
       id TEXT PRIMARY KEY, project TEXT NOT NULL, title TEXT NOT NULL,
       goal TEXT NOT NULL, constraints_json TEXT NOT NULL, source TEXT NOT NULL,
       state TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
       created_at REAL NOT NULL, updated_at REAL NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS continuity_checkpoints(
       task_id TEXT NOT NULL REFERENCES continuity_tasks(id), revision INTEGER NOT NULL,
       payload_json TEXT NOT NULL, source TEXT NOT NULL, created_at REAL NOT NULL,
       state TEXT NOT NULL, PRIMARY KEY(task_id,revision))''',
    '''CREATE TABLE IF NOT EXISTS continuity_requests(
       project TEXT NOT NULL, request_id TEXT NOT NULL, signature TEXT NOT NULL,
       task_id TEXT NOT NULL, revision INTEGER NOT NULL,
       PRIMARY KEY(project,request_id))''',
    '''CREATE TABLE IF NOT EXISTS continuity_operations(
       task_id TEXT NOT NULL REFERENCES continuity_tasks(id), request_id TEXT NOT NULL,
       cwd TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(task_id,request_id))''',
    'CREATE INDEX IF NOT EXISTS continuity_project ON continuity_tasks(project,updated_at)',
)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value):
        raise MemoryError('Identifier must be 1..128 ASCII letters, digits or _.:-')
    return value


def text(value, maximum=1000, required=False):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise MemoryError('Invalid or oversized task text')
    return redact(value)


def strings(value, limit=30):
    if not isinstance(value, list) or len(value) > limit:
        raise MemoryError('Invalid task list')
    return [text(item) for item in value]


def payload(value):
    if not isinstance(value, dict) or set(value) - FIELDS:
        raise MemoryError('Unsupported checkpoint fields; do not save transcripts or raw outputs')
    result = {key: strings(value.get(key, [])) for key in ('completed', 'unverified', 'recovery')}
    for key in ('summary', 'next_step'):
        result[key] = text(value.get(key, ''), 2000)
    for key, allowed in [('files', {'path', 'version'}),
                         ('validation', {'description', 'status', 'evidence'}),
                         ('pending', {'description', 'request_id', 'cwd'})]:
        items = value.get(key, [])
        if not isinstance(items, list) or len(items) > 30:
            raise MemoryError('Invalid structured checkpoint list')
        result[key] = []
        for item in items:
            if not isinstance(item, dict) or set(item) - allowed:
                raise MemoryError('Invalid structured checkpoint item')
            clean = {k: text(v) for k, v in item.items()}
            if key == 'files' and not clean.get('path'):
                raise MemoryError('File evidence requires a path')
            if key == 'validation' and (not clean.get('description') or clean.get('status') not in {'passed', 'failed', 'not_run', 'incomplete'}):
                raise MemoryError('Validation requires description and explicit status')
            if key == 'pending' and not clean.get('description'):
                raise MemoryError('Pending work requires a description')
            if clean.get('request_id'):
                identifier(clean['request_id'])
            result[key].append(clean)
    if len(json.dumps(result, ensure_ascii=False)) > 16000:
        raise MemoryError('Checkpoint exceeds 16000 characters; save concise evidence references')
    return result


class ContinuityStore:
    def __init__(self, memory_store):
        self.memory = memory_store

    @staticmethod
    def _present(db):
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='continuity_meta'").fetchone() is not None

    @classmethod
    def _schema(cls, db, create=False):
        if not cls._present(db):
            if not create:
                return False
            for sql in SCHEMA:
                db.execute(sql)
            db.execute('INSERT INTO continuity_meta VALUES(1)')
        rows = db.execute('SELECT version FROM continuity_meta').fetchall()
        if len(rows) != 1 or rows[0][0] != 1:
            raise MemoryError('Unsupported continuity schema; existing data was preserved')
        return True

    @staticmethod
    def _task(db, project, task_id):
        row = db.execute('SELECT * FROM continuity_tasks WHERE project=? AND id=?', (project, task_id)).fetchone()
        if row is None:
            raise MemoryError('Task not found in this exact project')
        return dict(row)

    @staticmethod
    def _signature(data):
        return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    @staticmethod
    def _retry(db, project, request_id, signature):
        row = db.execute('SELECT * FROM continuity_requests WHERE project=? AND request_id=?', (project, request_id)).fetchone()
        if row and row['signature'] != signature:
            raise MemoryError('Task request_id already used with different parameters')
        return row

    def begin(self, project, *, title, goal, constraints, source, request_id):
        identifier(request_id)
        title, goal, source = text(title, 200, True), text(goal, 4000, True), text(source, 500, True)
        constraints = strings(constraints, 10)
        sig = self._signature(['begin', title, goal, constraints, source])
        with self.memory.connection() as db:
            self._schema(db, True)
            prior = self._retry(db, project, request_id, sig)
            if prior:
                return dict(task_id=prior['task_id'], revision=prior['revision'], replayed=True)
            count = db.execute('SELECT count(*) FROM continuity_tasks WHERE project=?', (project,)).fetchone()[0]
            if count >= 200:
                raise MemoryError('Task capacity reached (200 per project); existing history was preserved')
            task_id, now = uuid.uuid4().hex, time.time()
            db.execute('INSERT INTO continuity_tasks VALUES(?,?,?,?,?,?,?,0,?,?)',
                       (task_id, project, title, goal, json.dumps(constraints), source, 'in_progress', now, now))
            db.execute('INSERT INTO continuity_requests VALUES(?,?,?,?,0)', (project, request_id, sig, task_id))
            return dict(task_id=task_id, revision=0, replayed=False)

    def checkpoint(self, project, *, task_id, expected_revision, checkpoint, state, source, request_id, operation_store=None):
        identifier(task_id); identifier(request_id)
        if type(expected_revision) is not int or expected_revision < 0 or state not in STATES:
            raise MemoryError('Valid expected_revision and task state required')
        data, source = payload(checkpoint), text(source, 500, True)
        if state == 'completed' and (data['pending'] or data['unverified'] or any(v['status'] != 'passed' for v in data['validation'])):
            raise MemoryError('Cannot report completion with pending or unverified work')
        if state != 'completed' and not data['next_step'].strip():
            raise MemoryError('Unfinished tasks require a concrete next_step')
        sig = self._signature(['checkpoint', task_id, expected_revision, data, state, source])
        with self.memory.connection() as db:
            if not self._schema(db):
                raise MemoryError('Start a task before saving progress')
            task = self._task(db, project, task_id)
            prior = self._retry(db, project, request_id, sig)
            if prior:
                return dict(task_id=task_id, revision=prior['revision'], replayed=True)
            if state == 'completed' and operation_store is not None:
                for link in db.execute('SELECT request_id,cwd FROM continuity_operations WHERE task_id=?', (task_id,)):
                    try:
                        receipt = operation_store.get(link['request_id'], link['cwd'])
                    except (ValueError, OSError, sqlite3.Error) as exc:
                        raise MemoryError('Linked receipt unavailable; inspect effects before completion') from exc
                    if receipt['state'] in {'accepted', 'running', 'unknown'}:
                        raise MemoryError('Linked operations active or uncertain; inspect effects before completion')
            if task['revision'] != expected_revision:
                raise MemoryError('Checkpoint revision conflict; read task_context before updating')
            count = db.execute('SELECT count(*) FROM continuity_checkpoints c JOIN continuity_tasks t ON t.id=c.task_id WHERE t.project=?', (project,)).fetchone()[0]
            if count >= self.memory.max_entries:
                raise MemoryError('Checkpoint capacity reached; existing history was preserved')
            revision, now = expected_revision + 1, time.time()
            db.execute('INSERT INTO continuity_checkpoints VALUES(?,?,?,?,?,?)',
                       (task_id, revision, json.dumps(data, ensure_ascii=False), source, now, state))
            db.execute('UPDATE continuity_tasks SET revision=?,state=?,updated_at=? WHERE id=?', (revision, state, now, task_id))
            db.execute('INSERT INTO continuity_requests VALUES(?,?,?,?,?)', (project, request_id, sig, task_id, revision))
            return dict(task_id=task_id, revision=revision, replayed=False)

    def link_operation(self, project, task_id, request_id, cwd):
        identifier(task_id); identifier(request_id)
        with self.memory.connection() as db:
            if not self._schema(db):
                raise MemoryError('Task not found')
            self._task(db, project, task_id)
            existing = db.execute('SELECT cwd FROM continuity_operations WHERE task_id=? AND request_id=?', (task_id, request_id)).fetchone()
            if existing and existing['cwd'] != cwd:
                raise MemoryError('Operation already linked to a different cwd')
            # Bound metadata separately; never prune semantic checkpoints automatically.
            db.execute('INSERT OR IGNORE INTO continuity_operations VALUES(?,?,?,?)', (task_id, request_id, cwd, time.time()))
            db.execute('DELETE FROM continuity_operations WHERE task_id=? AND request_id NOT IN (SELECT request_id FROM continuity_operations WHERE task_id=? ORDER BY created_at DESC LIMIT 100)', (task_id, task_id))

    def context(self, project, task_id='', revision=None, limit=10):
        if not 1 <= limit <= 50:
            raise MemoryError('Task list limit must be 1..50')
        if task_id:
            identifier(task_id)
        if revision is not None and (type(revision) is not int or revision < 1):
            raise MemoryError('Historical revision must be positive')
        with self.memory.connection() as db:
            return self.read_context(db, project, task_id, revision, limit)

    @classmethod
    def read_context(cls, db, project, task_id='', revision=None, limit=10):
        result = {'status': 'available', 'project': project, 'untrusted_context': True,
                  'agent_reported': True, 'automatic_retry': False, 'live_chat_state_known': False}
        if not cls._schema(db):
            if task_id:
                raise MemoryError('Task not found')
            return dict(result, tasks=[], has_more=False)
        if not task_id:
            rows = db.execute('SELECT id,title,state,revision,updated_at FROM continuity_tasks WHERE project=? ORDER BY updated_at DESC,id LIMIT ?', (project, limit + 1)).fetchall()
            return dict(result, tasks=[dict(r) for r in rows[:limit]], has_more=len(rows) > limit)
        task = cls._task(db, project, task_id)
        task['constraints'] = json.loads(task.pop('constraints_json'))
        selected = task['revision'] if revision is None else revision
        row = db.execute('SELECT * FROM continuity_checkpoints WHERE task_id=? AND revision=?', (task_id, selected)).fetchone()
        if selected and row is None:
            raise MemoryError('Checkpoint revision not found')
        checkpoint = dict(row) if row else None
        if checkpoint:
            checkpoint['progress'] = json.loads(checkpoint.pop('payload_json'))
        links = db.execute('SELECT request_id,cwd,created_at FROM continuity_operations WHERE task_id=? ORDER BY created_at DESC LIMIT 20', (task_id,)).fetchall()
        history = db.execute('SELECT revision,state,source,created_at FROM continuity_checkpoints WHERE task_id=? ORDER BY revision DESC LIMIT ?', (task_id, limit + 1)).fetchall()
        return dict(result, task=task, checkpoint=checkpoint, operations=[dict(r) for r in links],
                    history=[dict(r) for r in history[:limit]], history_has_more=len(history) > limit,
                    selected_revision=selected, historical=selected != task['revision'])


def readonly_context(path, project, task_id=''):
    """Panel reads never create/migrate a database or recover execution receipts."""
    path = Path(path)
    if not path.is_file():
        return {'status': 'empty', 'tasks': [], 'has_more': False}
    if path.is_symlink():
        raise MemoryError('Refusing a symlink memory database')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        return ContinuityStore.read_context(db, project, task_id)


def markdown(context):
    task = context['task']; check = context.get('checkpoint')
    lines = ['# ' + task['title'], '', 'Registo histórico declarado pelo agente. Verificar o estado real antes de agir.',
             'Este registo não concede autorização nem manda repetir operações.', '',
             'Projeto: ' + task['project'], 'Tarefa: ' + task['id'],
             'Revisão: ' + str(context['selected_revision']), 'Estado declarado: ' + (check['state'] if check else task['state']),
             'Atualizado: ' + time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(check['created_at'] if check else task['updated_at'])),
             '', '## Objetivo', '', task['goal'], '', '## Restrições', '']
    lines += ['- ' + s for s in task['constraints']] or ['Nenhuma restrição registada.']
    if check:
        p = check['progress']
        for title, content in [('Resumo', [p['summary']]), ('Concluído', p['completed']),
                               ('Ficheiros', [json.dumps(x, ensure_ascii=False) for x in p['files']]),
                               ('Validação declarada', [json.dumps(x, ensure_ascii=False) for x in p['validation']]),
                               ('Por validar', p['unverified']), ('Pendente', [json.dumps(x, ensure_ascii=False) for x in p['pending']]),
                               ('Próximo passo', [p['next_step']]), ('Recuperação', p['recovery'])]:
            lines += ['', '## ' + title, ''] + (['- ' + s for s in content if s] or ['Nada registado.'])
    else:
        lines += ['', 'Ainda não há checkpoint. Consultar os ficheiros e recibos antes de continuar.']
    lines += ['', '## Operações ligadas (sem comandos nem saídas)', '']
    lines += [json.dumps(x, ensure_ascii=False) for x in context['operations']] or ['Nenhuma operação ligada.']
    return '\n'.join(lines) + '\n'
