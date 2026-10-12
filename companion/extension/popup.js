const $ = id => document.getElementById(id);
const READY = ['idle','completed','interrupted'];
const STATES = {idle:'Ready to continue',completed:'Response finished',interrupted:'Response stopped',generating:'ChatGPT is responding',draft:'A draft is open in ChatGPT',approval:'Waiting for your approval',error:'ChatGPT reported an error',unknown:'Page state not confirmed',disconnected:'Connection lost'};
let tab, request=null, binding=null, busy=false, attempted=false;
let loop=null, goalPreview=null, startingLoop=false;
$('open-workspace').onclick=async()=>{try{const r=await chrome.runtime.sendMessage({kind:'panel_open',tab:tab?.id});if(!r.ok)throw Error(r.error);}catch(e){explain(e);}};
if(chrome.runtime.getManifest)$('version').textContent=' v'+chrome.runtime.getManifest().version;
const loopActive = () => !!loop && ['waiting','dispatching','reconnect_required'].includes(loop.state);
function controls() {
  $('bind').disabled=busy||!!request;
  $('unbind').disabled=busy||!binding;
  $('preview').disabled=busy||!binding||!!request||attempted;
  $('send').disabled=busy||!request||attempted;
  $('cancel').disabled=busy||!request||attempted;
  $('text').disabled=busy||!!request||attempted;
  $('project').disabled=busy||!!request;
  $('task').disabled=busy||!!request;
  $('new').disabled=busy||loopActive();
  if(loopActive()) {
    for(const id of ['bind','preview','send','project','task','text']) $(id).disabled=true;
  }
  $('goal').disabled=busy||loopActive()||!!goalPreview;
  $('limit').disabled=busy||loopActive()||!!goalPreview;
  $('goal-review').disabled=busy||!binding||loopActive()||!!request||!!goalPreview;
  $('goal-start').disabled=busy||!binding||!goalPreview||loopActive();
  $('goal-cancel').disabled=busy;
  $('goal-stop').disabled=!loopActive()&&!startingLoop;

}
function explain(e) {
  const text=e.message||String(e);
  $('status').scrollIntoView?.({block:'nearest'});
  $('status').textContent=/Receiving end does not exist|Could not establish connection/.test(text)
    ? 'The extension is not connected to this page yet. Reload the ChatGPT page when no draft or response is in progress, then reopen this window.' : text;
}
function action(id, fn) {
  $(id).onclick=async()=>{if(busy)return;busy=true;controls();try{await fn();}catch(e){explain(e);}finally{busy=false;controls();}};
}
async function native(payload) {
  const r=await chrome.runtime.sendMessage({kind:'native',tab:tab.id,url:tab.url,payload});
  if(!r.ok)throw Error(r.error);return r;
}
async function inspect(report=true) {
  const current=await chrome.tabs.get(tab.id);
  if(current.url!==tab.url)throw Error('The conversation changed. Close and reopen the extension.');
  const s=await chrome.tabs.sendMessage(tab.id,{kind:'inspect'});
  if(s.url!==tab.url)throw Error('The conversation changed. Close and reopen the extension.');
  $('state').textContent=STATES[s.state]||STATES.unknown;
  if(report&&binding)await native({action:'observe',state:s.state});
  return s;
}
function showPreview(r) {
  request=r;attempted=false;$('review').hidden=false;$('receipt').hidden=true;
  $('text').value=r.text;$('destination').textContent=`Send to: ${tab.title || 'ChatGPT conversation'}`;
  $('summary').textContent=r.text;
}
function showReceipt(r) {
  attempted=true;request=null;$('review').hidden=true;$('receipt').hidden=false;$('new').hidden=false;
  $('delivery').textContent=r.state==='observed_sent'?'Message sent — found in the correct conversation.'
    :'Delivery uncertain. Check the ChatGPT conversation before writing another message. This attempt will not be repeated.';
}
action('bind',async()=>{
  await native({action:'bind',project:$('project').value,task:$('task').value});
  await chrome.storage.local.set({project:$('project').value});
  await load();$('status').textContent='Conversation connected. Nothing has been sent.';
});
action('unbind',async()=>{
  await native({action:'unbind'});loop=null;renderLoop();binding=null;request=null;attempted=false;
  $('connection').textContent='Not connected';$('review').hidden=true;$('receipt').hidden=true;$('settings').open=true;
  $('status').textContent='Disconnected. Unsent previews were cancelled. Previous delivery receipts were kept.';
});
action('preview',async()=>{
  const s=await inspect();if(!READY.includes(s.state))throw Error(`Cannot send: ${STATES[s.state]||STATES.unknown}. Your ChatGPT draft will not be changed.`);
  const r=await native({action:'prepare',id:crypto.randomUUID(),text:$('text').value});showPreview(r);
  $('status').textContent='Review the message below. It has not been sent.';
});
action('cancel',async()=>{
  await native({action:'result',id:request.id,state:'cancelled'});request=null;$('review').hidden=true;
  $('status').textContent='Preview cancelled. Nothing was sent.';
});
action('send',async()=>{
  const s=await inspect();if(!READY.includes(s.state))throw Error(`Sending blocked: ${STATES[s.state]||STATES.unknown}. Cancel the preview or try again when the page is ready.`);
  const id=request.id;
  // A lost claim reply can mean the claim committed. Never re-enable this attempt.
  attempted=true;
  let claimed;
  try{claimed=await native({action:'claim',id});}catch(e){showReceipt({state:'uncertain'});throw e;}
  let result;
  try{result=await chrome.tabs.sendMessage(tab.id,{kind:'send',url:tab.url,text:claimed.text});}
  catch(e){result={state:'uncertain',error:e.message};}
  try{await native({action:'result',id,state:result.state||'uncertain'});}
  catch(e){showReceipt({state:'uncertain'});throw Error('The local receipt could not be confirmed. Check the conversation; do not repeat this attempt.');}
  showReceipt(result);$('status').textContent=result.error||'';
});
action('new',async()=>{attempted=false;request=null;$('text').value='';$('receipt').hidden=true;$('status').textContent='Write a new message. Previous requests are not reused.';});
async function load() {
  const info=await native({action:'status'});binding=info.binding;loop=info.loop||null;renderLoop();
  if(loop) {$('goal').value=loop.goal;$('limit').value=loop.max_steps;}
  $('connection').textContent=binding?`Connected to: ${binding.project}`:'Not connected';
  $('settings').open=!binding;
  if(binding){$('project').value=binding.project;$('task').value=binding.task||'';}
  const latest=info.requests?.[0];
  request=null;attempted=false;$('review').hidden=true;$('receipt').hidden=true;
  // Only offer a still-pending preview if it is the most recent request.
  if(latest?.state==='pending')showPreview(latest);
  else if(latest&&['observed_sent','uncertain'].includes(latest.state))showReceipt(latest);
  await inspect();
}
function renderLoop() {
  if(!loop) {$('loop-status').textContent='Not started';return;}
  const names={reconnect_required:'Observation needs reconnection · open full workspace',waiting:'Running — waiting for ChatGPT',dispatching:'Sending',stopped:'Stopped',completed:'Agent declared completion',limit_reached:'Continuation limit reached'};
  $('loop-status').textContent=`${names[loop.state]||loop.state} · ${loop.used}/${loop.max_steps} automatic continuations\n${loop.reason}`;
}
action('goal-review',async()=>{
  const s=await inspect();
  if(!['idle','completed'].includes(s.state))throw Error('Wait for a ready conversation with no draft before starting a goal.');
  const goal=$('goal').value.trim(),limit=Number($('limit').value);
  if(!goal||goal.length>4000||!Number.isInteger(limit)||limit<1||limit>10)throw Error('Enter a goal and a limit from 1 to 10.');
  goalPreview={goal,limit};$('goal-preview').hidden=false;
  $('goal-summary').textContent=`Destination: ${tab.title}\n${tab.url}\n\n${goal}\n\nStart sends one goal message, then at most ${limit} automatic continuations.`;
  $('status').textContent='Review the goal. Nothing has been sent.';
});
action('goal-cancel',async()=>{goalPreview=null;$('goal-preview').hidden=true;});
action('goal-start',async()=>{
  const s=await inspect();startingLoop=true;controls();
  try {
    const result=await native({action:'loop_start',...goalPreview,snapshot:s});
    loop=result.loop;renderLoop();if(result.delivery)showReceipt(result.delivery);$('status').textContent=result.delivery?.state==='observed_sent'?'Goal message sent. Loop status is shown below.':'Check the loop status and conversation before doing anything else.';
  } finally {startingLoop=false;goalPreview=null;$('goal-preview').hidden=true;}
});
// Stop stays available while a Start request is in flight.
$('goal-stop').onclick=async()=>{
  try{const r=await native({action:'loop_stop'});loop=r.loop;renderLoop();controls();}
  catch(e){explain(Error('Stop could not be confirmed. Check the conversation and close its tab to prevent further continuations.'));}
};
setInterval(async()=>{
  if(!tab||busy||!binding)return;
  try{const info=await native({action:'status'});loop=info.loop||null;renderLoop();controls();}
  catch(e){$('loop-status').textContent='Connection lost. No automatic retry. Check the conversation.';}
},5000);
(async()=>{
  busy=true;controls();
  try{
    [tab]=await chrome.tabs.query({active:true,currentWindow:true});
    if(!tab||!/^https:\/\/chatgpt\.com\//.test(tab.url||''))throw Error('Open the ChatGPT conversation you want to connect, then reopen this extension.');
    $('conversation').textContent=tab.title||'ChatGPT conversation';$('address').textContent=tab.url;
    const saved=await chrome.storage.local.get('project');$('project').value=saved.project||'';
    await load();
  }catch(e){explain(e);}finally{busy=false;controls();}
})();
