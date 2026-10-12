"""Bounded goal loop. Called inside the coordinator's IMMEDIATE transaction."""
import json
import re
import time
import uuid
try:
    from . import task_updates
except ImportError:
    import task_updates

ACTIVE = {'dispatching', 'waiting', 'reconnect_required'}
BLOCKED = {'draft','approval','error','unknown','interrupted'}


def setup(db):
    db.execute('''CREATE TABLE IF NOT EXISTS goal_loops(
        conversation TEXT PRIMARY KEY, run_id TEXT NOT NULL, goal TEXT NOT NULL,
        max_steps INTEGER NOT NULL, used INTEGER NOT NULL, state TEXT NOT NULL,
        reason TEXT NOT NULL, nonce TEXT NOT NULL, document_id TEXT NOT NULL,
        expected_users INTEGER NOT NULL, request_id TEXT NOT NULL,
        last_seen REAL NOT NULL, started REAL NOT NULL)''')
    columns = {r[1] for r in db.execute('PRAGMA table_info(goal_loops)')}
    for name in ('work_task_id','criteria_json','work_project'):
        if name not in columns: db.execute(f"ALTER TABLE goal_loops ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
    for name, default in [('final_review',0),('review_started',0),('review_revision',-1),('review_completed_revision',-1)]:
        if name not in columns: db.execute(f'ALTER TABLE goal_loops ADD COLUMN {name} INTEGER NOT NULL DEFAULT {default}')
    if 'feedback_count' not in columns:
        db.execute('ALTER TABLE goal_loops ADD COLUMN feedback_count INTEGER NOT NULL DEFAULT 0')


def stop(db, cid, reason, state='stopped'):
    db.execute('UPDATE goal_loops SET state=?,reason=? WHERE conversation=? AND state IN (\'dispatching\',\'waiting\',\'reconnect_required\')', (state,reason,cid))


def status(db, cid):
    row = db.execute('SELECT * FROM goal_loops WHERE conversation=?', (cid,)).fetchone()
    if not row:
        return None
    if row['state'] in ACTIVE:
        if row['state']=='dispatching' and time.time()-row['last_seen'] > 20:
            # Never revive an in-flight delivery whose permission has expired.
            stop(db,cid,'Delivery observation expired. Check the conversation; no automatic retry.')
    result = dict(db.execute('SELECT * FROM goal_loops WHERE conversation=?',(cid,)).fetchone())
    # A throttled observer is not evidence that the running model was interrupted.
    # Only loop_tick with a fresh, fully validated snapshot can dispatch again.
    result['phase'] = 'verification' if result['review_started'] else 'implementation'
    result['observation_stale'] = result['state']=='waiting' and time.time()-result['last_seen'] > 20
    return result


def snapshot(p):
    s=p.get('snapshot',{})
    if not isinstance(s,dict) or not isinstance(s.get('document_id'),str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,80}',s['document_id']):
        raise ValueError('Reload the ChatGPT page to enable Goal / Loop.')
    n=s.get('user_count')
    if type(n) is not int or n < 0 or n > 100000:
        raise ValueError('Conversation messages could not be identified.')
    return s


def prompt(goal, nonce, initial, task_id='', checks=None, checkpoint_text='', project=''):
    prefix='Work toward this goal within the scope already authorized by the user:\n' if initial else 'Continue toward this same goal within the scope already authorized by the user:\n'
    work = ''
    if task_id:
        work = ('\n\nPROJECT ROOT: '+project+'\nBRIDGE WORKSPACE TASK: '+task_id+'\nCompletion criteria:\n'+('\n'.join('- '+x for x in (checks or [])) or 'Derive concrete criteria from the goal before implementation.')+
            '\nBefore changing files, inspect the project and available tools. If the Bridge is unavailable, use NEEDS_USER. '
            'Call get_session_context with this exact project root and task_id, then read task_context for this exact task. Use this task_id for Bridge run_command so operation receipts are linked. '
            'Automatic Workspace instructions may also arrive inside Bridge tool results during this response. Apply them at a suitable boundary; do not interrupt or replay running operations. If an update is awaiting acknowledgement but its text is missing, read current task_context for this exact task to retrieve the original delivery; do not repeat operations. Include each supplied BRIDGE_UPDATE acknowledgement marker in a completed checkpoint entry explaining how it was handled. If delivery or scope is uncertain, use NEEDS_USER. '
            'Before every final response save task_checkpoint with this task_id and the latest expected_revision. '
            'Include summary, completed, files, validation [{description,status,evidence}], unverified, pending and next_step. '
            'For each listed completion criterion, add one validation entry with description equal to that exact criterion and a concise evidence reference. '
            'For automated command checks, include [receipt:REQUEST_ID] in evidence using the actual linked Bridge run_command request_id. '
            'A receipt confirms command execution only; explain what it tested and whether later changes invalidate it. '
            'Browser/visual checks without a command receipt remain explicitly model-reported. '
            'If criteria were derived, report them as validation entries too. At least one check with an evidence reference is required for completion. '
            'Validation status is passed/failed/not_run/incomplete. Use state completed only when the criteria are met; otherwise in_progress or blocked. '
            'The checkpoint is a report, not independent proof. The coordinator will require a completed checkpoint before accepting DONE. '
            'Do not invent evidence or treat exit code zero alone as proof of visual quality.')
        if checkpoint_text: work += '\n\nREVIEWED RESUME CONTEXT:\n'+checkpoint_text
    quality = ('\n\nQUALITY APPROACH: Match the effort to the task. Before substantial work, identify the intended outcome, constraints and practical checks. '
        'For consequential choices, consider genuinely different alternatives and challenge the chosen plan for unsupported assumptions, missing requirements and unnecessary complexity. Simple tasks need no formal comparison. '
        'Review the actual result against the request, look for concrete defects or regressions, fix justified findings and recheck affected behavior. Do not invent criticism, add scope or repeat reviews without new evidence. '
        'For visual work, choose a coherent direction and inspect the rendered result for hierarchy, legibility and relevant screen sizes. '
        'Distinguish implemented work, executed checks and unverified aspects in the existing checkpoint and final report. No separate review document or numerical self-score is required. '
        'Before declaring the result ready to use, verify the user-facing path you will deliver in the relevant environment, proportionate to the task impact. Distinguish component checks from end-to-end checks. If testing used different conditions or the delivered path could not be verified, record that gap in the existing validation/unverified fields and qualify the completion claim. This instruction does not expand authority or permit disruptive testing. '
        'Treat optional future improvements separately from unmet requirements; do not turn them into additional mandatory work.')
    return (prefix + goal + work + quality + '\n\nCheck existing results and checkpoints before acting. Never repeat operations with unknown outcomes. '
        'Use appropriate tests and report their evidence. Stop and ask the user if approval, clarification or additional authority is needed. '
        'This coordinator cannot approve tools or expand your permissions. Automatic continuation state controls future chat messages only; it does not cancel the current response or revoke work already authorized by the user. There is no wall-clock run limit. If automatic continuation stops or page observation is lost, preserve results and continue the current authorized response unless the user requests a stop, a tool rejects the operation, or another real blocker exists. Do not treat loop_state=stopped alone as a tool rejection or NEEDS_USER blocker. '
        'At the very end of your final response, put exactly ONE of the following status lines, with no text after it. '
        'Use PENDING only if further authorized work remains and you can continue without the user; '
        'DONE only when the goal criteria are met (summarize validation first); '
        'NEEDS_USER for questions, approvals, blockers or uncertain results. '
        'Do not include the other status lines in your answer.\n'
        f'[BRIDGE_LOOP:{nonce}:PENDING]\n[BRIDGE_LOOP:{nonce}:DONE]\n[BRIDGE_LOOP:{nonce}:NEEDS_USER]')


def review_instruction(row):
    return ('FINAL REVIEW — same authorized task, no expansion of scope. '
        'Inspect the delivered result against the original goal AND subsequent user updates. '
        'Use the latest checkpoint and existing evidence. Reuse checks that remain valid; run focused checks for missing coverage or changes made since them. '
        'Check the actual user-facing path where relevant, including rendered visual results and relevant sizes for visual work. '
        'Do not invent defects, rewrite working code, add features or repeat broad testing without a concrete reason. '
        'If checks or fixes require operations on a completed task, first save an in_progress task_checkpoint with the current expected_revision and a concrete next_step to reopen this same task. '
        'Fix substantiated in-scope defects and recheck affected behavior. Use PENDING if authorized review or corrections remain, NEEDS_USER for a real blocker. '
        'Save a NEW checkpoint with a completed entry containing '
        '[BRIDGE_REVIEW:'+row['run_id']+'] and a concise account of what you inspected, findings (or none), corrections and rechecks. '
        'Keep validation evidence and remaining gaps in their existing fields. Reassess evidence affected by any later user update before DONE. '
        'This acknowledgement is your report, not independent verification. A separate review document is unnecessary.')


def dispatch_message(db, row, users, initial=False):
    nonce=uuid.uuid4().hex
    rid='loop-'+uuid.uuid4().hex
    text=prompt(row['goal'],nonce,initial,row.get('work_task_id',''),json.loads(row.get('criteria_json') or '[]'),row.get('checkpoint_text',''),row.get('work_project',''))
    if row.get('work_task_id'):
        from external_reviews import optional_summary, instructions
        review=optional_summary(row['work_project'],row['work_task_id'])
        if review:text=instructions(review['policy']['settings'])+'\n\n'+text
    if row.get('final_review'):
        text = (review_instruction(row) if row.get('review_started') else 'FINAL REVIEW ENABLED: After you first report completion, the coordinator will request one separate review if continuation allowance remains. Perform normal checks now; do not defer them to that review.') + '\n\n' + text
    updates=task_updates.context(db,row['work_task_id']) if row.get('work_task_id') else ''
    if updates:text=updates+'\n\n'+text
    if row.get('continuation_context'):
        # Keep the exact final-marker instructions at the end of the prompt.
        text = row['continuation_context'] + '\n\n' + text
    # Mark uncertain before returning any message to the extension. Never replay.
    db.execute('INSERT INTO requests VALUES(?,?,?,?,?)',(rid,row['conversation'],text,'uncertain',time.time()))
    used=row['used'] + (0 if initial else 1)
    db.execute("UPDATE goal_loops SET state='dispatching',reason='Sending goal message.' ,nonce=?,expected_users=?,request_id=?,used=? WHERE conversation=?",
               (nonce,users+1,rid,used,row['conversation']))
    return {'send':{'id':rid,'text':text,'document_id':row['document_id'],'user_count':users},'loop':status(db,row['conversation'])}


def dispatch(db, p, cid, binding, evaluate=None):
    action=p['action']; row=status(db,cid)
    if action=='loop_stop':
        stop(db,cid,'Stopped by user. In-flight messages and already running tools may finish.')
        return {'loop':status(db,cid)}
    if action=='loop_start':
        if row and row['state'] in ACTIVE: raise ValueError('A loop is already active. Stop it first.')
        s,goal,limit=validate_start(p,binding)
        # Explicit Start cancels abandoned previews but cannot repeat an earlier uncertain attempt.
        db.execute("UPDATE requests SET state='cancelled' WHERE conversation=? AND state='pending'",(cid,))
        db.execute('INSERT OR REPLACE INTO goal_loops(conversation,run_id,goal,max_steps,used,state,reason,nonce,document_id,expected_users,request_id,last_seen,started,work_task_id,criteria_json,work_project) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (cid,uuid.uuid4().hex,goal,limit,0,'waiting','Starting goal.','',s['document_id'],s['user_count'],'',time.time(),time.time(),p.get('work_task_id',''),json.dumps(p.get('criteria',[])),binding['project'] if p.get('work_task_id') else ''))
        db.execute('UPDATE goal_loops SET final_review=? WHERE conversation=?',(int(p.get('final_review',False)),cid))
        row=status(db,cid)
        row['checkpoint_text']=p.get('checkpoint_text','')
        return dispatch_message(db,row,s['user_count'],True)
    if not row: return {'loop':None}
    if action=='loop_validate':
        allowed=row['state']=='dispatching' and row['request_id']==p.get('id')
        return {'allowed':allowed,'loop':row}
    if action=='loop_result':
        if row['request_id']!=p.get('id'):raise ValueError('This delivery belongs to an older run.')
        delivery=p.get('state')
        if delivery not in {'observed_sent','uncertain'}: raise ValueError('Invalid loop delivery result.')
        db.execute("UPDATE requests SET state=? WHERE id=? AND state='uncertain'",(delivery,p['id']))
        if row['state']=='dispatching':
            db.execute('UPDATE goal_loops SET state=?,reason=? WHERE conversation=?',
                ('waiting' if delivery=='observed_sent' else 'stopped',
                 'Waiting for an explicit status from ChatGPT.' if delivery=='observed_sent' else 'Delivery uncertain. Check the conversation; no automatic retry.' + (' '+str(p.get('error',''))[:200] if p.get('error') else ''),cid))
        return {'loop':status(db,cid)}
    if action!='loop_tick':raise ValueError('Unknown loop action.')
    if row['state'] not in ACTIVE or row['state']=='reconnect_required':return {'loop':row}
    s=snapshot(p)
    if s['document_id']!=row['document_id']:
        stop(db,cid,'Page observation changed. Reconnect observation to verify this conversation; nothing will be replayed.','reconnect_required')
    elif s.get('state') in BLOCKED:
        stop(db,cid,{'draft':'A draft is open.','approval':'Approval required.','interrupted':'Response was stopped.','error':'Page error.','unknown':'Page state is unconfirmed.'}[s['state']])
    elif row['state']=='dispatching':
        # A second poll cannot replay an in-flight / lost delivery.
        pass
    elif s['user_count']!=row['expected_users']:
        stop(db,cid,'Conversation changed outside this loop.')
    elif s.get('state')=='generating':
        db.execute('UPDATE goal_loops SET last_seen=? WHERE conversation=?',(time.time(),cid))
    elif s.get('state') in {'idle','completed'} and s.get('terminal') is True:
        marker=s.get('marker') or {}
        decision=marker.get('decision') if marker.get('nonce')==row['nonce'] else None
        if decision=='NEEDS_USER':stop(db,cid,'ChatGPT needs your input or approval.')
        elif decision not in {'PENDING','DONE'}:stop(db,cid,'Missing or ambiguous final status. Review the response.')
        else:
            review = evaluate(row, decision) if evaluate else {}
            issue = review.get('issue','')
            has_updates=review.get('has_updates',False)
            if review.get('waiting_external'):
                db.execute('UPDATE goal_loops SET reason=?,last_seen=? WHERE conversation=?',
                           ('Waiting for external reviewers. No chat message or continuation used.',time.time(),cid))
            elif review.get('blocked'):
                stop(db,cid,'Context needs review: '+issue,'review_required')
            elif decision=='DONE' and not issue and not has_updates:
                if row['final_review'] and not row['review_started']:
                    if row['used']>=row['max_steps']:
                        stop(db,cid,'Implementation reported complete; final review has not run. Continuation limit reached.','review_required')
                    else:
                        revision=review.get('revision',-1)
                        db.execute('UPDATE goal_loops SET review_started=1,review_revision=?,last_seen=? WHERE conversation=?',(revision,time.time(),cid))
                        row=status(db,cid)
                        row['continuation_context']=review.get('context','')
                        result=dispatch_message(db,row,s['user_count'])
                        db.execute('UPDATE goal_loops SET reason=? WHERE conversation=?',('Sending final review of checkpoint revision '+str(revision)+'. Uses one automatic continuation.',cid))
                        result['loop']=status(db,cid)
                        return result
                else:
                    if row['review_started']:
                        db.execute('UPDATE goal_loops SET review_completed_revision=? WHERE conversation=?',(review.get('revision',-1),cid))
                    reason=('Agent reported final review in checkpoint revision '+str(review.get('revision',-1))+'. Review its evidence.' if row['review_started'] else 'Agent declared goal complete. Review the reported evidence.')
                    stop(db,cid,reason,'completed')
            elif has_updates and row['used']>=row['max_steps']:
                stop(db,cid,'Updates pending — continuation limit reached. Review resume to send them.','updates_pending')
            elif decision=='DONE' and not has_updates and (row['feedback_count']>=1 or row['used']>=row['max_steps']):
                stop(db,cid,'Completion needs review: '+issue,'review_required')
            elif row['used']>=row['max_steps']:
                stop(db,cid,'Automatic continuation limit reached.','limit_reached')
            else:
                if status(db,cid)['state'] not in ACTIVE:
                    return {'loop':status(db,cid)}
                row['continuation_context']=review.get('context','')
                db.execute('UPDATE goal_loops SET last_seen=? WHERE conversation=?',(time.time(),cid))
                if decision=='DONE' and not has_updates:
                    db.execute('UPDATE goal_loops SET feedback_count=feedback_count+1 WHERE conversation=?',(cid,))
                result = dispatch_message(db,row,s['user_count'])
                if has_updates or decision=='DONE':
                    reason='Sending queued task updates.' if has_updates else 'Sending completion feedback: '+issue
                    db.execute('UPDATE goal_loops SET reason=? WHERE conversation=?',(reason,cid))
                    result['loop']=status(db,cid)
                return result
    else:stop(db,cid,'Response completion could not be confirmed.')
    return {'loop':status(db,cid)}


def validate_start(p,binding):
    if type(p.get('final_review',False)) is not bool: raise ValueError('Final review must be on or off.')
    if p.get('final_review') and not any(p.get(k) for k in ('work_task_id','work_request_id','resume')): raise ValueError('Final review requires a linked task. Start from the full workspace.')
    s=snapshot(p)
    if s.get('state') not in {'idle','completed'}: raise ValueError('Start only when the conversation is ready and has no draft.')
    if time.time()-binding['seen']>20: raise ValueError('Page observation is stale. Reopen the extension.')
    goal=p.get('goal','')
    if not isinstance(goal,str): raise ValueError('Goal must be text.')
    goal=goal.strip();limit=p.get('limit',3)
    if not goal or len(goal)>4000: raise ValueError('Enter a goal with clear completion criteria (up to 4,000 characters).')
    if type(limit) is not int or not 1<=limit<=10: raise ValueError('Choose 1 to 10 automatic continuations.')
    return s,goal,limit
