"""Task-scoped instructions. Both delivery routes serialize through the same SQLite transaction."""
import json
import re
import time


def setup(db):
    schema='''CREATE TABLE IF NOT EXISTS task_updates(
      id TEXT PRIMARY KEY, task_id TEXT NOT NULL, text TEXT NOT NULL, revision INTEGER NOT NULL,
      state TEXT NOT NULL, request_id TEXT NOT NULL, revision_floor INTEGER NOT NULL,
      confirmed_by TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS update_versions(
      id TEXT NOT NULL, revision INTEGER NOT NULL, text TEXT NOT NULL, created REAL NOT NULL,
      PRIMARY KEY(id,revision));
    CREATE TABLE IF NOT EXISTS update_deliveries(
      id TEXT NOT NULL, revision INTEGER NOT NULL, request_id TEXT NOT NULL, state TEXT NOT NULL,
      text TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(id,revision,request_id));'''
    for statement in schema.split(';'):
        if statement.strip():db.execute(statement)
    for table,definitions in {
        'task_updates':{'mode':"TEXT NOT NULL DEFAULT 'next_reply'",'delivery_channel':"TEXT NOT NULL DEFAULT 'chat'",'ack_revision':"INTEGER NOT NULL DEFAULT -1"},
        'update_versions':{'mode':"TEXT NOT NULL DEFAULT 'next_reply'"},
        'update_deliveries':{'delivery_channel':"TEXT NOT NULL DEFAULT 'chat'",'ack_revision':"INTEGER NOT NULL DEFAULT -1"}
    }.items():
        columns={r[1] for r in db.execute('PRAGMA table_info('+table+')')}
        for name,definition in definitions.items():
            if name not in columns:db.execute('ALTER TABLE '+table+' ADD COLUMN '+name+' '+definition)


def mode(value):
    if value not in {'automatic','next_reply'}:raise ValueError('Choose Automatic or Next response for update delivery.')
    return value


def listing(db, task_id):
    return [dict(r) for r in db.execute('SELECT * FROM task_updates WHERE task_id=? ORDER BY created,id',(task_id,))]


def validate_text(db, task_id, value, replacing=''):
    if not isinstance(value,str) or not value.strip() or len(value.strip())>2000:
        raise ValueError('Write an update of 1 to 2,000 characters.')
    value=value.strip()
    rows=[r for r in listing(db,task_id) if r['state']!='cancelled' and r['id']!=replacing]
    if len(rows)>=20 or sum(len(r['text']) for r in rows)+len(value)>8000:
        raise ValueError('This task supports 20 retained instructions and 8,000 characters. Start a new reviewed task for a larger scope.')
    return value


