"""Task-scoped delivery in existing MCP results; no chat messages or tool dispatch.

SQLite reservations serialize with the coordinator. Returning a result is NOT delivery
proof: only an explicit marker in a committed model checkpoint acknowledges receipt.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid

from . import task_updates

MARKER = re.compile(r'\[BRIDGE_UPDATE:([A-Za-z0-9-]{16,80}):(\d+):(bridge-update-[a-f0-9]{32})\]')


def directory():
    return Path(os.environ.get('BRIDGE_COMPANION_DIR', Path.home()/'.local/state/bridge-companion'))


def connect(root=None):
    root=Path(root or directory());path=root/'state.sqlite3'
    if root.is_symlink() or path.is_symlink():raise ValueError('Refusing a symlink companion database.')
    if not path.is_file():return None
    db=sqlite3.connect(path.as_uri()+'?mode=rw',uri=True,timeout=2)
    db.row_factory=sqlite3.Row
    return db


def project_task(memory_path, project, task_id, revision=None):
    path=Path(memory_path)
    if not path.is_file() or path.is_symlink():return None
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
        db.row_factory=sqlite3.Row
        task=db.execute('SELECT * FROM continuity_tasks WHERE id=? AND project=?',(task_id,project)).fetchone()
        if not task:return None
        selected=task['revision'] if revision is None else revision
        cp=db.execute('SELECT payload_json FROM continuity_checkpoints WHERE task_id=? AND revision=?',(task_id,selected)).fetchone()
        return {'revision':selected,'progress':json.loads(cp['payload_json']) if cp else None}


def operation_task(memory_path, operations_path, request_id='', cwd='', session_id=''):
    """Infer only a unique retained task/operation link. Never recover a receipt."""
    memory_path=Path(memory_path);operations_path=Path(operations_path)
    if memory_path.is_symlink() or not memory_path.is_file():return None
    if session_id:
        if operations_path.is_symlink() or not operations_path.is_file():return None
        with closing(sqlite3.connect(operations_path.as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
            rows=db.execute('SELECT request_id,scope FROM requests WHERE session_id=?',(session_id,)).fetchall()
        if len(rows)!=1:return None
        request_id,cwd=rows[0]
    with closing(sqlite3.connect(memory_path.as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
        rows=db.execute('SELECT t.project,t.id FROM continuity_operations o JOIN continuity_tasks t ON t.id=o.task_id WHERE o.request_id=? AND o.cwd=?',(request_id,str(Path(cwd).resolve()))).fetchall()
    return tuple(rows[0]) if len(rows)==1 else None


def acknowledge(db, task_id, checkpoint):
    if not checkpoint or not checkpoint['progress']:return
    # Use immutable committed checkpoint contents, never caller-supplied text.
    entries=checkpoint['progress'].get('completed',[])
    for text in entries:
        for uid,version,rid in MARKER.findall(text):
            row=db.execute('SELECT * FROM task_updates WHERE id=? AND task_id=?',(uid,task_id)).fetchone()
            if not row or row['revision']!=int(version) or row['request_id']!=rid or row['delivery_channel']!='bridge':continue
            if row['state']!='uncertain' or checkpoint['revision']<=row['revision_floor']:continue
            db.execute("UPDATE task_updates SET state='sent',confirmed_by='bridge_checkpoint',ack_revision=?,updated=? WHERE id=?",(checkpoint['revision'],time.time(),uid))
            db.execute("UPDATE update_deliveries SET state='bridge_acknowledged',ack_revision=? WHERE id=? AND revision=? AND request_id=?",(checkpoint['revision'],uid,int(version),rid))
            link=db.execute('SELECT conversation FROM workspace_tasks WHERE task_id=?',(task_id,)).fetchone()
            db.execute('INSERT INTO events(conversation,created,kind,state,detail) VALUES(?,?,?,?,?)',(link['conversation'],time.time(),'update_bridge_ack','sent','Bridge update acknowledged in checkpoint revision '+str(checkpoint['revision'])+'. Application remains model-reported.'))


def attach(result, project, task_id, memory_path, *, root=None, checkpoint_revision=None, operation=''):
    if not task_id or not isinstance(result,dict):return result
    db=None
    try:
        project=str(Path(project).resolve());db=connect(root)
        if db is None:return result
        with db:
            db.execute('BEGIN IMMEDIATE')
            if 'mode' not in {r[1] for r in db.execute('PRAGMA table_info(task_updates)')}:return result
            link=db.execute('SELECT * FROM workspace_tasks WHERE task_id=? AND project=?',(task_id,project)).fetchone()
            if not link:return result
            loop=db.execute('SELECT * FROM goal_loops WHERE conversation=? AND work_task_id=?',(link['conversation'],task_id)).fetchone()
            binding=db.execute('SELECT * FROM bindings WHERE conversation=?',(link['conversation'],)).fetchone()
            if not loop or not binding or binding['project']!=project or binding['task']!=link['reference']:return result
            cp=project_task(memory_path,project,task_id,checkpoint_revision)
            if not cp:return result
            if checkpoint_revision is not None:acknowledge(db,task_id,cp)
            rows=task_updates.listing(db,task_id)
            unresolved=[r for r in rows if r['state']=='uncertain']
            if unresolved:
                payload={'status':'awaiting_acknowledgement','task_id':task_id,
                    'delivery_ids':list(dict.fromkeys(r['request_id'] for r in unresolved)),
                    'instructions':'A reserved update is awaiting confirmation. Read current task_context with this exact project and task_id to retrieve the original Bridge delivery text and markers. Reading the same delivery does not create a new instruction or repeat any operation. Check what you already applied; do not replay commands. Acknowledge each exact BRIDGE_UPDATE marker in completed when saving task_checkpoint and report how it was handled. No chat fallback is sent while delivery is uncertain. Retrieving this instruction does not restart automatic chat messages or expand authority. A stopped loop does not cancel the current response or revoke already authorized work; an explicit user stop or a real tool rejection must still be respected.'}
                if operation=='task_context':
                    retained=[]
                    for row in unresolved:
                        if row['delivery_channel']!='bridge':continue
                        original=db.execute("SELECT text FROM update_deliveries WHERE id=? AND revision=? AND request_id=? AND delivery_channel='bridge'",(row['id'],row['revision'],row['request_id'])).fetchone()
                        if original:
                            retained.append({'id':row['id'],'revision':row['revision'],'text':original['text'],
                                'acknowledgement':f"[BRIDGE_UPDATE:{row['id']}:{row['revision']}:{row['request_id']}]"})
                    if retained:
                        payload.update(updates=retained,retrieved_existing_delivery=True,loop_state=loop['state'])
                return {**result,'workspace_updates':payload}
            # Stop, explicit resume and the existing deadline retain control. No
            # heartbeat refresh and no semantic guess that an operation has finished.
            if loop['state']!='waiting':return result
            queued=[r for r in rows if r['state']=='queued' and r['mode']=='automatic']
            if not queued:return result
            rid='bridge-update-'+uuid.uuid4().hex;now=time.time()
            updates=[]
            for row in queued:
                marker=f"[BRIDGE_UPDATE:{row['id']}:{row['revision']}:{rid}]"
                updates.append({'id':row['id'],'revision':row['revision'],'text':row['text'],'acknowledgement':marker})
                db.execute("UPDATE task_updates SET state='uncertain',request_id=?,revision_floor=?,delivery_channel='bridge',updated=? WHERE id=? AND state='queued'",(rid,cp['revision'],now,row['id']))
                db.execute("INSERT INTO update_deliveries(id,revision,request_id,state,text,created,delivery_channel) VALUES(?,?,?,?,?,?,?)",(row['id'],row['revision'],rid,'uncertain',row['text'],now,'bridge'))
            payload={'status':'offered_via_bridge','task_id':task_id,'delivery_id':rid,'operation':operation,'updates':updates,
                'instructions':'These are later user instructions for this exact task, delivered inside this tool result, not a new chat message. Later instructions can correct earlier ones within authorized scope; formal criteria and tool approvals remain unchanged. A command may still be running: do not interrupt or replay it merely because an update arrived. Apply the update at a suitable boundary, or use NEEDS_USER if scope, safety or criteria conflict. Read the latest task_context; if the task was completed and more authorized work is required, save an in_progress checkpoint before new mutations. In your next task_checkpoint, put each exact acknowledgement marker in a completed entry with a concise account of how you handled that update. The marker acknowledges receipt; a report is not independent proof of application. Returning this result alone is not confirmed delivery.'}
            db.execute('INSERT INTO events(conversation,created,kind,state,detail) VALUES(?,?,?,?,?)',(link['conversation'],now,'update_bridge_offer','uncertain','Automatic update reserved in a Bridge result; awaiting model checkpoint acknowledgement. No chat message sent.'))
            return {**result,'workspace_updates':payload}
    except (ValueError,OSError,sqlite3.Error) as exc:
        # Preserve the original command/checkpoint result even if queue metadata
        # is unavailable. Never invite re-execution of a successful operation.
        return {**result,'workspace_updates':{'status':'unavailable','instructions':'Update delivery metadata is unavailable. Preserve the original tool result; do not replay commands or assume an update was applied. Inspect Workspace before further changes.','detail':str(exc)[:160]}}
    finally:
        if db is not None:db.close()
