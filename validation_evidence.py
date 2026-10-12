"""Version-bound check records. File freshness is observed; coverage is never inferred."""
from contextlib import contextmanager, closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time

from operation_store import configured_directory

EXCLUDED={'.git','.venv','venv','node_modules','__pycache__','.cache','dist','build','coverage','.next','.opencode'}
MAX_FILES=2000
MAX_BYTES=100*1024*1024


def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()
def directory(config):return Path(os.environ.get('BRIDGE_VALIDATION_DIR',config.get('validation',{}).get('directory',configured_directory(config)/'validations')))


def source_snapshot(project,paths,config):
    project=Path(project).resolve()
    if not isinstance(paths,list) or not 1<=len(paths)<=40 or any(not isinstance(p,str) or len(p)>1000 for p in paths):
        raise ValueError('Choose 1..40 relevant project files/directories for validation.')
    scopes=[];versions={};total=0;excluded=0
    denied=[Path(p).expanduser().resolve() for p in config.get('filesystem',{}).get('denied_paths',[])]
    def eligible(path):
        return not any(part in EXCLUDED for part in path.relative_to(project).parts) and not path.name.startswith('.env') and path.suffix.lower() not in {'.key','.pem'} and path.name not in {'auth.json','credentials.json'} and not any(path.resolve().is_relative_to(p) for p in denied)
    def inspect(path):
        nonlocal total,excluded
        if str(path.relative_to(project)) in versions:return
        if path.is_symlink() or not eligible(path):excluded+=1;return
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            before=os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):raise ValueError('Validation scope contains a non-regular file.')
            if before.st_size>20*1024*1024 or total+before.st_size>MAX_BYTES or len(versions)>=MAX_FILES:
                raise ValueError('Validation scope exceeds 2,000 files/100 MB or a file exceeds 20 MB. Narrow the scope; no partial evidence accepted.')
            total+=before.st_size;hasher=hashlib.sha256()
            while True:
                data=os.read(fd,65536)
                if not data:break
                hasher.update(data)
            after=os.fstat(fd)
            if (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):raise ValueError('A monitored file changed while capturing its version.')
            versions[str(path.relative_to(project))]=hasher.hexdigest()
        finally:os.close(fd)
    for name in sorted(set(paths)):
        raw=Path(name);raw=raw if raw.is_absolute() else project/raw
        if any(p.is_symlink() for p in [raw,*raw.parents] if p!=project) or not raw.resolve().is_relative_to(project):raise ValueError('Validation scopes must be inside this project without symlinks.')
        path=raw.resolve()
        if not eligible(path):raise ValueError('Validation scope is excluded or denied.')
        scopes.append(str(path.relative_to(project)))
        if path.is_file():inspect(path)
        elif path.is_dir():
            for current,dirs,files in os.walk(path,followlinks=False):
                dirs[:]=sorted(d for d in dirs if d not in EXCLUDED and not (Path(current)/d).is_symlink() and eligible(Path(current)/d))
                for f in sorted(files):
                    child=Path(current)/f
                    if str(child.relative_to(project)) not in versions:inspect(child)
        else:raise ValueError('A validation scope is unavailable: '+name)
    if not versions:raise ValueError('Validation scope has no eligible monitored files.')
    return {'paths':scopes,'files':versions,'excluded_files':excluded,
            'coverage_notice':'Monitored scopes only. Dependencies/generated folders, credentials and symlinks excluded. Versions do not establish test coverage, browser truth or aesthetics.'}


