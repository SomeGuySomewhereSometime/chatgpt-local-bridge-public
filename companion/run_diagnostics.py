"""Bounded metadata only. Diagnostic failures never retry or change agent actions."""
import json
import platform
import re
import sqlite3
import time

VERSION = '0.14.0'
PER_RUN = 150
TOTAL = 1500
RUNS = 100
RETENTION = 7 * 86400
STATES = {'idle','completed','interrupted','generating','draft','approval','error','unknown','disconnected','waiting','dispatching','stopped','limit_reached','review_required','updates_pending','reconnect_required','observed_sent','uncertain','pending','cancelled'}
ACTIONS = {'verify_delivery','reconnect','connection_status','bind','unbind','prepare','claim','result','observe','loop_start','loop_tick','loop_validate','loop_result','loop_stop','work_save','work_resume_preview','update_add','update_edit','update_cancel','update_reconcile','status','diagnostics_set'}
CODES = {'ok','rejected','native_transport','native_rejected','page_changed','draft_preserved','send_unavailable','context_invalidated','permission_denied','delivery_text_mismatch','identity_unavailable','count_mismatch','delivery_unconfirmed','content_transport'}

def ident(value):
    return value if isinstance(value,str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,100}',value) else ''

def error_code(error):
    text = str(error).lower()
    for needles,code in [(['context invalidated'],'context_invalidated'), (['text did not match','text could not be verified'],'delivery_text_mismatch'), (['identit'],'identity_unavailable'), (['count did not match'],'count_mismatch'), (['draft'],'draft_preserved'), (['send is unavailable'],'send_unavailable'), (['conversation changed','page changed'],'page_changed'), (['loop stopped','not allowed'],'permission_denied'), (['delivery','observation expired'],'delivery_unconfirmed')]:
        if any(n in text for n in needles): return code
    return 'rejected'

def metadata(value):
    if not isinstance(value,dict): return {}
    out={}
    for key in ('elapsed_ms','user_count','expected_users','used','max_steps','polls','text_length','observed_text_length','review_revision','review_completed_revision'):
        v=value.get(key)
        if type(v) in (int,float) and 0<=v<=10000000:out[key]=round(v,2)
    for key in ('terminal','identified','count_matches','text_matches','clicked','allowed','debug_enabled','final_review','review_started','bubble_expanded'):
        if type(value.get(key)) is bool:out[key]=value[key]
    for key in ('page_state','loop_state','delivery_state'):
        if isinstance(value.get(key),str) and value[key] in STATES:out[key]=value[key]
    if isinstance(value.get('code'),str) and value['code'] in CODES:out['code']=value['code']
    v=value.get('extension_version')
    if isinstance(v,str) and re.fullmatch(r'\d{1,3}\.\d{1,3}\.\d{1,3}',v):out['extension_version']=v
    if value.get('phase') in {'implementation','verification'}:out['phase']=value['phase']
    return out