def mutate(db, task_id, p, active):
    action=p['action']; uid=p.get('id','')
    if not isinstance(uid,str) or not re.fullmatch(r'[A-Za-z0-9-]{16,80}',uid):
        raise ValueError('Invalid update ID.')
    row=db.execute('SELECT * FROM task_updates WHERE id=?',(uid,)).fetchone()
    if action=='update_add':
        if row:
            original=db.execute('SELECT text,mode FROM update_versions WHERE id=? AND revision=1',(uid,)).fetchone()
            if row['task_id']!=task_id or not isinstance(p.get('text'),str) or original['text']!=p['text'].strip() or original['mode']!=mode(p.get('mode','next_reply')):
                raise ValueError('This update ID was already used for different content.')
            return  # Idempotent even if the original add response was lost.
        if db.execute('SELECT count(*) FROM task_updates WHERE task_id=?',(task_id,)).fetchone()[0]>=100:
            raise ValueError('Update history capacity reached. Start a new reviewed task.')
        value=validate_text(db,task_id,p.get('text'));now=time.time()
        db.execute('INSERT INTO task_updates(id,task_id,text,revision,state,request_id,revision_floor,confirmed_by,created,updated,mode) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(uid,task_id,value,1,'queued','',-1,'',now,now,mode(p.get('mode','next_reply'))))
        db.execute('INSERT INTO update_versions(id,revision,text,created,mode) VALUES(?,?,?,?,?)',(uid,1,value,now,mode(p.get('mode','next_reply'))))
        return
    if not row or row['task_id']!=task_id:raise ValueError('This update does not belong to the selected task.')
    if type(p.get('revision')) is not int or p['revision']!=row['revision']:
        raise ValueError('The update changed. Refresh before editing it.')
    if action in {'update_edit','update_cancel'}:
        if row['state']!='queued':raise ValueError('Only queued updates can be edited or cancelled. Add a new correction instead.')
        if action=='update_edit':
            value=validate_text(db,task_id,p.get('text'),uid);version=row['revision']+1;now=time.time()
            delivery_mode=mode(p.get('mode',row['mode']))
            db.execute('UPDATE task_updates SET text=?,revision=?,updated=?,mode=? WHERE id=?',(value,version,now,delivery_mode,uid))
            db.execute('INSERT INTO update_versions(id,revision,text,created,mode) VALUES(?,?,?,?,?)',(uid,version,value,now,delivery_mode))
        else:db.execute("UPDATE task_updates SET state='cancelled',updated=? WHERE id=?",(time.time(),uid))
    elif action=='update_reconcile':
        if active:raise ValueError('Stop the loop before resolving an uncertain update.')
        if row['state']!='uncertain' or p.get('resolution') not in {'found','exclude'}:
            raise ValueError('Inspect the conversation, then mark this uncertain update as found or exclude it from future context.')
        state='sent' if p['resolution']=='found' else 'cancelled'
        db.execute('UPDATE task_updates SET state=?,confirmed_by=?,updated=? WHERE id=?',(state,'user' if state=='sent' else 'excluded',time.time(),uid))
    else:raise ValueError('Unknown update action.')


def context(db, task_id):
    rows=[r for r in listing(db,task_id) if r['state']!='cancelled']
    if any(r['state']=='uncertain' for r in rows):
        raise ValueError('An update has uncertain delivery. Inspect ChatGPT and resolve it in Updates for this task before resuming.')
    if not rows:return ''
    values=[{'id':r['id'],'revision':r['revision'],'text':r['text'],'delivery':'new instruction' if r['state']=='queued' else 'previously sent instruction'} for r in rows]
    return ('USER UPDATES FOR THIS TASK\nThese are later user instructions, in order. Apply them within the reviewed project and task. '
            'Later instructions can correct earlier ones. They do not change the formal completion criteria or approve restricted tools. '
            'If scope or criteria conflict, explain and use NEEDS_USER. Report how the updates were addressed in your next checkpoint. '
            'Repeated instructions preserve context; never repeat operations merely because an instruction appears again.\n'+json.dumps(values,ensure_ascii=False))


def reserve(db, task_id, request_id, revision_floor):
    now=time.time()
    for row in listing(db,task_id):
        if row['state']=='queued':
            db.execute("UPDATE task_updates SET state='uncertain',request_id=?,revision_floor=?,delivery_channel='chat',updated=? WHERE id=? AND state='queued'",(request_id,revision_floor,now,row['id']))
            db.execute('INSERT INTO update_deliveries(id,revision,request_id,state,text,created) VALUES(?,?,?,?,?,?)',(row['id'],row['revision'],request_id,'uncertain',row['text'],now))


def delivery(db, request_id, state):
    if state=='observed_sent':
        db.execute("UPDATE update_deliveries SET state='observed_sent' WHERE request_id=?",(request_id,))
        db.execute("UPDATE task_updates SET state='sent',confirmed_by='receipt',updated=? WHERE request_id=? AND state='uncertain'",(time.time(),request_id))


def floor(rows):
    return max((r['revision_floor'] for r in rows if r['state']=='sent'),default=-1)