class ValidationStore:
    def __init__(self,config=None):
        self.config=config or {};self.root=directory(self.config)
        if self.root.is_symlink():raise ValueError('Symlink evidence directory refused.')
        self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.path=self.root/'evidence.sqlite3'
        if self.path.is_symlink():raise ValueError('Symlink evidence database refused.')
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS checks(id TEXT PRIMARY KEY,project TEXT,task TEXT,signature TEXT,before_json TEXT,after_json TEXT,description TEXT,status TEXT,receipt TEXT,receipt_cwd TEXT,created REAL,finished REAL)')
        os.chmod(self.path,0o600)

    @contextmanager
    def connection(self):
        db=sqlite3.connect(self.path,timeout=5);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    def begin(self,project,task,request_id,paths):
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{8,160}',request_id):raise ValueError('Supply a stable validation request_id.')
        project=str(Path(project).resolve());signature=digest([project,task,paths]);key=digest([project,task,request_id])[:32]
        with self.connection() as db:
            old=db.execute('SELECT * FROM checks WHERE id=?',(key,)).fetchone()
        if old:
            if old['signature']!=signature:raise ValueError('Validation request_id already used with different scopes.')
            return self.status(project,task,key)
        before=source_snapshot(project,paths,self.config)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM checks WHERE id=?',(key,)).fetchone()
            if old and old['signature']!=signature:raise ValueError('Validation request_id already used with different scopes.')
            if not old and db.execute('SELECT count(*) FROM checks WHERE project=?',(project,)).fetchone()[0]>=2000:raise ValueError('Validation history capacity reached for this project; retain/export existing evidence before adding more.')
            db.execute('INSERT OR IGNORE INTO checks VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(key,project,task,signature,canonical(before),'','','running','','',time.time(),None))
        return self.status(project,task,key)

    def finish(self,project,task,key,description,status,receipt='',receipt_cwd=''):
        if status not in {'passed','failed','incomplete'} or not isinstance(description,str) or not 1<=len(description)<=1000:raise ValueError('Supply check description and passed/failed/incomplete status.')
        with self.connection() as db:row=db.execute('SELECT * FROM checks WHERE id=? AND project=? AND task=?',(key,str(Path(project).resolve()),task)).fetchone()
        if not row:raise ValueError('Validation not found in this exact task.')
        if row['finished']:
            if [description,status,receipt,receipt_cwd]!=[row['description'],row['status'],row['receipt'],row['receipt_cwd']]:raise ValueError('Finished validation is immutable. Record a new check.')
            return self.status(project,task,key)
        before=json.loads(row['before_json']);after=source_snapshot(project,before['paths'],self.config)
        # Commands cannot be attached to another task or an unrelated cwd.
        if receipt:
            from continuity import readonly_context
            path=Path(self.config.get('memory',{}).get('path','')).expanduser().absolute()
            context=readonly_context(path,str(Path(project).resolve()),task)
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
                linked=db.execute('SELECT 1 FROM continuity_operations WHERE task_id=? AND request_id=? AND cwd=?',(task,receipt,receipt_cwd)).fetchone()
            if not context.get('task') or not linked:raise ValueError('Command receipt is not linked to this task.')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT * FROM checks WHERE id=?',(key,)).fetchone()
            if current['finished']:
                if [description,status,receipt,receipt_cwd]!=[current['description'],current['status'],current['receipt'],current['receipt_cwd']]:raise ValueError('Finished validation is immutable.')
            else:db.execute('UPDATE checks SET after_json=?,description=?,status=?,receipt=?,receipt_cwd=?,finished=? WHERE id=?',(canonical(after),description,status,receipt,receipt_cwd,time.time(),key))
        return self.status(project,task,key)

    def status(self,project,task,key):
        with self.connection() as db:row=db.execute('SELECT * FROM checks WHERE id=? AND project=? AND task=?',(key,str(Path(project).resolve()),task)).fetchone()
        if not row:raise ValueError('Validation not found in this exact task.')
        before=json.loads(row['before_json']);after=json.loads(row['after_json']) if row['after_json'] else None
        result={'id':key,'description':row['description'],'status':row['status'],'reference':'[validation:'+key+']','paths':before['paths'],'files_monitored':len(before['files']),
            'coverage_notice':before['coverage_notice'],'created':row['created'],'finished':row['finished'],'receipt_id':row['receipt'],'receipt_confirmed':False,
            'freshness':'unfinished','changed_paths':[],'detail':'Validation has not finished.'}
        if not after:return result
        def changed(a,b):return [p for p in sorted(set(a)|set(b)) if a.get(p)!=b.get(p)]
        during=changed(before['files'],after['files'])
        try:current=source_snapshot(project,before['paths'],self.config);later=changed(after['files'],current['files'])
        except (OSError,ValueError):later=['A monitored scope is unavailable.']
        result['changed_paths']=(during or later)[:20];result['changes_omitted']=max(0,len(during or later)-20)
        result['freshness']='changed_during_check' if during else 'stale' if later else 'current'
        result['detail']='Source changed during this check.' if during else 'Monitored source changed after this check.' if later else 'Monitored file versions are unchanged. Coverage and reported check outcomes still need assessment.'
        if row['status']!='passed':result['detail']='Recorded check status: '+row['status']+'. '+result['detail']
        if row['receipt']:
            try:
                path=configured_directory(self.config)/'requests.sqlite3'
                if path.is_symlink():raise ValueError('Symlink receipt refused.')
                with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
                    db.row_factory=sqlite3.Row
                    value=db.execute('SELECT * FROM requests WHERE request_id=? AND scope=?',(row['receipt'],row['receipt_cwd'])).fetchone()
                    result['receipt_confirmed']=bool(value and value['operation']=='run_command' and value['state']=='completed' and value['returncode']==0 and not value['timed_out'])
            except (ValueError,OSError,sqlite3.Error):pass
            if not result['receipt_confirmed']:result['detail']+=' Referenced command has no successful completion.'
        result['outcome_basis']='command_receipt' if row['receipt'] else 'assistant_report'
        return result


def referenced_checks(config,project,task,checks):
    refs=list(dict.fromkeys(ref for check in checks for ref in re.findall(r'\[validation:([a-f0-9]{32})\]',check.get('evidence',''))))
    if not refs:return {}
    result={}
    if not (directory(config)/'evidence.sqlite3').is_file():return {key:{'id':key,'freshness':'unavailable','status':'incomplete','detail':'Validation record unavailable.'} for key in refs}
    store=ValidationStore(config)
    for key in refs:
        try:result[key]=store.status(project,task,key)
        except (ValueError,OSError,sqlite3.Error):result[key]={'id':key,'freshness':'unavailable','status':'incomplete','detail':'Validation record unavailable for this task.'}
    return result
