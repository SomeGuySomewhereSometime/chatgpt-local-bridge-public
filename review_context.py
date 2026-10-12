from contextlib import closing
"""Task-specific review material. Read-only, bounded, never reserves update delivery."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile

from continuity import readonly_context


def automatic_context(project, task, config, independent=False):
    project=str(Path(project).resolve())
    result={'project':project,'task_id':task,'untrusted_context':True,'checkpoint':None,'active_instructions':[], 'notices':[]}
    path=Path(os.environ.get('BRIDGE_COMPANION_DIR',Path.home()/'.local/state/bridge-companion'))/'state.sqlite3'
    if path.is_file() and not path.is_symlink():
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            row=db.execute('SELECT goal,criteria_json FROM goal_loops WHERE work_task_id=? AND work_project=? ORDER BY started DESC LIMIT 1',(task,project)).fetchone()
            if row:
                result.update(goal=row['goal'],criteria=json.loads(row['criteria_json']))
                result['active_instructions']=[dict(r) for r in db.execute("SELECT id,revision,text,state FROM task_updates WHERE task_id=? AND state!='cancelled' ORDER BY created,id",(task,))]
    settings=config.get('memory',{})
    path=Path(settings.get('path','~/.local/state/chatgpt-local-bridge-NOBUBBLEWARP/memory/memory.sqlite3')).expanduser().absolute()
    if settings.get('enabled') and path.is_file():
        context=readonly_context(path,project,task)
        t=context.get('task')
        if t:
            result.setdefault('goal',t['goal']);result.setdefault('criteria',[])
            result['constraints']=t['constraints'];result['checkpoint_revision']=context['selected_revision']
            checkpoint=context.get('checkpoint')
            if independent:
                result['notices'].append('Independent strategy: prior implementation/plan checkpoint content omitted to avoid anchoring.')
            elif checkpoint:
                result['checkpoint']={k:checkpoint[k] for k in ('revision','state','created_at','source','progress')}
    if 'goal' not in result:result['notices'].append('No exact task goal record available. Supply necessary requirements explicitly; no other task was substituted.')
    return result


def git_diff(project, paths, base='', limit=48000,config=None):
    """HEAD is a stated baseline, never assumed to be the start of this task."""
    env={'PATH':'/usr/bin:/bin','HOME':'/nonexistent','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_TERMINAL_PROMPT':'0','GIT_OPTIONAL_LOCKS':'0','LC_ALL':'C.UTF-8'}
    def run(args):
        return subprocess.run(['/usr/bin/git','--no-pager','-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false',*args],cwd=project,env=env,capture_output=True,timeout=5)
    try:
        root=run(['rev-parse','--show-toplevel'])
        if root.returncode or Path(root.stdout.decode().strip()).resolve()!=Path(project).resolve():
            return {'available':False,'reason':'No Git repository rooted at this project.'}
        revision=run(['rev-parse','--verify','--end-of-options',(base or 'HEAD')+'^{commit}'])
        if revision.returncode:return {'available':False,'reason':'No valid commit baseline.'}
        commit=revision.stdout.decode().strip()
        # Discover names first so tracked credentials/dependencies cannot enter an automatic diff.
        with tempfile.TemporaryFile() as names:
            listed=subprocess.run(['/usr/bin/git','-c','core.fsmonitor=false','diff','--name-only','-z',commit,'--',*(paths or ['.'])],cwd=project,env=env,stdout=names,stderr=subprocess.DEVNULL,timeout=5)
            if listed.returncode:return {'available':False,'reason':'Changed file list unavailable.'}
            size_names=names.tell();names.seek(0);raw_names=names.read(200000)
        if size_names>200000:return {'available':False,'reason':'Changed file list too large; select explicit ranges.'}
        excluded={'.git','.venv','venv','node_modules','__pycache__','.cache','dist','build','coverage','.next','.opencode'}
        denied=[Path(p).expanduser().resolve() for p in (config or {}).get('filesystem',{}).get('denied_paths',[])]
        safe=[];omitted=0
        for raw_name in raw_names.split(b'\0'):
            if not raw_name:continue
            name=raw_name.decode('utf-8','replace');path=Path(name);actual=Path(project)/path
            if (any(x in excluded for x in path.parts) or path.name.startswith('.env') or path.suffix.lower() in {'.key','.pem'} or path.name in {'auth.json','credentials.json','opencode.json','opencode.jsonc'} or actual.is_symlink() or not actual.resolve().is_relative_to(Path(project).resolve()) or any(actual.resolve().is_relative_to(p) for p in denied)):
                omitted+=1;continue
            safe.append(name)
        if len(safe)>1000:return {'available':False,'reason':'More than 1,000 changed files; select explicit paths.'}
        if not safe:return {'available':True,'base_commit':commit,'baseline':'Explicit base' if base else 'Current HEAD; not a task-start snapshot.', 'text':'','paths':[],'truncated':False,'omitted_bytes':0,'excluded_files':omitted,'untracked_files_included':False}
        with tempfile.TemporaryFile() as out:
            diff=subprocess.run(['/usr/bin/git','--no-pager','-c','core.fsmonitor=false','-c','core.hooksPath=/dev/null','diff','--no-ext-diff','--no-textconv','--unified=3',commit,'--',*safe],cwd=project,env=env,stdout=out,stderr=subprocess.DEVNULL,timeout=5)
            if diff.returncode:return {'available':False,'reason':'Git diff unavailable.'}
            size=out.tell();out.seek(0);raw=out.read(limit)

        return {'available':True,'base_commit':commit,'baseline':'Explicit base' if base else 'Current HEAD; may include earlier changes, not a task-start snapshot.',
                'paths':safe,'excluded_files':omitted,'text':raw[:limit].decode('utf-8','replace'),'truncated':size>limit,
                'omitted_bytes':max(0,size-limit),'untracked_files_included':False}
    except (OSError,ValueError,subprocess.TimeoutExpired):return {'available':False,'reason':'Git baseline or diff unavailable.'}
