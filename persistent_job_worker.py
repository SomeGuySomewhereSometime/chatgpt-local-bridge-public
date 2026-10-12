"""One-shot durable job owner. Never restart/replay a command automatically."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from operation_store import OperationStore, identity
from persistent_jobs import atomic_json, read_json, boot_id, lock, MAX_STREAM


def main(directory):
    os.umask(0o077)
    with lock(directory/'.worker.lock'):
        # Permanent marker prevents duplicate dispatch even after a worker crash.
        if (directory/'started.json').exists(): return 125
        request=read_json(directory/'request.json')
        meta=read_json(directory/'meta.json')
        store=OperationStore(request['operations'])
        gate=store.gate()
        cancelled=threading.Event()
        signal.signal(signal.SIGTERM,lambda *_:cancelled.set())
        signal.signal(signal.SIGINT,lambda *_:cancelled.set())
        state=dict(meta,execution_state='running',boot_id=boot_id(),
                   owner_pid=os.getpid(),owner_birth=identity(os.getpid()),returncode=None)
        from memory_store import MemoryService
        if request.get('memory_disabled'): os.environ['BRIDGE_MEMORY_DISABLED']='1'
        memory=None
        history=None
        process=None
        readers=[]
        validation_store=None;validation_id='';validation=request.get('validation')
        validation_config={'memory':request.get('memory_config',{}),'filesystem':request.get('filesystem',{}),'operations':{'directory':request['operations']},'validation':{'directory':validation['directory']} if validation else {}}
        try:
            memory=MemoryService({'memory':request.get('memory_config',{}),'filesystem':request.get('filesystem',{})})
            atomic_json(directory/'started.json',{'at':time.time()})
            if request['request_id']:
                store.transfer(request['request_id'],meta['session_id'])
                store.progress(request['request_id'], {'persistent':True,'session_id':meta['session_id']})
            atomic_json(directory/'state.json',state)
            os.close(gate); gate=None
            history=memory.begin(meta['cwd'])
            if validation:
                try:
                    from validation_evidence import ValidationStore
                    validation_store=ValidationStore(validation_config)
                    check=validation_store.begin(validation['project'],validation['task'],request['request_id'],validation['paths'])
                    validation_id=check['id'];state['validation_reference']=check['reference']
                except Exception:state['validation_warning']='Source snapshot unavailable; command execution is separate and will not be replayed.'
            process=subprocess.Popen(['/bin/bash','--noprofile','--norc','-c',request['command']],
                cwd=meta['cwd'],env=request['environment'],stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
            # Command/env only needed for dispatch. Retain output and metadata, not credentials.
            (directory/'request.json').unlink()
            def drain(stream, pipe):
                written=0
                try:
                    with open(directory/(stream+'.bin'),'wb',buffering=0) as output:
                        while True:
                            data=os.read(pipe.fileno(),16384)
                            if not data: break
                            chunk=data[:max(0,MAX_STREAM-written)]
                            try:
                                output.write(chunk); written+=len(chunk)
                            except OSError:
                                state['receipt_warning']='Output storage failed; output may be incomplete'
                            if len(chunk)<len(data): state[stream+'_truncated']=True
                        os.fsync(output.fileno())
                except OSError:
                    state['receipt_warning']='Output storage failed; output may be incomplete'
                    # Continue draining so failed storage cannot deadlock a command.
                    try:
                        while os.read(pipe.fileno(),16384): pass
                    except OSError: pass
                finally: pipe.close()
            for name,pipe in (('stdout',process.stdout),('stderr',process.stderr)):
                t=threading.Thread(target=drain,args=(name,pipe),daemon=True);t.start();readers.append(t)
            deadline=time.monotonic()+request['timeout'] if request['timeout'] else float('inf')
            while process.poll() is None:
                timed_out=time.monotonic()>=deadline
                if cancelled.is_set() or (directory/'cancel.json').exists() or timed_out:
                    state['timed_out']=timed_out
                    state['execution_state']='timed_out' if timed_out else 'cancelled'
                    try: os.killpg(process.pid,signal.SIGTERM)
                    except ProcessLookupError: pass
                    try: process.wait(timeout=.6)
                    except subprocess.TimeoutExpired:
                        try: os.killpg(process.pid,signal.SIGKILL)
                        except ProcessLookupError: pass
                        process.wait(timeout=1)
                    break
                time.sleep(.025)
            state['returncode']=process.wait()
            if state['execution_state']=='running':
                state['execution_state']='cancelled' if cancelled.is_set() or (directory/'cancel.json').exists() else 'completed' if process.returncode==0 else 'failed'
        except BaseException as exc:
            state.update(execution_state='unknown',receipt_warning='Job interrupted: '+type(exc).__name__)
        finally:
            if process:
                try: os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError: pass
                for t in readers:t.join(.3)
                if any(t.is_alive() for t in readers):
                    # systemd cleans escaped descendants on service exit; receipt remains conservative.
                    state.update(execution_state='unknown',receipt_warning='Descendant output still open; inspect unit cleanup')
            if state.get('receipt_warning'): state['execution_state']='unknown'
            state['finished_at']=time.time()
            if history is not None:
                try: memory.finish(meta['cwd'],history,state.get('returncode'),state.get('timed_out',False),state['execution_state']=='unknown')
                except Exception: state['history_warning']='Command history update failed; command was not replayed'
            if request['request_id']:
                try:store.finish(request['request_id'],state.get('returncode'),state.get('timed_out',False),state['execution_state']=='unknown')
                except Exception:state.update(execution_state='unknown',receipt_warning='Receipt update failed; do not replay')
            if validation_store and validation_id:
                try:
                    validation_store.finish(validation['project'],validation['task'],validation_id,'Command validation of selected scopes',
                        'passed' if state['execution_state']=='completed' and state.get('returncode')==0 else 'incomplete' if state['execution_state']=='unknown' else 'failed',
                        request['request_id'],meta['cwd'])
                except Exception:state['validation_warning']='Final source snapshot/evidence unavailable. Inspect the existing command; never repeat it for metadata.'
            atomic_json(directory/'state.json',state)
            if gate is not None: os.close(gate)
    return 0


if __name__=='__main__':
    raise SystemExit(main(Path(sys.argv[1])))
