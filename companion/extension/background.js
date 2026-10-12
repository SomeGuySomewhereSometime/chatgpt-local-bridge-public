const HOST = 'io.local.bridge_companion';
let clientPromise;
const driving = new Set();
// Fixed metadata allowlist: never store payload text, raw error messages or URLs.
const diagnosticContexts = new Map();
function diagnosticVersion(){try{return chrome.runtime.getManifest?.().version;}catch(_){return undefined;}}
const diagnosticActions=new Set(['verify_delivery','reconnect','connection_status','status','observe','loop_start','loop_tick','loop_validate','loop_result','loop_stop','delivery','claim','result','bind','unbind','prepare','work_save','work_resume_preview','update_add','update_edit','update_cancel','update_reconcile','diagnostics_set','diagnostics_export']);
const diagnosticWrites = new Map();
const diagnosticCode = error => {
  const text=String(error?.message||error||'').toLowerCase();
  for(const [pattern,code] of [[/context invalidated/,'context_invalidated'],[/text did not match|text could not be verified/,'delivery_text_mismatch'],[/identit/,'identity_unavailable'],[/count did not match/,'count_mismatch'],[/draft/,'draft_preserved'],[/send is unavailable/,'send_unavailable'],[/conversation changed|page changed/,'page_changed'],[/loop stopped|not allowed/,'permission_denied']])if(pattern.test(text))return code;
  return 'delivery_unconfirmed';
};
function browserDiagnostic(tab,action,code,requestId='',meta={}) {
  try {
  if(!meta||typeof meta!=='object')meta={};
  // A bounded browser journal remains available when the native host is offline.
  if(!diagnosticActions.has(action))action='request';
  const key='bridgeDiagnostics';
  const conversation=tab.url?.match(/\/c\/([\w-]+)/)?.[1]||'';
  const savedContext=diagnosticContexts.get(tab.id)||{};
  const context=savedContext.conversation===conversation?savedContext:{};
  const event={created:Date.now()/1000,conversation,run_id:context.run_id||'',action,code,request_id:typeof requestId==='string'&&/^[\w-]{1,100}$/.test(requestId)?requestId:'',metadata:{}};
  for(const field of ['elapsed_ms','text_length','observed_text_length','polls'])if(Number.isFinite(meta[field])&&meta[field]>=0&&meta[field]<=10000000)event.metadata[field]=meta[field];
  for(const field of ['clicked','identified','count_matches','text_matches','bubble_expanded'])if(typeof meta[field]==='boolean')event.metadata[field]=meta[field];
  const version=diagnosticVersion();
  if(/^\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(version||''))event.metadata.extension_version=version;
  const previous=diagnosticWrites.get(key)||Promise.resolve();
  const write=previous.then(async()=>{
    const stored=await chrome.storage.local.get(key);
    const rows=Array.isArray(stored[key])?stored[key]:[];
    await chrome.storage.local.set({[key]:[...rows.filter(r=>r.created>Date.now()/1000-7*86400),event].slice(-200)});
  }).catch(()=>{});
  diagnosticWrites.set(key,write);
  // Do not delay the delivery path or propagate logging failures.
  } catch(_) { /* Optional telemetry cannot affect the caller. */ }
}
async function browserExport(tab,runId) {
  await diagnosticWrites.get('bridgeDiagnostics');
  const stored=await chrome.storage.local.get('bridgeDiagnostics');
  const conversation=tab.url.match(/\/c\/([\w-]+)/)[1];
  const rows=(stored.bridgeDiagnostics||[]).filter(r=>r.created>Date.now()/1000-7*86400&&r.conversation===conversation);
  return {events:rows.filter(r=>r.run_id===runId).map(({conversation,...r})=>r),uncorrelated_failures:rows.filter(r=>!r.run_id).map(({conversation,...r})=>r),limit:200};
}
function clientId() {
  if (!clientPromise) clientPromise = (async () => {
    const data = await chrome.storage.local.get('client');
    if (data.client) return data.client;
    const client = crypto.randomUUID(); await chrome.storage.local.set({client}); return client;
  })();
  return clientPromise;
}
async function native(tab, payload) {
  const key='loopHalted:'+tab.id;
  const flags=await chrome.storage.local.get(key);
  if(flags[key] && ['loop_tick','loop_validate'].includes(payload.action)) {
    payload={action:'loop_stop'};
  }
  const started=Date.now();
  try {
    const result=await chrome.runtime.sendNativeMessage(HOST,{...payload,diagnostic_meta:{...payload.diagnostic_meta,extension_version:diagnosticVersion()},title:tab.title||'',url:tab.url,tab:tab.id,client:await clientId()});
    const run=result.loop?.run_id||result.diagnostics?.run_id;
    if(run)diagnosticContexts.set(tab.id,{run_id:run,conversation:tab.url.match(/\/c\/([\w-]+)/)?.[1]});
    if(!result.ok)browserDiagnostic(tab,payload.action,'native_rejected',payload.id,{elapsed_ms:Date.now()-started});
    if(result.ok&&payload.action==='diagnostics_export') {
      try {result.diagnostic_export.browser=await browserExport(tab,payload.run_id);}
      catch(_) {result.diagnostic_export.browser={unavailable:true};}
    }
    if(!result.ok && payload.action.startsWith('loop_'))await chrome.storage.local.set({[key]:true});
    return result;
  } catch(e) {
    browserDiagnostic(tab,payload.action,'native_transport',payload.id,{elapsed_ms:Date.now()-started});
    await chrome.storage.local.set({[key]:true});
    if(payload.action==='diagnostics_export'&&/^[a-f0-9]{32}$/.test(payload.run_id||'')) {
      try {
        const {associations={}}=await chrome.storage.local.get('associations');
        if(associations[String(tab.id)]===tab.url) {
          return {ok:true,diagnostic_export:{schema:1,exported_at:Date.now()/1000,run:{run_id:payload.run_id},partial:true,environment:{extension_version:diagnosticVersion()},native:{unavailable:true},browser:await browserExport(tab,payload.run_id),notice:'Partial export: native coordinator unavailable. Browser events only; uncorrelated failures belong to this conversation, not necessarily this run. No automatic replay.'}};
        }
      } catch(_) { /* Preserve the original transport failure. */ }
    }
    throw e;
  }
}