def setup(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS diagnostic_preferences(conversation TEXT PRIMARY KEY,enabled INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS diagnostic_runs(run_id TEXT PRIMARY KEY,conversation TEXT NOT NULL,task_id TEXT NOT NULL,started REAL NOT NULL,enabled INTEGER NOT NULL,version TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS diagnostic_events(id INTEGER PRIMARY KEY,conversation TEXT NOT NULL,run_id TEXT NOT NULL,created REAL NOT NULL,action TEXT NOT NULL,code TEXT NOT NULL,request_id TEXT NOT NULL,meta TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS diagnostic_run_events ON diagnostic_events(run_id,id);''')

def current(db,cid):
    return db.execute('SELECT * FROM goal_loops WHERE conversation=?',(cid,)).fetchone()

def ensure_run(db,cid,row,enabled=False):
    if row:
        db.execute('INSERT OR IGNORE INTO diagnostic_runs VALUES(?,?,?,?,?,?)',(row['run_id'],cid,ident(row['work_task_id']),row['started'],int(enabled),VERSION))

def summary(db,cid):
    row=current(db,cid)
    if row and row['started']<time.time()-RETENTION:row=None
    saved=db.execute('SELECT * FROM diagnostic_runs WHERE run_id=?',(row['run_id'] if row else '',)).fetchone()
    pref=db.execute('SELECT enabled FROM diagnostic_preferences WHERE conversation=?',(cid,)).fetchone()
    return {'available':True,'enabled':bool(saved['enabled'] if saved and saved['started']>=time.time()-RETENTION else pref and pref['enabled']), 'run_id':row['run_id'] if row else '', 'runs':[dict(r) for r in db.execute('SELECT run_id,task_id,started,enabled,version FROM diagnostic_runs WHERE conversation=? AND started>=? ORDER BY started DESC LIMIT 20',(cid,time.time()-RETENTION))], 'limits':{'events_per_run':PER_RUN,'events_total':TOTAL,'retention_days':7}}

def mutate(db,cid,p):
    if type(p.get('enabled')) is not bool:raise ValueError('Choose debug on or off.')
    row=current(db,cid)
    if row and row['started']<time.time()-RETENTION:row=None
    if p.get('run_id','')!=(row['run_id'] if row else ''):raise ValueError('The run changed. Refresh before changing debug mode.')
    if row:
        ensure_run(db,cid,row)
        db.execute('UPDATE diagnostic_runs SET enabled=? WHERE run_id=?',(int(p['enabled']),row['run_id']))
    else:
        db.execute('INSERT OR REPLACE INTO diagnostic_preferences VALUES(?,?)',(cid,int(p['enabled'])))
    return summary(db,cid)

def prune(db,run_id):
    db.execute('DELETE FROM diagnostic_events WHERE created<?',(time.time()-RETENTION,))
    db.execute('DELETE FROM diagnostic_runs WHERE started<?',(time.time()-RETENTION,))
    db.execute('DELETE FROM diagnostic_events WHERE run_id=? AND id NOT IN (SELECT id FROM diagnostic_events WHERE run_id=? ORDER BY id DESC LIMIT ?)',(run_id,run_id,PER_RUN))
    db.execute('DELETE FROM diagnostic_events WHERE id NOT IN (SELECT id FROM diagnostic_events ORDER BY id DESC LIMIT ?)',(TOTAL,))
    db.execute('DELETE FROM diagnostic_runs WHERE run_id NOT IN (SELECT run_id FROM diagnostic_runs ORDER BY started DESC LIMIT ?)',(RUNS,))
    db.execute("DELETE FROM diagnostic_events WHERE run_id!='' AND run_id NOT IN (SELECT run_id FROM diagnostic_runs)")

def record(db,p,result=None,error=None,elapsed=0,changed=False):
    # Authentication and ownership apply even when the attempted action failed.
    match=re.fullmatch(r'https://chatgpt\.com/(?:g/[^/?#]+/)?c/([a-zA-Z0-9-]+)(?:[?#].*)?',str(p.get('url','')))
    if not match:return
    cid=match[1]
    b=db.execute('SELECT client,tab FROM bindings WHERE conversation=?',(cid,)).fetchone()
    if not b or b['client']!=p.get('client') or b['tab']!=p.get('tab'):return
    row=current(db,cid);action=p.get('action');result=result or {}
    if action not in ACTIONS:return
    if action=='loop_start' and result.get('send'):
        pref=db.execute('SELECT enabled FROM diagnostic_preferences WHERE conversation=?',(cid,)).fetchone()
        enabled=p.get('debug') if type(p.get('debug')) is bool else bool(pref and pref['enabled'])
        ensure_run(db,cid,row,enabled)
        db.execute('DELETE FROM diagnostic_preferences WHERE conversation=?',(cid,))
    ensure_run(db,cid,row)
    rid=row['run_id'] if row else ''
    saved=db.execute('SELECT enabled FROM diagnostic_runs WHERE run_id=?',(rid,)).fetchone()
    code=error_code(error) if error else 'ok'
    if p.get('state')=='uncertain':code=error_code(p.get('error','delivery'))
    if row and row['state']=='stopped' and changed and action in {'loop_tick','loop_result'}:code=error_code(row['reason'])
    essential=error is not None or code!='ok' or changed or action in {'loop_start','loop_result','loop_stop','claim','result','diagnostics_set'}
    if not essential and (not saved or not saved['enabled'] or action=='status'):return
    meta={'elapsed_ms':elapsed,'page_state':p.get('state'),**metadata(p.get('diagnostic_meta'))}
    if isinstance(p.get('snapshot'),dict):
        s=p['snapshot'];meta.update({'page_state':s.get('state'),'user_count':s.get('user_count'),'terminal':s.get('terminal')})
    if row:meta.update({'loop_state':row['state'],'used':row['used'],'max_steps':row['max_steps'],'final_review':bool(row['final_review']),'review_started':bool(row['review_started']),'review_revision':row['review_revision'],'review_completed_revision':row['review_completed_revision'],'phase':'verification' if row['review_started'] else 'implementation'})
    if action=='diagnostics_set':meta['debug_enabled']=p.get('enabled')
    if type(result.get('allowed')) is bool:meta['allowed']=result['allowed']
    db.execute('INSERT INTO diagnostic_events(conversation,run_id,created,action,code,request_id,meta) VALUES(?,?,?,?,?,?,?)',(cid,rid,time.time(),action,code,ident(p.get('id') or (result.get('send') or {}).get('id')),json.dumps(metadata(meta),separators=(',',':'))))
    prune(db,rid)

def export(db,cid,rid):
    run=db.execute('SELECT * FROM diagnostic_runs WHERE conversation=? AND run_id=? AND started>=?',(cid,rid,time.time()-RETENTION)).fetchone()
    if not run:raise ValueError('Choose a retained run to export.')
    rows=[{**{k:r[k] for k in ('created','action','code','request_id')},'metadata':json.loads(r['meta'])} for r in db.execute('SELECT * FROM diagnostic_events WHERE conversation=? AND run_id=? ORDER BY id',(cid,rid))]
    return {'schema':1,'exported_at':time.time(),'run':{k:run[k] for k in ('run_id','task_id','started','enabled','version')},'environment':{'coordinator_version':VERSION,'python':platform.python_version(),'sqlite':sqlite3.sqlite_version},'limits':{'events_per_run':PER_RUN,'events_total':TOTAL,'retention_days':7},'events':rows,'notice':'Metadata only. Events are bounded and may be incomplete. Debug is not proof of work quality. No automatic replay.'}
