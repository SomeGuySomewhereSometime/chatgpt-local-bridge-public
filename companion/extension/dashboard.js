'use strict';
const $ = id => document.getElementById(id);
const chatURL = url => /^https:\/\/chatgpt\.com\/(?:g\/[^/]+\/)?c\/[\w-]+(?:[?#].*)?$/.test(url || '');
const cid = url => url?.match(/\/c\/([\w-]+)/)?.[1];
const ready = state => ['idle','completed','interrupted'].includes(state);
const active = loop => loop && ['waiting','dispatching','reconnect_required'].includes(loop.state);
const pageNames = {idle:'Ready',completed:'Response finished',interrupted:'Response stopped',generating:'Responding',draft:'Draft open',approval:'Approval needed',error:'Page error',unknown:'Unconfirmed',disconnected:'Disconnected'};
let selected=null, binding=null, loop=null, info=null, busy=false, polling=false, stopping=false;
let goalPreview=null, messagePreview=null, page='unknown', items=[], selectionEpoch=0, work=null, editorRevision=null, editorTaskId=null;
let updateTask=null, updateEditing=null, updateRequest=null;
const updateDraftKey = () => 'workspaceUpdate:'+updateTask;
const draftKey = () => 'workspaceDraft:'+cid(selected?.url);
const timestamp = value => value ? new Date(value*1000).toLocaleString() : '';
let noticeTimer;
function notice(text,error=false) {
  clearTimeout(noticeTimer);
  $('notice').textContent=text; $('notice').hidden=!text; $('notice').className=error?'error':'';
  // Success describes the last action, not the current run. Keep it temporary.
  // Errors remain visible until the next user action or explicit notice.
  if(text && !error) noticeTimer=setTimeout(()=>{$('notice').hidden=true;},8000);
}
function verifiableReceipt() {
  const r=info?.requests?.find(r=>r.id===loop?.request_id);
  return r&&(r.state==='uncertain'||(r.state==='observed_sent'&&loop?.state==='stopped'&&/^(Delivery uncertain\.|Delivery observation expired\.)/.test(loop.reason||'')))?r:null;
}
function controls() {
  const hasTab=Number.isInteger(selected?.id), linked=!!binding && binding.tab===selected?.id;
  for(const id of ['refresh','connect','disconnect','review-goal','review-message','start','send','cancel-goal','cancel-message','resume-goal','load-checkpoint','save-checkpoint']) $(id).disabled=busy;
  $('open-chat').disabled=busy||!selected;
  $('debug-enabled').disabled=busy||!linked||info?.diagnostics?.available!==true;
  $('diagnostic-run').disabled=busy||!linked;
  $('export-diagnostics').disabled=busy||!linked||!info?.diagnostics?.runs?.length;
  $('connect').disabled=busy||!hasTab||active(loop)||!!goalPreview||!!messagePreview;
  $('disconnect').disabled=busy||!binding;
  const uncertainReceipt=verifiableReceipt();
  $('verify-delivery').hidden=!uncertainReceipt;
  $('verify-note').hidden=!uncertainReceipt;
  $('verify-delivery').disabled=busy||!linked||active(loop)||!uncertainReceipt;
  $('reconnect').disabled=busy||!hasTab||!binding||!!goalPreview||!!messagePreview;
  $('reconnect').hidden=!binding;
  $('review-goal').disabled=busy||!linked||active(loop)||!!goalPreview||!!messagePreview;
  $('review-message').disabled=busy||!linked||active(loop)||!!goalPreview||!!messagePreview;
  $('start').disabled=busy||!goalPreview||active(loop);
  $('send').disabled=busy||!messagePreview||active(loop);
  $('stop').disabled=stopping||!active(loop);
  $('resume-goal').disabled=busy||!linked||active(loop)||!work?.context?.checkpoint||!!goalPreview||!!messagePreview;
  $('load-checkpoint').disabled=busy||!work?.context||active(loop);
  $('save-checkpoint').disabled=busy||!work?.context||active(loop)||editorRevision===null;
  for(const id of ['checkpoint-summary','checkpoint-completed','checkpoint-pending','checkpoint-unverified','checkpoint-next'])$(id).disabled=busy||active(loop)||!work?.context;
  for(const id of ['goal','criteria','limit','final-review']) $(id).disabled=busy||active(loop)||!!goalPreview;
  for(const id of ['project','task']) $(id).disabled=busy||active(loop)||!!goalPreview||!!messagePreview;
  $('message').disabled=busy||active(loop)||!!messagePreview;
  const canUpdate=!!work?.task_id&&linked;
  const editCurrent=updateEditing?work?.updates?.find(x=>x.id===updateEditing.id):null;
  $('update-text').disabled=busy||!canUpdate;
  $('queue-update').disabled=busy||!canUpdate||(!!updateEditing&&(!editCurrent||editCurrent.state!=='queued'||editCurrent.revision!==updateEditing.revision));
  $('update-mode').disabled=busy||!canUpdate;
  $('queue-update').textContent=updateEditing?'Save queued update':'Queue update';
  $('cancel-update-edit').hidden=!updateEditing;
  $('cancel-update-edit').disabled=busy;
  for(const button of $('task-updates').querySelectorAll('button'))button.disabled=busy||(button.requiresStopped&&active(loop));
  for(const button of $('conversations').querySelectorAll('button'))button.disabled=busy;
}
function explain(error) {
  let text=error?.message || String(error);
  if(/Receiving end does not exist|Could not establish connection|context invalidated/i.test(text))text='The extension is not connected to this ChatGPT page. Preserve any draft, reload the ChatGPT tab when it is safe, then refresh this workspace.';
  notice(text,true);
}
function action(id,fn) { $(id).onclick=async()=>{if(busy)return;busy=true;controls();notice('');try{await fn();}catch(e){explain(e);}finally{busy=false;controls();}}; }
async function native(payload) {
  if(!selected || !Number.isInteger(selected.id))throw Error('Open and select the ChatGPT conversation first.');
  const r=await chrome.runtime.sendMessage({kind:'native',tab:selected.id,url:selected.url,payload});
  if(!r?.ok)throw Error(r?.error||'Local coordinator did not respond.');
  return r;
}
async function inspect() {
  const current=await chrome.tabs.get(selected.id);
  if(current.url!==selected.url)throw Error('The selected tab changed conversation. Refresh and select the correct conversation.');
  const checkedURL=selected.url, epoch=selectionEpoch;
  const s=await chrome.tabs.sendMessage(selected.id,{kind:'inspect'});
  if(epoch!==selectionEpoch || selected?.url!==checkedURL)throw Error('Selection changed while checking the page.');
  if(s.url!==selected.url)throw Error('The conversation changed while checking it.');
  page=s.state;$('page-state').textContent=pageNames[page]||page;
  if(binding?.tab===selected.id)await native({action:'observe',state:s.state});
  return s;
}
function showMessage(r) {
  messagePreview=r;$('message-review').hidden=false;
  $('message-summary').textContent=r.text;
  $('message-destination').textContent=`Destination: ${selected.title}\n${selected.url}`;
}
function render(info) {
  binding=info.binding||null;loop=info.loop||null;work=info.work||null;
  if(typeof renderReviewers==='function')renderReviewers(info);
  const connected=binding?.tab===selected?.id;
  $('connection-badge').textContent=connected?'Connected':binding?'Reconnect required':'Not connected';
  $('project-label').textContent=binding?.project||'Choose a project folder and connect this conversation.';
  const names={reconnect_required:'Observation needs reconnection',waiting:'Waiting for ChatGPT',dispatching:'Sending message',completed:'Model declared completion',limit_reached:'Continuation limit reached',review_required:'Completion needs review',updates_pending:'Updates pending',stopped:/needs your input|approval required/i.test(loop?.reason||'')?'Waiting for you':'Automatic continuation stopped'};
  $('run-state').textContent=loop?(loop.observation_stale?'Waiting for page observation':names[loop.state]||loop.state):(connected?'Ready to set a goal':'Not connected');
  $('reason').textContent=(loop?.observation_stale?'ChatGPT may still be working. No continuation is sent until a fresh page observation passes all checks.':loop?.reason)||(connected?'Review a goal, then explicitly start the loop.':'Connecting does not send a message.');
  if(loop && !active(loop))$('reason').textContent+=' Automatic chat messages are off. This does not cancel the current ChatGPT response or its authorized work.';
  $('final-review-state').textContent=!loop?'':!loop.final_review?'Separate final review: off.':loop.review_completed_revision>=0?`Final review reported in checkpoint revision ${loop.review_completed_revision}. Evidence remains model-reported.`:reviewReported()?'Final review reported in the latest checkpoint; message delivery is tracked separately.':loop.review_started?'Final review requested · verification or corrections remain unconfirmed.':'Final review pending · implementation must finish and continuation allowance must remain.';
  $('counter').textContent=loop?`${loop.used} / ${loop.max_steps}`:'—';
  const seconds=loop?Math.max(0,Math.floor((active(loop)?Date.now()/1000:loop.last_seen)-loop.started)):0;
  $('elapsed').textContent=loop?`${Math.floor(seconds/60)}m ${seconds%60}s`:'—';
  $('active-goal').textContent=loop?.goal||'No goal started yet.';
  renderWork();
  renderUpdates();
  renderDiagnostics(info.diagnostics);
  const uncertain=info.requests?.find(r=>r.id===loop?.request_id&&r.state==='uncertain');
  $('uncertain-review').hidden=!uncertain;
  const last=info.requests?.[0];
  $('delivery').textContent=last?({observed_sent:'Message sent — found in the selected conversation.',uncertain:'Delivery uncertain. Check ChatGPT. This attempt will not be repeated.',pending:'Preview saved. Nothing has been sent.',cancelled:'Preview cancelled. Nothing was sent.'}[last.state]||last.state):'No attempts yet.';
  $('delivery-time').textContent=last?timestamp(last.created):'';
  $('last-message-details').hidden=!last;
  $('last-message').textContent=last?.text||'';
  const events=info.events||[];
  $('activity-count').textContent=String(events.length);
  const hostEvents=events.map(e=>({time:e.created,title:{bind:'Conversation connected',reconnect:'Observation reconnected',unbind:'Conversation disconnected',prepare:'Message reviewed',claim:'Delivery attempt started',result:'Delivery recorded',verify_delivery:'Message delivery verified · no resend',loop_start:'Goal started',loop_result:'Goal delivery recorded',loop_stop:'Loop stopped',work_save:'Checkpoint saved',update_bridge_offer:'Update offered via Bridge',update_bridge_ack:'Update acknowledged in checkpoint',update_add:'User update queued',update_edit:'Queued update edited',update_cancel:'Queued update cancelled',update_reconcile:'Uncertain update reviewed',work_resume:'Goal resumed explicitly',loop_tick:'Loop progress',observe:'ChatGPT page changed'}[e.kind]||e.kind,detail:e.detail,state:e.state}));
  // Older receipts predate the event journal; retain their delivery evidence.
  const earliest=events.at(-1)?.created ?? Infinity;
  for(const r of info.requests||[])if(r.created<earliest)hostEvents.push({time:r.created,title:'Earlier delivery receipt',detail:r.state,state:''});
  $('activity').replaceChildren();
  for(const e of hostEvents.sort((a,b)=>b.time-a.time).slice(0,60)) {
    const li=document.createElement('li'),title=document.createElement('strong'),detail=document.createElement('span'),time=document.createElement('time');
    title.textContent=e.title;detail.className='event-detail';detail.textContent=[e.detail,e.state].filter(Boolean).join(' · ');time.textContent=timestamp(e.time);
    li.append(title,detail,time);$('activity').append(li);
  }
  if(!hostEvents.length){const li=document.createElement('li');li.textContent='No activity recorded yet.';$('activity').append(li);}
  controls();
}
async function status(recover=false) {
  const epoch=selectionEpoch;
  const result=await native({action:'connection_status'});
  if(epoch!==selectionEpoch)return;
  info=result;render(result);
  await loadUpdateDraft();
  if(recover&&result.requests?.[0]?.state==='pending')showMessage(result.requests[0]);
}
async function saveDraft() {
  if(!selected)return;
  await chrome.storage.local.set({[draftKey()]:{goal:$('goal').value,criteria:$('criteria').value,limit:$('limit').value,final_review:!!$('final-review').checked,message:$('message').value,project:$('project').value,task:$('task').value}});
}
function renderList() {
  $('conversations').replaceChildren();
  for(const item of items) {
    const b=document.createElement('button'),name=document.createElement('strong'),sub=document.createElement('span');
    b.className='conversation'+(selected?.key===item.key?' selected':'');name.textContent=item.title||'ChatGPT conversation';
    sub.textContent=item.project?item.project.split('/').pop()+' · '+(item.id?'Open tab':'Tab closed'):(item.id?'Open · not connected':'Tab closed');
    b.append(name,sub);b.onclick=async()=>{if(busy)return;busy=true;controls();try{await saveDraft();await select(item);}catch(e){explain(e);}finally{busy=false;controls();}};
    $('conversations').append(b);
  }
  if(!items.length){const p=document.createElement('p');p.className='small muted';p.textContent='Open an existing ChatGPT conversation in Chrome, then refresh.';$('conversations').append(p);}
}
async function select(item) {
  selected=item;selectionEpoch++;updateTask=null;updateEditing=null;updateRequest=null;$('update-text').value='';$('update-mode').value='automatic';binding=null;loop=null;info=null;goalPreview=null;messagePreview=null;page='unknown';work=null;editorRevision=null;editorTaskId=null;$('ack-uncertain').checked=false;
  for(const id of ['checkpoint-summary','checkpoint-completed','checkpoint-pending','checkpoint-unverified','checkpoint-next'])$(id).value='';
  $('goal-review').hidden=true;$('message-review').hidden=true;notice('');
  $('title').textContent=item.title||'ChatGPT conversation';$('url').textContent=item.url;renderList();render({});
  const saved=await chrome.storage.local.get(draftKey()),draft=saved[draftKey()]||{};
  $('goal').value=draft.goal??item.loop?.goal??'';$('criteria').value=draft.criteria??'';$('limit').value=draft.limit??item.loop?.max_steps??3;
  $('final-review').checked=draft.final_review??(item.loop?!!item.loop.final_review:true);
  $('message').value=draft.message||'';$('project').value=draft.project??item.project??'';$('task').value=draft.task??item.task??'';
  await chrome.storage.local.set({workspaceSelection:item.key});
  if(!item.id){binding=item.binding;loop=item.loop;render({binding,loop});$('page-state').textContent='Tab closed';return;}
  await status(true);
  try{await inspect();}catch(e){page='disconnected';$('page-state').textContent='Disconnected';explain(e);}
}
async function refreshList(initial=false) {
  const result=await chrome.runtime.sendMessage({kind:'panel_list'});
  if(!result?.ok)throw Error(result?.error||'Could not list conversations.');
  items=result.tabs.map(t=>{const b=result.bindings.find(b=>b.conversation===cid(t.url));return {...t,...(b?{project:b.project,task:b.task,loop:b.loop,binding:b}:{}),key:'tab:'+t.id};});
  for(const b of result.bindings)if(!result.tabs.some(t=>cid(t.url)===b.conversation))items.push({key:'closed:'+b.conversation,id:null,url:'https://chatgpt.com/c/'+b.conversation,title:b.title,project:b.project,task:b.task,loop:b.loop,binding:b});
  if(initial||!selected){const saved=await chrome.storage.local.get('workspaceSelection');const wanted=Number(new URLSearchParams(location.search).get('tab'));const item=items.find(t=>t.id===wanted)||items.find(t=>t.key===saved.workspaceSelection)||items[0];if(item)await select(item);}
  else {const current=items.find(t=>t.key===selected.key&&t.url===selected.url);if(!current){const matches=items.filter(t=>cid(t.url)===cid(selected.url));if(matches.length===1){await select(matches[0]);notice('Conversation found again. Use Reconnect observation to verify the page. Nothing has been sent.');}else{selected=null;binding=null;loop=null;render({});notice('The selected tab was closed or changed. Select its current conversation.',true);}}}
  renderList();controls();
}
action('refresh',async()=>{await refreshList();if(selected?.id){await status();await inspect();}});
action('verify-delivery',async()=>{const r=await native({action:'verify_delivery',id:loop.request_id,run_id:loop.run_id});await status();await inspect();notice(r.loop?.state==='completed'?'Delivery verified and the reported completion reconciled. Nothing was sent.':'Delivery verified. Automatic messages remain off; review resume if further work is needed.');});
action('reconnect',async()=>{const r=await native({action:'reconnect'});await status();await inspect();await refreshList();notice(r.continuation_retained?'Observation reconnected. The remaining continuation allowance is preserved; this action sent nothing.':'Observation reconnected. Automatic continuation remains off; no message was sent.');});
action('connect',async()=>{await native({action:'bind',project:$('project').value,task:$('task').value});await status();await inspect();await saveDraft();await refreshList();notice('Conversation connected. Nothing has been sent.');});
action('disconnect',async()=>{
  if(selected.id)await native({action:'unbind'});
  else {const r=await chrome.runtime.sendMessage({kind:'panel_disconnect',url:selected.url});if(!r.ok)throw Error(r.error);}
  binding=null;loop=null;goalPreview=null;messagePreview=null;$('goal-review').hidden=true;$('message-review').hidden=true;render({});await refreshList();notice('Conversation disconnected. Existing receipts were kept.');
});
action('open-chat',async()=>{if(selected.id){const t=await chrome.tabs.get(selected.id);if(t.url!==selected.url)throw Error('That tab changed conversation. Refresh before opening it.');await chrome.tabs.update(t.id,{active:true});await chrome.windows.update(t.windowId,{focused:true});}else{await chrome.tabs.create({url:selected.url});notice('Conversation opened. Refresh and use Reconnect observation. No message was sent.');}});
action('review-goal',async()=>{
  const s=await inspect();if(!['idle','completed'].includes(s.state))throw Error(`Cannot start: ${pageNames[s.state]||s.state}. Preserve any ChatGPT draft.`);
  const goal=$('goal').value.trim(),limit=Number($('limit').value);
  if(!goal||goal.length>4000||!Number.isInteger(limit)||limit<1||limit>10)throw Error('Enter a goal and a continuation limit from 1 to 10.');
  const checks=$('criteria').value.trim();if(checks.length>2000||checks.split('\n').filter(x=>x.trim()).length>12||checks.split('\n').some(x=>x.length>300))throw Error('Use at most 12 criteria, each under 300 characters.');
  goalPreview={goal,criteria:checks,limit,final_review:!!$('final-review').checked,url:selected.url,project:binding.project,task:binding.task,work_request_id:crypto.randomUUID()};$('start').textContent='Start loop and send goal';$('resume-summary').hidden=true;$('goal-summary').textContent=goal+(checks?'\n\nCompletion criteria:\n'+checks:'');$('goal-destination').textContent=`Destination: ${selected.title}\n${selected.url}\nProject: ${binding.project}`;
  $('goal-budget').textContent=`One initial message + at most ${limit} automatic continuations. No time limit. ${goalPreview.final_review?'Final review is ON and uses this allowance.':'Separate final review is OFF.'}`;
  $('goal-review').hidden=false;await saveDraft();$('goal-review').scrollIntoView({behavior:'smooth',block:'center'});notice('Review the destination and goal below. Nothing has been sent.');
});
action('cancel-goal',async()=>{goalPreview=null;$('goal-review').hidden=true;notice('Goal review cancelled. Nothing was sent.');});
action('start',async()=>{
  const preview=goalPreview;if(!preview||preview.url!==selected.url)throw Error('Review the goal for this conversation first.');
  await status();
  if(!binding || binding.project!==preview.project || binding.task!==preview.task)throw Error('Project connection changed. Cancel this review and review the goal again.');
  const s=await inspect();
  if(!['idle','completed'].includes(s.state))throw Error('Wait for a ready ChatGPT conversation with no draft.');
  loop={state:'dispatching',used:0,max_steps:preview.limit,goal:preview.goal,reason:'Starting the reviewed goal.',started:Date.now()/1000,last_seen:Date.now()/1000};render({...info,loop});
  try{const result=await native({action:'loop_start',debug:!!$('debug-enabled').checked,goal:preview.goal,limit:preview.limit,final_review:preview.final_review,snapshot:s,criteria:preview.criteria,work_request_id:preview.work_request_id,...(preview.resume?{resume:preview.resume}:{})});loop=result.loop;render({...info,loop});notice(result.delivery?.state==='observed_sent'?'Goal sent. The loop is waiting for ChatGPT.':result.delivery?.error||'Delivery uncertain. Check ChatGPT before starting another run.',result.delivery?.state!=='observed_sent');}
  catch(e){await status();throw e;}
  finally{goalPreview=null;$('goal-review').hidden=true;}
  await status();
});
$('stop').onclick=async()=>{if(stopping)return;stopping=true;controls();try{const r=await native({action:'loop_stop'});loop=r.loop;render({...info,loop});notice('Stop confirmed. No further continuations will be dispatched. In-flight work may finish.');}catch(e){explain(e);}finally{stopping=false;controls();}};
action('review-message',async()=>{const s=await inspect();if(!ready(s.state))throw Error(`Cannot send: ${pageNames[s.state]||s.state}.`);const r=await native({action:'prepare',id:crypto.randomUUID(),text:$('message').value});showMessage(r);await saveDraft();$('message-review').scrollIntoView({behavior:'smooth',block:'center'});notice('Review the message below. Nothing has been sent.');});
action('cancel-message',async()=>{await native({action:'result',id:messagePreview.id,state:'cancelled'});messagePreview=null;$('message-review').hidden=true;await status();notice('Message preview cancelled.');});
action('send',async()=>{
  const s=await inspect();if(!ready(s.state))throw Error(`Sending blocked: ${pageNames[s.state]||s.state}.`);
  const id=messagePreview.id;let claimed;
  // Clear this attempt before claiming: a lost reply must never re-enable it.
  messagePreview=null;$('message-review').hidden=true;
  try{claimed=await native({action:'claim',id});}catch(e){await status();throw e;}
  let delivery;
  try{delivery=await chrome.tabs.sendMessage(selected.id,{kind:'send',url:selected.url,text:claimed.text});}catch(e){delivery={state:'uncertain',error:e.message};}
  await native({action:'result',id,state:delivery?.state||'uncertain'});await status();
  notice(delivery?.state==='observed_sent'?'Message sent — found in the selected conversation.':delivery?.error||'Delivery uncertain. Check ChatGPT. This attempt will not be repeated.',delivery?.state!=='observed_sent');
});

function reviewReported() {
  const context=work?.context,checkpoint=context?.checkpoint;
  return checkpoint?.state==='completed'&&loop?.review_started&&context.selected_revision>loop.review_revision&&checkpoint.progress.completed.some(x=>x.includes('[BRIDGE_REVIEW:'+loop.run_id+']'));
}
function renderWork() {
  $('work-id').textContent=work?.task_id?'Bridge task: '+work.task_id:'';
  $('work-error').textContent=work?.error||'';
  const context=work?.context,checkpoint=context?.checkpoint,p=checkpoint?.progress;
  $('work-state').textContent=work?.error?'Work status unavailable':({completed:'Reported complete',in_progress:'Reported in progress',blocked:'Reported blocked',paused:'Reported paused'}[checkpoint?.state]||'No checkpoint yet');
  const uncertain=info?.requests?.some(r=>r.id===loop?.request_id&&r.state==='uncertain');
  const reviewed=reviewReported();
  $('delivery-work-state').textContent=uncertain?(reviewed?'Final review reported complete; message delivery confirmation pending. Use Verify delivery.':checkpoint?.state==='completed'?'Work reported complete; message delivery confirmation pending. Use Verify delivery.':'Message delivery confirmation pending. The current ChatGPT response may continue working.') : verifiableReceipt()?'Message delivery is confirmed; completion reconciliation remains pending. Use Verify delivery.':'';
  $('work-state-detail').textContent=p ? `${(p.validation || []).filter(x=>x.status==='passed').length} checks reported passed · ${(p.unverified || []).length} unverified items. Latest checkpoint: ${timestamp(checkpoint.created_at)}. This is the model’s report; a stopped loop does not mean the work failed. See checks and unverified items below.` : work?.error || 'No work conclusion is available yet. Message delivery and loop state do not establish completion.';
  $('checkpoint-badge').textContent=checkpoint?'Revision '+context.selected_revision:context?'Awaiting checkpoint':'No linked task';
  $('checkpoint-meta').textContent=checkpoint?`${checkpoint.source} · ${timestamp(checkpoint.created_at)} · ${checkpoint.state}`:'';
  $('checkpoint-view').textContent=p?[
    'Summary: '+p.summary,'Completed:\n'+p.completed.join('\n'),
    'Remaining:\n'+p.pending.map(x=>x.description).join('\n'),
    'Unverified:\n'+p.unverified.join('\n'),'Next step: '+p.next_step,
    'Files:\n'+p.files.map(x=>x.path+(x.version?' · '+x.version:'')).join('\n')
  ].join('\n\n'):context?'No checkpoint yet. The model is instructed to save one through the Bridge.':'Start a goal from this workspace to link a Bridge task.';
  $('reported-checks').replaceChildren();$('observed-operations').replaceChildren();$('current-criteria').replaceChildren();$('checkpoint-history').replaceChildren();
  for(const criterion of work?.criteria||[]) {
    const check=p?.validation?.find(x=>x.description===criterion),li=document.createElement('li');
    li.textContent=criterion+' · '+(check?'Reported '+check.status:'Not reported');$('current-criteria').append(li);
  }
  for(const revision of context?.history||[]) {
    const li=document.createElement('li');li.textContent=`Revision ${revision.revision} · ${revision.state} · ${revision.source} · ${timestamp(revision.created_at)}`;$('checkpoint-history').append(li);
  }
  for(const check of p?.validation||[]) {
    const li=document.createElement('li');li.textContent=`Reported: ${check.status} · ${check.description}`;
    const matched=work?.evidence_checks?.find(x=>x.description===check.description);
    const label=matched?({version_confirmed:'Monitored source versions current',receipt_confirmed:'Command receipt confirmed',invalid:'Evidence needs review',reported:'Model-reported evidence'}[matched.state]||matched.state):'';
    const detail=document.createElement('small');detail.textContent=[check.evidence||'No evidence reference provided.',label,matched?.detail||''].filter(Boolean).join(' · ');li.append(detail);$('reported-checks').append(li);
  }
  for(const receipt of work?.receipts||[]) {
    const li=document.createElement('li');li.textContent=`Observed receipt: ${receipt.operation||'operation'} · ${receipt.state}`+(receipt.returncode!==null?' · exit '+receipt.returncode:'')+(receipt.timed_out?' · timed out':'');
    const detail=document.createElement('small');detail.textContent=receipt.request_id+' · '+receipt.cwd;li.append(detail);$('observed-operations').append(li);
  }
  $('evidence-summary').textContent=context?`${p?.validation?.length||0} reported checks · ${work.receipt_count??work.receipts?.length??0} linked operation receipts · ${work.unresolved_receipts||0} unresolved · ${work.failed_receipts||0} failures recorded`+(work.receipts_truncated?' · Only the latest 20 receipts are displayed; all retained links were checked for unresolved state.':'')+(work.receipt_history_limited?' · Receipt history is limited to the latest 100 links.':''):'No evidence for this goal yet.';
}
action('load-checkpoint',async()=>{
  await status();if(!work?.context)throw Error('No linked Bridge task.');
  const p=work.context.checkpoint?.progress||{};editorRevision=work.context.selected_revision;editorTaskId=work.task_id;
  $('checkpoint-summary').value=p.summary||'';$('checkpoint-completed').value=(p.completed||[]).join('\n');
  $('checkpoint-pending').value=(p.pending||[]).map(x=>x.description).join('\n');
  $('checkpoint-unverified').value=(p.unverified||[]).join('\n');$('checkpoint-next').value=p.next_step||'';
  notice('Checkpoint loaded into the editor. Nothing has been saved or sent.');
});
action('save-checkpoint',async()=>{
  const result=await native({action:'work_save',task_id:editorTaskId,revision:editorRevision,id:crypto.randomUUID(),fields:{summary:$('checkpoint-summary').value,completed:$('checkpoint-completed').value,pending:$('checkpoint-pending').value,unverified:$('checkpoint-unverified').value,next_step:$('checkpoint-next').value}});
  editorRevision=result.work.context.selected_revision;await status();notice('Checkpoint saved as user-reported context. No message sent.');
});
action('resume-goal',async()=>{
  await status();const s=await inspect();if(!['idle','completed'].includes(s.state))throw Error('Wait for a ready conversation with no draft before reviewing a resume.');
  const latest=info.requests?.find(r=>r.id===loop?.request_id),ack=latest?.state==='uncertain'&&$('ack-uncertain').checked?latest.id:'';
  const result=await native({action:'work_resume_preview',ack_id:ack}),limit=Number($('limit').value);
  if(!Number.isInteger(limit)||limit<1||limit>10)throw Error('Choose 1 to 10 automatic continuations.');
  goalPreview={goal:result.goal,criteria:result.criteria,limit,final_review:!!$('final-review').checked,url:selected.url,project:binding.project,task:binding.task,resume:result.resume};
  $('goal-summary').textContent=result.goal+'\n\nCompletion criteria:\n'+result.criteria;
  $('resume-summary').textContent=result.checkpoint_text;$('resume-summary').hidden=false;
  $('goal-destination').textContent=`Resume destination: ${selected.title}\n${selected.url}\nProject: ${binding.project}`;
  $('goal-budget').textContent=`A NEW run: one resume message + at most ${limit} automatic continuations. No time limit. Previous messages are not replayed. ${goalPreview.final_review?'Final review is ON for this new run and uses this allowance.':'Separate final review is OFF.'}`;
  $('start').textContent='Resume loop and send context';$('goal-review').hidden=false;$('goal-review').scrollIntoView({behavior:'smooth',block:'center'});
  notice('Review the checkpoint and destination. Resume sends a new message only when you press the button below.');
});

for(const id of ['goal','criteria','limit','final-review','message','project','task'])$(id).addEventListener('input',()=>{saveDraft().catch(explain);});
setInterval(async()=>{if(busy||polling||!selected?.id)return;polling=true;try{await status();await inspect();}catch(e){$('page-state').textContent='Disconnected';explain(e);}finally{polling=false;}},5000);
(async()=>{busy=true;controls();$('version').textContent='v'+chrome.runtime.getManifest().version;try{await refreshList(true);}catch(e){explain(e);}finally{busy=false;controls();}})();

async function saveUpdateDraft() {
  if(!updateTask)return;
  await chrome.storage.local.set({[updateDraftKey()]:{text:$('update-text').value,mode:$('update-mode').value,editing:updateEditing,request:updateRequest}});
}
async function loadUpdateDraft() {
  const task=work?.task_id||null;
  if(updateTask===task)return;
  updateTask=task;updateEditing=null;updateRequest=null;$('update-text').value='';$('update-mode').value='automatic';
  if(!task){controls();return;}
  const epoch=selectionEpoch,key=updateDraftKey(),saved=await chrome.storage.local.get(key);
  if(epoch!==selectionEpoch||work?.task_id!==task)return;
  const draft=saved[key]||{};
  $('update-text').value=draft.text||'';$('update-mode').value=draft.mode||'automatic';updateEditing=draft.editing||null;updateRequest=draft.request||null;controls();
}
async function mutateUpdate(payload) {
  await native({...payload,task_id:work.task_id});await status();
}
function updateButton(label,fn,requiresStopped=false) {
  const b=document.createElement('button');b.textContent=label;b.className='quiet';b.requiresStopped=requiresStopped;
  b.onclick=async()=>{if(busy)return;busy=true;controls();notice('');try{await fn();}catch(e){explain(e);}finally{busy=false;controls();}};
  return b;
}
function renderUpdates() {
  const rows=work?.updates||[],queued=rows.filter(x=>x.state==='queued').length;
  $('updates-badge').textContent=work?.task_id?`${queued} queued`:'No linked task';
  $('updates-note').textContent=!work?.task_id?'Start a goal from this workspace to queue instructions.':
    queued?(active(loop)?'Automatic instructions can arrive in supported Bridge results during this response. Otherwise they join the next permitted chat continuation.':'Instructions are saved. Review resume to send them; queuing does not restart the loop.'):
    'Sent instructions remain in later continuations and reviewed resumes. Sent means delivery was confirmed, not that the instruction was fulfilled.';
  $('task-updates').replaceChildren();
  for(const row of rows) {
    const li=document.createElement('li'),label=document.createElement('strong'),text=document.createElement('p'),detail=document.createElement('small'),buttons=document.createElement('div');
    const bridgeDelivery=row.delivery_channel==='bridge';
    const sending=!bridgeDelivery&&row.state==='uncertain'&&loop?.state==='dispatching'&&row.request_id===loop.request_id;
    label.textContent=bridgeDelivery&&row.state==='uncertain'?(active(loop)?'Bridge · awaiting checkpoint acknowledgement':'Bridge delivery unconfirmed'):bridgeDelivery&&row.state==='sent'?(row.confirmed_by==='bridge_checkpoint'?'Acknowledged via Bridge · checkpoint '+row.ack_revision:'Acknowledged by you · Bridge'):sending?'Sending':({queued:'Queued',sent:row.confirmed_by==='user'?'Sent · confirmed by you':'Sent · found in ChatGPT',uncertain:'Delivery uncertain',cancelled:row.confirmed_by==='excluded'?'Excluded after uncertain delivery':'Cancelled'}[row.state]||row.state);
    text.className='update-text';text.textContent=row.text;detail.textContent=`Revision ${row.revision} · ${timestamp(row.created)} · ${row.mode==='automatic'?'Automatic':'Next response'}`+(row.request_id?' · '+(bridgeDelivery?'Tool delivery ':'Message ')+row.request_id:'');buttons.className='button-row';
    if(row.state==='queued') {
      buttons.append(updateButton('Edit',async()=>{updateEditing={id:row.id,revision:row.revision};updateRequest=null;$('update-text').value=row.text;$('update-mode').value=row.mode||'next_reply';await saveUpdateDraft();$('update-text').scrollIntoView({behavior:'smooth',block:'center'});}),
      updateButton('Cancel queued update',async()=>{await mutateUpdate({action:'update_cancel',id:row.id,revision:row.revision});notice('Queued update cancelled. No message sent.');}));
    } else if(row.state==='uncertain'&&!sending) {
      detail.textContent+=bridgeDelivery?' · Check the Bridge tool result and checkpoint in ChatGPT. No chat fallback will duplicate this reserved instruction.':' · Inspect ChatGPT. This attempt will not be retried.';
      buttons.append(updateButton(bridgeDelivery?'I checked the tool delivery':'I found it in ChatGPT',async()=>{await mutateUpdate({action:'update_reconcile',id:row.id,revision:row.revision,resolution:'found'});notice('Update receipt acknowledged by you. Original delivery history is retained; application still needs review.');},true),
      updateButton('Exclude from future context',async()=>{await mutateUpdate({action:'update_reconcile',id:row.id,revision:row.revision,resolution:'exclude'});notice('Update excluded from future context. It may already have reached ChatGPT.');},true));
    }
    li.append(label,text,detail,buttons);$('task-updates').append(li);
  }
}
for(const id of ['update-text','update-mode'])$(id).addEventListener(id==='update-mode'?'change':'input',async()=>{updateRequest=null;await saveUpdateDraft();});
action('queue-update',async()=>{
  const task=work?.task_id;if(!task||updateTask!==task)throw Error('Refresh the linked task before saving an update.');
  const text=$('update-text').value;
  if(!updateEditing){updateRequest=updateRequest||crypto.randomUUID();await saveUpdateDraft();}
  await mutateUpdate(updateEditing?{action:'update_edit',id:updateEditing.id,revision:updateEditing.revision,text,mode:$('update-mode').value}:{action:'update_add',id:updateRequest,text,mode:$('update-mode').value});
  updateEditing=null;updateRequest=null;$('update-text').value='';$('update-mode').value='automatic';await saveUpdateDraft();
  notice('Update saved using the selected delivery mode. No message was sent by this action.');
});
action('cancel-update-edit',async()=>{updateEditing=null;updateRequest=null;await saveUpdateDraft();notice('Editing cancelled. The text is kept so you can queue it as a new instruction.');});

function renderDiagnostics(d) {
  $('debug-enabled').checked=!!d?.enabled;
  $('debug-state').textContent=d?.available===true?(d.run_id?(d.enabled?'Debug on for the current run. ':'Debug off for the current run. '):'This setting applies to the next run. ')+ 'Essential failures are recorded either way.':'Diagnostics unavailable. The work can continue without debug.';
  const chosen=$('diagnostic-run').value;
  $('diagnostic-run').replaceChildren();
  for(const run of d?.runs||[]) {
    const option=document.createElement('option');option.value=run.run_id;
    option.textContent=timestamp(run.started)+' · '+run.run_id.slice(0,8)+(run.enabled?' · debug':'');
    $('diagnostic-run').append(option);
  }
  $('diagnostic-run').value=d?.runs?.some(r=>r.run_id===chosen)?chosen:d?.runs?.[0]?.run_id||'';
}
action('debug-enabled',async()=>{
  const result=await native({action:'diagnostics_set',run_id:info?.diagnostics?.run_id||'',enabled:!!$('debug-enabled').checked});
  info={...info,diagnostics:result.diagnostics};render(info);
  notice('Debug setting saved locally. No message was sent.');
});
action('export-diagnostics',async()=>{
  const epoch=selectionEpoch, runId=$('diagnostic-run').value;
  const result=await native({action:'diagnostics_export',run_id:runId});
  if(epoch!==selectionEpoch)throw Error('Selection changed. Export cancelled.');
  const blob=new Blob([JSON.stringify(result.diagnostic_export,null,2)],{type:'application/json'});
  const url=URL.createObjectURL(blob),link=document.createElement('a');
  link.href=url;link.download='bridge-diagnostics-'+runId+'.json';
  document.body.append(link);link.click();link.remove();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
  notice(result.diagnostic_export.partial?'Partial diagnostic export downloaded. Native coordinator unavailable; browser events only.':'Diagnostic export downloaded. It contains local metadata, not the conversation.');
});