async function deliver(tab, result) {
  if (!result.ok || !result.send) return result;
  const send=result.send;
  let delivery={state:'uncertain'};
  try {
    const current=await chrome.tabs.get(tab.id);
    if(current.url!==tab.url) throw Error('Conversation changed.');
    const permit=await native(tab,{action:'loop_validate',id:send.id});
    if(!permit.ok||!permit.allowed) throw Error('Loop stopped.');
    delivery=await chrome.tabs.sendMessage(tab.id,{kind:'send',loop:true,url:tab.url,...send});
  } catch(e) { delivery={state:'uncertain',error:e.message}; }
  if(delivery.state!=='observed_sent')browserDiagnostic(tab,'delivery',diagnosticCode(delivery.error),send.id,delivery.diagnostic_meta);
  const recorded=await native(tab,{action:'loop_result',id:send.id,state:delivery.state||'uncertain',error:delivery.error||'',diagnostic_meta:delivery.diagnostic_meta||{},observation:delivery.observation||{}});
  return {...recorded,delivery};
}
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  (async () => {
    const panel = sender.url?.split('?')[0] === chrome.runtime.getURL('dashboard.html');
    if (msg.kind === 'panel_open') {
      if (sender.tab && !panel) throw Error('Action not allowed.');
      const existing = (await chrome.tabs.query({})).find(t => t.url?.startsWith(chrome.runtime.getURL('dashboard.html')));
      if (existing) { await chrome.tabs.update(existing.id,{active:true}); return {ok:true}; }
      await chrome.tabs.create({url:chrome.runtime.getURL('dashboard.html')+(Number.isInteger(msg.tab)?'?tab='+msg.tab:'')});
      return {ok:true};
    }
    if (msg.kind === 'panel_disconnect') {
      if (!panel) throw Error('Action not allowed.');
      const result=await chrome.runtime.sendNativeMessage(HOST,{action:'panel_disconnect',url:msg.url,client:await clientId()});
      if(result.ok) {
        const {associations={}}=await chrome.storage.local.get('associations');
        const conversation=msg.url.match(/\/c\/([\w-]+)/)?.[1];
        for(const [tabId,url] of Object.entries(associations))if(url.match(/\/c\/([\w-]+)/)?.[1]===conversation)delete associations[tabId];
        await chrome.storage.local.set({associations});
      }
      return result;
    }
    if (msg.kind === 'panel_list') {
      if (!panel) throw Error('Action not allowed.');
      const info = await chrome.runtime.sendNativeMessage(HOST,{action:'panel_list',client:await clientId()});
      if (!info.ok) return info;
      return {...info,tabs:(await chrome.tabs.query({})).filter(t=>/^https:\/\/chatgpt\.com\/(?:g\/[^/]+\/)?c\/[\w-]+(?:[?#].*)?$/.test(t.url||''))};
    }
    if(msg.kind==='loop_validate' && sender.tab) return native(sender.tab,{action:'loop_validate',id:msg.id});
    if (msg.kind !== 'native') throw Error('Invalid request.');
    const tab = panel ? await chrome.tabs.get(msg.tab) : sender.tab || await chrome.tabs.get(msg.tab);
    if (!/^https:\/\/chatgpt\.com\/(?:g\/[^/]+\/)?c\/[\w-]+(?:[?#].*)?$/.test(tab.url || '')) throw Error('Open an existing ChatGPT conversation.');
    if ((panel || !sender.tab) && msg.url !== tab.url) throw Error('The conversation changed. Close and reopen the extension.');
    if (sender.tab && !panel && msg.payload.action !== 'observe') throw Error('Action not allowed.');
    if (sender.tab && !panel) {
      const {associations = {}} = await chrome.storage.local.get('associations');
      if (associations[String(tab.id)] !== tab.url) return {ok:true,ignored:true};
    }
    if(msg.payload.action==='verify_delivery') {
      if(!panel)throw Error('Verify delivery is available in the full workspace only.');
      if(driving.has(tab.id))throw Error('A delivery is in progress. Wait before verifying it.');
      driving.add(tab.id);
      try {
        const info=await native(tab,{action:'connection_status'});
        if(!info.ok||info.binding?.tab!==tab.id)throw Error('Reconnect this conversation before verifying delivery.');
        const receipt=info.requests?.find(r=>r.id===info.loop?.request_id);
        if(!receipt||receipt.id!==msg.payload.id||info.loop.run_id!==msg.payload.run_id)throw Error('The run or receipt changed. Refresh first.');
        const checked=await chrome.tabs.sendMessage(tab.id,{kind:'verify_delivery',url:tab.url,id:receipt.id,text:receipt.text,message_id:receipt.message_id||''});
        const current=await chrome.tabs.get(tab.id);
        if(!checked?.ok||current.url!==tab.url||checked.snapshot?.url!==tab.url)throw Error(checked?.error||'The complete message could not be verified. Nothing sent.');
        const fresh=await chrome.tabs.sendMessage(tab.id,{kind:'inspect'});
        if(fresh?.url!==tab.url||fresh.document_id!==checked.snapshot.document_id||fresh.last_user_id!==checked.snapshot.last_user_id||fresh.user_count!==checked.snapshot.user_count)throw Error('The conversation changed during verification. Nothing sent.');
        const flags=await chrome.storage.local.get('loopHalted:'+tab.id);
        return await native(tab,{action:'verify_delivery',id:receipt.id,run_id:info.loop.run_id,snapshot:fresh,delivery_halted:!!flags['loopHalted:'+tab.id]});
      } finally {driving.delete(tab.id);}
    }
    if(msg.payload.action==='reconnect') {
      if(!panel)throw Error('Reconnect observation is available in the full workspace only.');
      const info=await native(tab,{action:'connection_status'});
      if(!info.ok||!info.binding)throw Error(info.error||'Connect this conversation first.');
      const previous=info.binding.tab;
      if(driving.has(previous)||driving.has(tab.id))throw Error('A delivery is in progress. Wait for its result before reconnecting.');
      if(previous!==tab.id) {
        let old;try{old=await chrome.tabs.get(previous);}catch(_){}
        if(old?.url?.match(/\/c\/([\w-]+)/)?.[1]===tab.url.match(/\/c\/([\w-]+)/)?.[1])
          throw Error('This conversation is still connected in another open tab. Use that tab or close it before reconnecting here.');
      }
      const checked=await chrome.tabs.sendMessage(tab.id,{kind:'inspect'});
      const current=await chrome.tabs.get(tab.id);
      if(current.url!==tab.url||checked?.url!==tab.url)throw Error('Conversation changed while reconnecting. Refresh first.');
      const flags=await chrome.storage.local.get(['loopHalted:'+previous,'loopHalted:'+tab.id]);
      const result=await native(tab,{action:'reconnect',expected_tab:previous,expected_run_id:info.loop?.run_id||'',
        snapshot:checked,delivery_halted:!!(flags['loopHalted:'+previous]||flags['loopHalted:'+tab.id])});
      if(result.ok) {
        const {associations={}}=await chrome.storage.local.get('associations');
        const conversation=tab.url.match(/\/c\/([\w-]+)/)?.[1];
        for(const [id,url] of Object.entries(associations))if(url.match(/\/c\/([\w-]+)/)?.[1]===conversation)delete associations[id];
        associations[String(tab.id)]=tab.url;await chrome.storage.local.set({associations});
      }
      return result; // Reconnect itself never sends or clicks a chat message.
    }
    if(msg.payload.action==='loop_start') {
      if(driving.has(tab.id))throw Error('A delivery is already in progress.');
      await chrome.storage.local.remove('loopHalted:'+tab.id);
      driving.add(tab.id);
      try {return await deliver(tab,await native(tab,msg.payload));}
      finally {driving.delete(tab.id);}
    }
    if(msg.payload.action==='loop_stop')await chrome.storage.local.set({['loopHalted:'+tab.id]:true});
    const result = await native(tab,msg.payload);
    if(result.ok && sender.tab && !panel && msg.payload.snapshot && !driving.has(tab.id)) {
      driving.add(tab.id);
      try {
        const tick=await native(tab,{action:'loop_tick',snapshot:msg.payload.snapshot});
        if(tick.ok)await deliver(tab,tick);
      } finally {driving.delete(tab.id);}
    }
    if (result.ok && ['bind','unbind'].includes(msg.payload.action)) {
      const {associations = {}} = await chrome.storage.local.get('associations');
      if(msg.payload.action === 'bind') associations[String(tab.id)] = tab.url;
      else delete associations[String(tab.id)];
      await chrome.storage.local.set({associations});
    }
    return result;
  })().then(reply, e => reply({ok:false,error:e.message}));
  return true;
});
