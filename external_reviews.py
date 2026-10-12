"""Optional, task-scoped external opinions. Never test receipts or implementation agents.

One durable job per model, launched by a dedicated worker outside the MCP lifetime.
Only explicitly zero-cost, currently listed OpenCode models are eligible. No replay.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.request
import uuid

from operation_store import identity

PURPOSES = ('plan', 'code', 'tests', 'visual', 'question')
STRATEGIES = ('critique', 'independent', 'compare', 'verify')
ACTIVE = ('queued', 'running')
RECOMMENDED = {'plan':['opencode/muse-spark-1.3-contributor-free'],
 'code':['opencode/mimo-v2.6-flash-free'], 'tests':['opencode/mimo-v2.6-flash-free'],
 'visual':['opencode/muse-spark-1.3-contributor-free'], 'question':['opencode/mimo-v2.6-flash-free']}
DEFAULT = {'enabled':False, 'mode':'automatic', 'stages':['plan','code'],
 'models':RECOMMENDED, 'concurrency':3, 'limit':0, 'timeout':600, 'tools':'plan', 'context_budget_bytes':524288}
TERMINAL_DECISIONS = {'corrected','rejected','out_of_scope'}


def canonical(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(value): return hashlib.sha256(canonical(value).encode()).hexdigest()
def root_directory():
    return Path(os.environ.get('BRIDGE_REVIEWS_DIR',Path.home()/'.local/state/bridge-companion/external-reviews'))


def system_environment():
    runtime='/run/user/'+str(os.getuid())
    return {'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':str(Path.home()),'XDG_RUNTIME_DIR':runtime,
            'DBUS_SESSION_BUS_ADDRESS':'unix:path='+runtime+'/bus'}


def validate_policy(value):
    if not isinstance(value,dict) or set(value)-set(DEFAULT): raise ValueError('Invalid reviewer settings.')
    p=json.loads(canonical(DEFAULT));p.update(value)
    if type(p['enabled']) is not bool or p['mode'] not in {'manual','automatic','required'}: raise ValueError('Choose Manual, Automatic or Required.')
    if p['tools'] not in {'context','snapshots','plan'}:raise ValueError('Choose supplied context, selected snapshots or Plan project tools.')
    if not isinstance(p['stages'],list) or len(set(p['stages']))!=len(p['stages']) or any(x not in PURPOSES for x in p['stages']):raise ValueError('Invalid review stages.')
    if p['enabled'] and p['mode']=='required' and not p['stages']:raise ValueError('Required needs at least one stage.')
    if not isinstance(p['models'],dict) or set(p['models'])!=set(PURPOSES):raise ValueError('Choose models for each review purpose.')
    for purpose, models in p['models'].items():
        if not isinstance(models,list) or len(models)>8 or len(set(models))!=len(models):raise ValueError('Choose up to 8 distinct models per purpose.')
        for m in models:
            if not isinstance(m,str) or not re.fullmatch(r'opencode/[a-zA-Z0-9_.-]{1,120}',m) or m.startswith('opencode/jev'):raise ValueError('Only OpenCode prose reviewers are supported.')
        if p['enabled'] and purpose in p['stages'] and not models:raise ValueError('Selected stages need at least one model.')
    for name,low,high in [('concurrency',1,8),('limit',0,10000),('timeout',30,3600),('context_budget_bytes',32768,2097152)]:
        if type(p[name]) is not int or not low<=p[name]<=high:raise ValueError('Invalid '+name+'. Limit 0 means no consultation cap.')
    return p


def fetch_json(url):
    request=urllib.request.Request(url,headers={'User-Agent':'BridgeReview/0.13'})
    with urllib.request.urlopen(request,timeout=20) as response:
        data=response.read(12*1024*1024+1)
    if len(data)>12*1024*1024:raise ValueError('Model catalogue exceeds limit.')
    return json.loads(data)


def free_catalogue(metadata,live):
    listed={m['id'] for m in live.get('data',[])}; result=[]
    for model,m in metadata.get('opencode',{}).get('models',{}).items():
        cost=m.get('cost',{})
        # Unknown cost, cache costs, deprecated and structured Jev models are excluded.
        if model not in listed or model.startswith('jev') or cost.get('input')!=0 or cost.get('output')!=0 or any(v!=0 for k,v in cost.items() if k in {'cache_read','cache_write'}):continue
        result.append({'id':'opencode/'+model,'name':m.get('name',model),'free':True,
          'inputs':m.get('modalities',{}).get('input',['text']), 'context':m.get('limit',{}).get('context',0),
          'capability_source':'models.dev/opencode', 'vision_tested':False})
    return sorted(result,key=lambda x:x['name'])


def instructions(policy):
    if not policy['enabled']:return ''
    stages=', '.join(policy['stages'])
    access=('External models review a disposable project copy using the native OpenCode Plan tools and permissions, including diagnostic commands and tests. '
      'They are instructed to analyze without implementing fixes or changing the original project. '
      'Native Plan edit restrictions are not a filesystem sandbox; command access must not be described as technically read-only. '
      if policy['tools']=='plan' else
      'External models only analyze supplied context and selected read-only snapshots; they cannot edit or run commands. ')
    return ('EXTERNAL REVIEWERS: '+policy['mode'].upper()+'. Selected stages: '+stages+'. '
      'ChatGPT remains the implementer and decision maker. '+access+
      'Use external_review_request for an evidence-based question, with purpose, reason, strategy, selected files/excerpts and exact task_id. Goal, criteria, active user instructions and the exact latest checkpoint are attached automatically; a bounded Git diff is attached when a valid base is available. Review the supplied context and omission notices. '
      'For consequential planning, request plan review before implementation; simple incidental decisions need no consultation. '
      'For independent alternatives, omit your proposed solution and ask for an independent approach. '
      'For final code/tests/visual stages, include the actual relevant code, checks and rendered images, then assess the findings. '
      'Consult external_review_status for durable results; running jobs survive a disconnected tool client. Do not resubmit uncertain jobs. '
      'Use external_review_decision to record your evaluation and evidence for every finding, including a reasoned rejection where appropriate. '
      'An opinion is not a passed test. Verify substantive findings and recheck fixes. For meaningful command checks pass validation_paths to run_command to bind the recorded check to the monitored files/directories automatically. For browser/manual checks use validation_evidence begin before inspection and finish after, then cite [validation:ID] in checkpoint evidence. Choose relevant source/assets/configuration scopes; source freshness does not prove coverage or aesthetics. A changed file can make a review stale: use new context, not an old opinion as current verification. '
      'Reuse fresh reviews in the final review; request another only for a changed question or new evidence. No automatic implementation or scope expansion. '+
      ('Manual mode: consult only when the user requests it. ' if policy['mode']=='manual' else
       'Automatic mode: choose worthwhile consultations and explain the specific justification. ' if policy['mode']=='automatic' else
       'Required mode: every selected model for each required stage must return a real, evaluated review before formal completion. If unavailable, report the unmet review rather than inventing it. '))


class ReviewStore:
    def __init__(self,directory=None,config=None):
        self.root=Path(directory or root_directory());self.config=config or {}
        self._version_cache={}
        if self.root.is_symlink():raise ValueError('Symlink review directory refused.')
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.path=self.root/'reviews.sqlite3'
        if self.path.is_symlink():raise ValueError('Symlink review database refused.')
        with self.connection() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS policies(project TEXT,scope TEXT,version INTEGER,payload TEXT,PRIMARY KEY(project,scope));
              CREATE TABLE IF NOT EXISTS catalogue(id INTEGER PRIMARY KEY CHECK(id=1),updated REAL,payload TEXT);
              CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,project TEXT,task TEXT,request_id TEXT,model TEXT,purpose TEXT,strategy TEXT,
                fingerprint TEXT,policy_version INTEGER,packet TEXT,state TEXT,unit TEXT,owner_pid INTEGER,owner_birth TEXT,
                created REAL,started REAL,finished REAL,result TEXT,error TEXT,decision TEXT,UNIQUE(project,task,request_id,model));
              CREATE TABLE IF NOT EXISTS vision_probes(model TEXT PRIMARY KEY,updated REAL);''')
        os.chmod(self.path,0o600)

    @contextmanager
    def connection(self):
        db=sqlite3.connect(self.path,timeout=5);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    def policy(self,project,scope):
        with self.connection() as db:r=db.execute('SELECT * FROM policies WHERE project=? AND scope=?',(str(project),scope)).fetchone()
        return {'version':r['version'] if r else 0,'settings':validate_policy(json.loads(r['payload'])) if r else validate_policy({})}

    def configure(self,project,scope,value,expected_version):
        return self.configure_many(project,[(scope,value,expected_version)])[0]

    def configure_many(self,project,changes):
        values=[(scope,validate_policy(value),expected) for scope,value,expected in changes];results=[]
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for scope,policy,expected in values:
                r=db.execute('SELECT * FROM policies WHERE project=? AND scope=?',(str(project),scope)).fetchone();v=r['version'] if r else 0
                if type(expected) is not int or expected!=v:raise ValueError('Reviewer settings changed. Refresh before saving.')
                version=v if r and json.loads(r['payload'])==policy else v+1
                db.execute('INSERT OR REPLACE INTO policies VALUES(?,?,?,?)',(str(project),scope,version,canonical(policy)))
                results.append({'version':version,'settings':policy})
        return results

    def inherit(self,project,source,task):
        p=self.policy(project,source)
        if p['version'] or p['settings']['enabled']:return self.configure(project,task,p['settings'],0)
        return p

    def catalogue(self,refresh=False):
        with self.connection() as db:r=db.execute('SELECT * FROM catalogue').fetchone()
        if not refresh and r:
            result=json.loads(r['payload']);result['stale']=time.time()-r['updated']>3600
        else:
            try:
                models=free_catalogue(fetch_json('https://models.dev/api.json'),fetch_json('https://opencode.ai/zen/v1/models'))
                if not models:raise ValueError('No zero-cost models confirmed.')
                result={'models':models,'updated':time.time(),'stale':False,'sources':['https://models.dev/api.json','https://opencode.ai/zen/v1/models']}
                with self.connection() as db:db.execute('INSERT OR REPLACE INTO catalogue VALUES(1,?,?)',(result['updated'],canonical(result)))
            except Exception as e:
                if not r:raise ValueError('Model catalogue unavailable; no model request was sent.') from e
                result=json.loads(r['payload']);result.update(stale=True,error='Refresh failed; calls are disabled until zero-cost status can be checked.')
        with self.connection() as db:probes={r['model'] for r in db.execute('SELECT * FROM vision_probes')}
        for m in result['models']:m['vision_tested']=m['id'] in probes
        result['recommended']=RECOMMENDED
        return result

    def requirement_hash(self,project,task):
        # The coordinator's goal + criteria + subsequent instructions identify scope.
        path=Path(os.environ.get('BRIDGE_COMPANION_DIR',Path.home()/'.local/state/bridge-companion'))/'state.sqlite3'
        if not path.is_file() or path.is_symlink():return ''
        db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)
        try:
            row=db.execute('SELECT goal,criteria_json FROM goal_loops WHERE work_task_id=? AND work_project=?',(task,str(project))).fetchone()
            if not row:return ''
            updates=db.execute("SELECT id,revision,text FROM task_updates WHERE task_id=? AND state!='cancelled' ORDER BY id",(task,)).fetchall()
            return digest([list(row),[list(x) for x in updates]])
        finally:db.close()

    def packet(self,project,task,purpose,question,reason,strategy,context,files,images,diff_base='',budget=None):
        if purpose not in PURPOSES or strategy not in STRATEGIES:raise ValueError('Invalid review purpose or strategy.')
        for label,value,limit in [('question',question,4000),('reason',reason,1500),('context',context,500000)]:
            if not isinstance(value,str) or len(value)>limit or (label!='context' and not value.strip()):raise ValueError('Invalid '+label+'.')
        if not isinstance(files,list) or len(files)>40 or not isinstance(images,list) or len(images)>4:raise ValueError('At most 40 selected files and 4 images.')
        if purpose=='visual' and not images:raise ValueError('Visual review requires an actual rendered image.')
        project=Path(project).resolve();snapshots=[]; attachments=[]
        for item in files:
            if not isinstance(item,dict) or set(item)-{'path','start_line','end_line'}:raise ValueError('Files need path and optional start_line/end_line.')
            path=self.selected_path(project,item.get('path',''))
            data=self.file_bytes(project,path)
            if len(data)>2*1024*1024:raise ValueError('Select a smaller file or supply a concise excerpt as context.')
            lines=data.decode('utf-8').splitlines(keepends=True);start=item.get('start_line',1);end=item.get('end_line',len(lines))
            if type(start) is not int or type(end) is not int or start<1 or end<start or end>len(lines):raise ValueError('Invalid file line range.')
            content=''.join(lines[start-1:end]);snapshots.append({'path':str(path.relative_to(project)),'sha256':hashlib.sha256(data).hexdigest(),'start_line':start,'end_line':end,'content':content})
        for item in images:
            path=self.selected_path(project,item)
            data=self.file_bytes(project,path)
            if len(data)>8*1024*1024 or not (data.startswith(b'\x89PNG\r\n\x1a\n') or data.startswith(b'\xff\xd8\xff')):raise ValueError('Use a PNG or JPEG under 8 MB.')
            attachments.append({'path':str(path.relative_to(project)),'sha256':hashlib.sha256(data).hexdigest(),'image':True})
        packet={'project':str(project),'task_id':task,'purpose':purpose,'question':question.strip(),'reason':reason.strip(),'strategy':strategy,
            'context':context,'files':snapshots,'images':attachments,'requirements_hash':self.requirement_hash(project,task)}
        from review_context import automatic_context, git_diff
        packet['task_context']=automatic_context(project,task,self.config,strategy=='independent')
        if not isinstance(diff_base,str) or len(diff_base)>200:raise ValueError('Invalid Git base.')
        packet['diff']=git_diff(project,[x['path'] for x in snapshots],diff_base,config=self.config) if strategy!='independent' else {'available':False,'reason':'Independent strategy omits the existing diff to avoid anchoring.'}
        budget=budget or DEFAULT['context_budget_bytes']
        packet['context_budget_bytes']=budget
        if len(canonical(packet).encode())>budget:
            packet['diff']={'available':False,'reason':'Diff omitted to fit the configured context budget. Request focused ranges or a larger budget.'}
        size=len(canonical(packet).encode())
        if size>budget:raise ValueError('Review context needs '+str(size)+' bytes; configured budget is '+str(budget)+'. Narrow the inline text/file ranges or increase Context budget in Workspace. No model call sent.')
        return packet

    def selected_path(self,project,name):
        path=Path(name)
        if not path.is_absolute():path=project/path
        if path.is_symlink() or not path.resolve().is_relative_to(project):raise ValueError('Review files must be regular files inside this project.')
        path=path.resolve()
        for forbidden in self.config.get('filesystem',{}).get('denied_paths',[]):
            if path.is_relative_to(Path(forbidden).expanduser().resolve()):raise ValueError('Path is denied by Bridge filesystem policy.')
        if not path.is_file() or path.name.startswith('.env') or path.suffix.lower() in {'.pem','.key'} or path.name in {'auth.json','credentials.json'}:raise ValueError('Not an eligible review file.')
        return path

    def file_bytes(self,project,path):
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            actual=Path('/proc/self/fd/'+str(fd)).resolve();self.selected_path(Path(project),str(actual))
            metadata=os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size>8*1024*1024:raise ValueError('Not an eligible bounded regular file.')
            with os.fdopen(fd,'rb',closefd=False) as source:return source.read(8*1024*1024+1)
        finally:os.close(fd)

    def current_hash(self,project,path):
        selected=self.selected_path(Path(project),path);s=selected.stat()
        key=(str(selected),s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        if key not in self._version_cache:self._version_cache[key]=hashlib.sha256(self.file_bytes(project,selected)).hexdigest()
        return self._version_cache[key]

    def stale(self,row):
        packet=json.loads(row['packet']);policy=self.policy(row['project'],row['task'])
        if policy['version']!=row['policy_version']:return 'Reviewer policy changed.'
        if packet['requirements_hash']!=self.requirement_hash(row['project'],row['task']):return 'Task instructions changed.'
        # A reviewed planning choice remains historical evidence after implementation.
        if row['purpose']=='plan' and row['decision']:return ''
        result=json.loads(row['result']) if row['result'] else {}
        for entry in packet['files']+packet['images']+result.get('project_snapshot',[]):
            try:version=self.current_hash(row['project'],entry['path'])
            except (OSError,ValueError):return 'A selected file is unavailable.'
            if version!=entry['sha256']:return 'A selected file changed: '+entry['path']
        return ''

    def _recover(self,db):
        for r in db.execute("SELECT * FROM jobs WHERE state IN ('queued','running')").fetchall():
            if r['owner_pid'] and identity(r['owner_pid'])!=r['owner_birth']:
                db.execute("UPDATE jobs SET state='uncertain',error='Worker unavailable. No automatic retry.',finished=? WHERE id=?",(time.time(),r['id']))
            elif not r['owner_pid'] and time.time()-r['created']>30:
                db.execute("UPDATE jobs SET state='uncertain',error='Worker start not confirmed. No automatic retry.',finished=? WHERE id=?",(time.time(),r['id']))

    def request(self,project,task,request_id,purpose,question,reason,strategy='critique',context='',files=None,images=None,models=None,repeat=False,launch=True,diff_base=''):
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{8,160}',request_id):raise ValueError('Supply a stable review request_id.')
        policy=self.policy(project,task);p=policy['settings']
        if not p['enabled']:raise ValueError('External reviewers are off for this task.')
        if purpose not in PURPOSES:raise ValueError('Invalid review purpose.')
        models=models if models is not None else p['models'][purpose]
        input_hash=digest([str(project),task,purpose,question,reason,strategy,context,files or [],images or [],models,repeat,diff_base])
        with self.connection() as db:
            prior=db.execute('SELECT * FROM jobs WHERE project=? AND task=? AND request_id=?',(str(project),task,request_id)).fetchall()
        if prior and all(json.loads(x['packet']).get('request_parameters_hash')==input_hash for x in prior):
            return self.status(project,task,request_id=request_id)
        packet=self.packet(project,task,purpose,question,reason,strategy,context,files or [],images or [],diff_base,p['context_budget_bytes'])
        packet['request_parameters_hash']=input_hash
        if not models or len(models)>8 or len(set(models))!=len(models):raise ValueError('Choose distinct review models.')
        if any(x not in p['models'][purpose] for x in models):raise ValueError('Select this model for the review purpose in Workspace first.')
        if len(canonical(packet).encode())>p['context_budget_bytes']:raise ValueError('Review context exceeds the configured budget after metadata. Nothing sent.')
        fingerprint=digest([{k:v for k,v in packet.items() if k!='request_parameters_hash'},models,policy['version']])
        with self.connection() as db:
            prior=db.execute('SELECT * FROM jobs WHERE project=? AND task=? AND request_id=?',(str(project),task,request_id)).fetchall()
        if prior:
            if any(x['fingerprint']!=fingerprint for x in prior):raise ValueError('Review request_id already has different parameters. Inspect the existing job.')
            return self.status(project,task,request_id=request_id)
        cat=self.catalogue();cat=self.catalogue(True) if cat['stale'] else cat
        if cat['stale']:raise ValueError('Current zero-cost status unavailable; no request sent.')
        by_id={m['id']:m for m in cat['models']}
        for m in models:
            if m not in by_id:raise ValueError('Selected model is unavailable or no longer confirmed free: '+m)
            if packet['images'] and 'image' not in by_id[m]['inputs']:raise ValueError('Selected model has no documented image support: '+m)
        now=time.time();new=[]
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE');self._recover(db)
            prior=db.execute('SELECT * FROM jobs WHERE project=? AND task=? AND request_id=?',(str(project),task,request_id)).fetchall()
            if prior:
                if any(x['fingerprint']!=fingerprint for x in prior):raise ValueError('Review request_id already has different parameters.')
                return self._status_db(db,project,task,request_id)
            if db.execute("SELECT 1 FROM jobs WHERE project=? AND task=? AND fingerprint=? AND state='uncertain' LIMIT 1",(str(project),task,fingerprint)).fetchone():
                raise ValueError('A matching consultation is uncertain. Explicitly exclude that attempt before requesting another opinion.')
            if not repeat:
                matches=db.execute("SELECT * FROM jobs WHERE project=? AND task=? AND fingerprint=? AND state IN ('queued','running','completed') ORDER BY created DESC",(str(project),task,fingerprint)).fetchall()
                same=next((r for r in matches if not self.stale(r)),None)
                if same:return {'reused':True,**self._status_db(db,project,task,same['request_id'])}
            used=db.execute('SELECT count(*) FROM jobs WHERE project=? AND task=?',(str(project),task)).fetchone()[0]
            if p['limit'] and used+len(models)>p['limit']:raise ValueError('The configured consultation limit would be exceeded.')
            for model in models:
                jid=uuid.uuid4().hex;unit='bridge-review-'+jid+'.service'
                db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (jid,str(project),task,request_id,model,purpose,strategy,fingerprint,policy['version'],canonical(packet),'queued',unit,None,'',now,None,None,'','',''))
                new.append((jid,unit))
        if launch:
            for jid,unit in new:
                try:
                    r=subprocess.run(['/usr/bin/systemd-run','--user','--quiet','--collect','--unit='+unit,'--service-type=exec',
                      '--property=KillMode=control-group','--property=TimeoutStopSec=3','--property=UMask=0077',
                      '--property=StandardOutput=null','--property=StandardError=null',sys.executable,'-B',
                      str(Path(__file__).with_name('external_review_worker.py')),str(self.root),jid],
                      env=system_environment(),capture_output=True,timeout=12)
                    if r.returncode:raise ValueError('Worker launch not confirmed.')
                except Exception:
                    with self.connection() as db:db.execute("UPDATE jobs SET state='uncertain',error='Worker launch not confirmed. No automatic retry.' WHERE id=? AND state='queued'",(jid,))
        return self.status(project,task,request_id=request_id)

    def _status_db(self,db,project,task,request_id='',job_id='',include_result=False,limit=50):
        rows=db.execute('SELECT * FROM jobs WHERE project=? AND task=? AND (?=\'\' OR request_id=?) AND (?=\'\' OR id=?) ORDER BY created DESC,id LIMIT ?',
                        (str(project),task,request_id,request_id,job_id,job_id,limit+1)).fetchall()
        jobs=[]
        for row in rows[:limit]:
            r={k:row[k] for k in ('id','request_id','model','purpose','strategy','state','created','started','finished','error','policy_version')}
            r['stale_reason']=self.stale(row);r['decision']=json.loads(row['decision']) if row['decision'] else None
            result=json.loads(row['result']) if row['result'] else None
            r['findings']=result.get('findings',[]) if result else []
            r['summary']=result.get('summary','') if result else ''
            if include_result:
                r['result']={k:v for k,v in result.items() if k!='project_snapshot'} if result else None
                if result and 'project_snapshot' in result:r['result']['project_snapshot_files']=len(result['project_snapshot']);r['result']['snapshot_versions_retained']=True
                r['packet']=json.loads(row['packet'])
            else:
                r['summary']=r['summary'][:1200]
                r['findings_omitted']=max(0,len(r['findings'])-5)
                r['findings']=[{k:(v[:800] if isinstance(v,str) else v) for k,v in f.items()} for f in r['findings'][:5]]
                if r['decision']:
                    r['decision']={**r['decision'],'summary':r['decision']['summary'][:1200],
                        'findings':r['decision']['findings'][:5]}
                r['detail_available']=True
            jobs.append(r)
        return {'jobs':jobs,'has_more':len(rows)>limit,'automatic_retry':False,'opinion_not_test_evidence':True}

    def detail_page(self,project,task,job_id,offset=0,version='',limit=24000):
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1000<=limit<=48000:raise ValueError('Invalid detail cursor.')
        value=self.status(project,task,job_id=job_id,include_result=True,limit=1)
        if not value['jobs']:raise ValueError('Review not found for this task.')
        job=value['jobs'][0]
        # Dynamic staleness metadata must not invalidate an immutable body cursor.
        body=canonical({k:job[k] for k in ('result','packet','decision')})
        token=digest(body)
        if offset and version!=token:raise ValueError('Review detail changed. Reload the full response.')
        if offset>len(body):raise ValueError('Invalid detail cursor.')
        end=min(len(body),offset+limit)
        return {'id':job_id,'text':body[offset:end],'version':token,'next_offset':end if end<len(body) else None,
                'total_characters':len(body),'stale_reason':job['stale_reason'],'opinion_not_test_evidence':True}

    def status(self,project,task,request_id='',job_id='',include_result=False,limit=50):
        with self.connection() as db:
            self._recover(db)
            return self._status_db(db,project,task,request_id,job_id,include_result,max(1,min(limit,100)))

    def decision(self,project,task,job_id,summary,findings):
        if not isinstance(summary,str) or not 10<=len(summary)<=4000:raise ValueError('Record a concrete evaluation summary.')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM jobs WHERE id=? AND project=? AND task=?',(job_id,str(project),task)).fetchone()
            if not row or row['state']!='completed':raise ValueError('Only a completed provider response can be evaluated.')
            stale=self.stale(row)
            if stale:raise ValueError('Review is stale: '+stale+' Request a fresh review.')
            expected={f['id'] for f in json.loads(row['result'])['findings']}
            if not isinstance(findings,list) or len(findings)!=len(expected) or {f.get('id') for f in findings}!=expected:raise ValueError('Evaluate every returned finding by its exact ID.')
            for f in findings:
                if set(f)!={'id','disposition','reason','evidence'} or f['disposition'] not in TERMINAL_DECISIONS|{'needs_context'}:raise ValueError('Invalid finding assessment.')
                if not isinstance(f['reason'],str) or not 10<=len(f['reason'])<=2000 or not isinstance(f['evidence'],str) or len(f['evidence'])>2000:raise ValueError('Explain the finding assessment.')
                if f['disposition'] in {'corrected','rejected'} and not f['evidence'].strip():raise ValueError('A correction or rejection needs evidence.')
            value={'summary':summary,'findings':findings,'evaluated_at':time.time()}
            db.execute('UPDATE jobs SET decision=? WHERE id=?',(canonical(value),job_id))
        return self.status(project,task,job_id=job_id)

    def cancel(self,project,task,job_id):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM jobs WHERE id=? AND project=? AND task=?',(job_id,str(project),task)).fetchone()
            if not row:raise ValueError('Unknown review job.')
            if row['state'] in ACTIVE or row['state']=='uncertain':db.execute("UPDATE jobs SET state='cancelled',finished=? WHERE id=?",(time.time(),job_id))
        if row['state'] in ACTIVE and row['owner_pid']:
            subprocess.run(['/usr/bin/systemctl','--user','stop',row['unit']],env=system_environment(),capture_output=True,timeout=10)
        return self.status(project,task,job_id=job_id)

    def task_summary(self,project,task):
        policy=self.policy(project,task)
        if not policy['settings']['enabled']:return None
        with self.connection() as db:
            self._recover(db)
            rows=db.execute('SELECT * FROM jobs WHERE project=? AND task=? ORDER BY created DESC',(str(project),task)).fetchall()
        missing=[];waiting=any(r['state'] in ACTIVE for r in rows)
        if policy['settings']['mode']=='required':
            for stage in policy['settings']['stages']:
                for model in policy['settings']['models'][stage]:
                    valid=any(r['purpose']==stage and r['model']==model and r['state']=='completed' and r['decision'] and not self.stale(r) and
                              all(f['disposition'] in TERMINAL_DECISIONS for f in json.loads(r['decision'])['findings']) for r in rows)
                    if not valid:missing.append(stage+' · '+model)
        return {'policy':policy,'waiting':waiting,'missing':missing,
            'issue':('Required external review is unmet: '+', '.join(missing)) if missing else '',
            **self.status(project,task,limit=30)}


def optional_summary(project,task,config=None):
    # Off uses no worker, network or prompt addition. Avoid creating a store on reads.
    if not (root_directory()/'reviews.sqlite3').is_file():return None
    return ReviewStore(config=config).task_summary(project,task)
