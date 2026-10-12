const vm=require('node:vm'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto'),cp=require('node:child_process'),assert=require('node:assert/strict');
const root=fs.mkdtempSync(path.join(os.tmpdir(),'loop-background-'));
const manifest=JSON.parse(fs.readFileSync(__dirname+'/extension/manifest.json'));
const id=[...crypto.createHash('sha256').update(Buffer.from(manifest.key,'base64')).digest('hex').slice(0,32)].map(c=>String.fromCharCode(97+parseInt(c,16))).join('');
const bridgeConfig=path.join(root,'bridge-config.json');
fs.writeFileSync(bridgeConfig,JSON.stringify({memory:{enabled:true,path:path.join(root,'private/memory.sqlite3')},filesystem:{denied_paths:[path.join(root,'private')]},operations:{directory:path.join(root,'operations')}}));
const store={client:'integration-client'};let listener, sentTexts=[], sends=0,offline=false,mark='PENDING',slow=null,diagnosticStorageFails=false;
let tab={id:1,title:'Loop fixture',url:'https://chatgpt.com/c/integration-test'};
let openTabs=[tab],verificationChanged=false;
let snap={document_id:'test-document-123456',state:'idle',user_count:0,terminal:true,marker:null};
function host(p) {
 if(offline)throw Error('Native transport unavailable');
 const raw=Buffer.from(JSON.stringify(p)),header=Buffer.alloc(4);header.writeUInt32LE(raw.length);
 const r=cp.spawnSync('/usr/bin/python3',['-B',__dirname+'/native_host.py',`chrome-extension://${id}/`],{input:Buffer.concat([header,raw]),env:{...process.env,HOME:root,BRIDGE_CONFIG:bridgeConfig}});
 assert.equal(r.status,0,r.stderr.toString());const n=r.stdout.readUInt32LE(0);assert.equal(n,r.stdout.length-4);return JSON.parse(r.stdout.subarray(4));
}
const mock={crypto:{randomUUID:()=>crypto.randomUUID()},chrome:{storage:{local:{get:async k=>Object.fromEntries((Array.isArray(k)?k:[k]).map(key=>[key,store[key]])),set:async v=>{if(diagnosticStorageFails&&'bridgeDiagnostics' in v)throw Error('Diagnostic storage unavailable');return Object.assign(store,v);},remove:async k=>delete store[k]}},tabs:{query:async()=>openTabs,get:async id=>{const t=openTabs.find(t=>t.id===id);if(!t)throw Error('Tab closed');return t;},sendMessage:async(_,m)=>{
 if(m.kind==='verify_delivery')return {ok:true,verified:true,snapshot:{...snap,url:tab.url,last_user_id:'last-user-fixture',last_user_text:sentTexts.at(-1)}};
 if(m.kind==='inspect')return {...snap,url:tab.url,last_user_id:verificationChanged?'changed-user-fixture':'last-user-fixture',last_user_text:sentTexts.at(-1)};
 if(slow)await slow;
 const validation=await call({kind:'loop_validate',id:m.id},{tab});
 if(!validation.allowed)return {state:'uncertain'};
 sends++;sentTexts.push(m.text);const nonce=m.text.match(/BRIDGE_LOOP:([a-f0-9]{32}):/)[1];
 snap={...snap,user_count:snap.user_count+1,marker:{nonce,decision:mark}};
 return {state:'observed_sent'};
}},runtime:{getManifest:()=>manifest,getURL:p=>'chrome-extension://'+id+'/'+p,onMessage:{addListener:f=>listener=f},sendNativeMessage:async(_,p)=>host(p)}}};
vm.runInNewContext(fs.readFileSync(__dirname+'/extension/background.js','utf8'),mock);
function call(msg,sender={}){return new Promise(resolve=>listener(msg,sender,resolve));}
const popup=p=>call({kind:'native',tab:tab.id,url:tab.url,payload:p});
const beat=()=>call({kind:'native',payload:{action:'observe',state:snap.state,snapshot:snap}},{tab});
(async()=>{
 assert.equal((await popup({action:'bind',project:root,task:''})).ok,true);await beat();
 const start=await popup({action:'loop_start',goal:'Test only',limit:2,snapshot:snap});assert.equal(start.ok,true);assert.equal(sends,1);
 await Promise.all([beat(),beat()]);assert.equal(sends,2);await beat();assert.equal(sends,3);await beat();
 assert.equal((await popup({action:'status'})).loop.state,'limit_reached');await beat();assert.equal(sends,3);
 mark='DONE';await popup({action:'loop_start',goal:'Second run',limit:1,snapshot:snap});await beat();assert.equal((await popup({action:'status'})).loop.state,'completed');
 mark='PENDING';await popup({action:'loop_start',goal:'Transport failure',limit:1,snapshot:snap});const prior=sends;offline=true;assert.equal((await beat()).ok,false);offline=false;await beat();assert.equal(sends,prior);assert.equal((await popup({action:'status'})).loop.state,'stopped');

 const failedStatus=await popup({action:'status'});
 const exportedFailure=await popup({action:'diagnostics_export',run_id:failedStatus.loop.run_id});
 assert.equal(exportedFailure.ok,true,exportedFailure.error);
 assert.ok(exportedFailure.diagnostic_export.browser.events.some(e=>e.code==='native_transport'));
 assert.equal(JSON.stringify(exportedFailure.diagnostic_export).includes('Transport failure'),false);
 assert.equal(exportedFailure.diagnostic_export.environment.coordinator_version,manifest.version);
 offline=true;const partial=await popup({action:'diagnostics_export',run_id:failedStatus.loop.run_id});offline=false;assert.equal(partial.ok,true,partial.error);assert.equal(partial.diagnostic_export.partial,true);assert.equal(partial.diagnostic_export.native.unavailable,true);assert.equal(sends,prior);
 // Browser logging failure must neither mask the transport error nor revive a run.
 diagnosticStorageFails=true;offline=true;
 assert.equal((await beat()).ok,false);offline=false;diagnosticStorageFails=false;
 await beat();assert.equal(sends,prior);
 let release;slow=new Promise(r=>release=r);const starting=popup({action:'loop_start',goal:'Stop while sending',limit:1,snapshot:snap});
 for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r));
 await popup({action:'loop_stop'});release();await starting;slow=null;assert.equal(sends,prior);assert.equal((await popup({action:'status'})).loop.state,'stopped');
 assert.equal((await popup({action:'loop_start',goal:'No content permission',limit:1,snapshot:snap})).ok,true);
 const denied=await call({kind:'native',payload:{action:'loop_start',goal:'forged',limit:1,snapshot:snap}},{tab});assert.equal(denied.ok,false);
 await popup({action:'loop_stop'});
 const panelSender={url:'chrome-extension://'+id+'/dashboard.html',tab:{id:9,url:'chrome-extension://'+id+'/dashboard.html'}};
 const overview=await call({kind:'panel_list'},panelSender);assert.equal(overview.ok,true);assert.equal(overview.bindings.length,1);
 const panelCall=p=>call({kind:'native',tab:tab.id,url:tab.url,payload:p},panelSender);
 await panelCall({action:'loop_start',goal:'Panel run',limit:1,snapshot:snap});assert.equal((await panelCall({action:'status'})).loop.state,'waiting');
 await panelCall({action:'loop_stop'});assert.equal((await panelCall({action:'status'})).loop.state,'stopped');
 // Exercise enriched workspace through real native subprocesses and the same existing Bridge schema.
 mark='DONE';const prepared=await panelCall({action:'loop_start',goal:'Quality fixture',criteria:'Text-only task completes',work_request_id:crypto.randomUUID(),limit:1,snapshot:snap});
 assert.equal(prepared.ok,true,prepared.error);const taskId=prepared.loop.work_task_id;assert.ok(taskId);
 await beat();assert.equal((await panelCall({action:'status'})).loop.feedback_count,1);
 await beat();assert.equal((await panelCall({action:'status'})).loop.state,'review_required');
 const saveCode=`import sys,json,uuid;sys.path.insert(0,sys.argv[1]);from work_context import WorkContext;w=WorkContext();p=sys.argv[2];tid=sys.argv[3];w.store.checkpoint(p,task_id=tid,expected_revision=0,checkpoint={'summary':'Fixture result recorded','completed':['Text-only criterion met'],'validation':[{'description':'Text-only task completes','status':'passed','evidence':'Fixture result recorded'}]},state='completed',source='Native integration fixture',request_id=uuid.uuid4().hex)`;
 const saved=cp.spawnSync('/usr/bin/python3',['-B','-c',saveCode,__dirname,root,taskId],{env:{...process.env,HOME:root,BRIDGE_CONFIG:bridgeConfig}});assert.equal(saved.status,0,saved.stderr.toString());
 const reviewed=await panelCall({action:'work_resume_preview'});assert.equal(reviewed.ok,true,reviewed.error);assert.equal(reviewed.work.context.selected_revision,1);
 mark='DONE';const resumed=await panelCall({action:'loop_start',goal:'Quality fixture',limit:1,snapshot:snap,resume:reviewed.resume});assert.equal(resumed.ok,true,resumed.error);
 await beat();const completed=await panelCall({action:'status'});assert.equal(completed.loop.state,'completed');assert.equal(completed.work.task_id,taskId);
 assert.equal(completed.work.context.checkpoint.source,'Native integration fixture');
 const beforeUpdates=sends;
 const queued=await panelCall({action:'update_add',task_id:taskId,id:crypto.randomUUID(),text:'Preserve existing behavior; inspect before changing it.'});
 assert.equal(queued.ok,true,queued.error);assert.equal(sends,beforeUpdates);assert.equal(queued.loop.state,'updates_pending');
 assert.equal((await call({kind:'native',payload:{action:'update_add',task_id:taskId,id:crypto.randomUUID(),text:'forged'}},{tab})).ok,false);
 const updateResume=await panelCall({action:'work_resume_preview'});assert.equal(updateResume.ok,true,updateResume.error);
 mark='PENDING';const updateRun=await panelCall({action:'loop_start',goal:'Quality fixture',limit:2,snapshot:snap,resume:updateResume.resume});
 assert.equal(updateRun.ok,true,updateRun.error);assert.match(sentTexts.at(-1),/Preserve existing behavior/);
 assert.equal((await panelCall({action:'status'})).work.updates[0].state,'sent');
 const saveAgain=cp.spawnSync('/usr/bin/python3',['-B','-c',saveCode.replace('expected_revision=0','expected_revision=1'),__dirname,root,taskId],{env:{...process.env,HOME:root,BRIDGE_CONFIG:bridgeConfig}});assert.equal(saveAgain.status,0,saveAgain.stderr.toString());
 mark='DONE';await beat();assert.match(sentTexts.at(-1),/previously sent instruction/);await beat();
 assert.equal((await panelCall({action:'status'})).loop.state,'completed');

 // Observation handoff uses real native transactions, with simulated Chrome lifecycle.
 mark='PENDING';const recovery=await panelCall({action:'loop_start',goal:'Reconnect fixture',limit:2,snapshot:snap});
 assert.equal(recovery.ok,true,recovery.error);const beforeReconnect=sends;
 snap={...snap,state:'generating',terminal:false};
 tab.windowId=5;const moved=await panelCall({action:'reconnect'});
 assert.equal(moved.ok,true,moved.error);assert.equal(moved.continuation_retained,true);assert.equal(sends,beforeReconnect);
 const oldTab=tab;tab={...oldTab,id:2};openTabs=[oldTab,tab];
 const duplicate=await panelCall({action:'reconnect'});assert.equal(duplicate.ok,false);assert.match(duplicate.error,/another open tab/);assert.equal(sends,beforeReconnect);
 openTabs=[tab];snap={...snap,document_id:'reopened-document-123456'};
 const reopened=await panelCall({action:'reconnect'});assert.equal(reopened.ok,true,reopened.error);assert.equal(reopened.continuation_retained,true);
 assert.equal(reopened.loop.run_id,recovery.loop.run_id);assert.equal(reopened.loop.used,0);assert.equal(sends,beforeReconnect);
 assert.equal(store.associations['1'],undefined);assert.equal(store.associations['2'],tab.url);
 const oldBeat=await call({kind:'native',payload:{action:'observe',state:'completed',snapshot:snap}},{tab:oldTab});assert.equal(oldBeat.ignored,true);assert.equal(sends,beforeReconnect);
 snap={...snap,state:'completed',terminal:true};await beat();assert.equal(sends,beforeReconnect+1);
 await panelCall({action:'loop_stop'});const stopped=await panelCall({action:'reconnect'});assert.equal(stopped.ok,true,stopped.error);assert.equal(stopped.continuation_retained,false);
 await beat();assert.equal(sends,beforeReconnect+1);
 assert.equal((await popup({action:'reconnect'})).ok,false); // Full workspace only.
 // Final review crosses the real native-host protocol; no live ChatGPT message is sent.
 mark='DONE';const finalRun=await panelCall({action:'loop_start',goal:'Final review fixture',criteria:'Text-only task completes',work_request_id:crypto.randomUUID(),final_review:true,limit:1,snapshot:snap});
 assert.equal(finalRun.ok,true,finalRun.error);
 const writeReview=(revision,ack)=>{
  const script=saveCode.replace('expected_revision=0','expected_revision='+revision).replace("'Text-only criterion met'",JSON.stringify(ack));
  const result=cp.spawnSync('/usr/bin/python3',['-B','-c',script,__dirname,root,finalRun.loop.work_task_id],{env:{...process.env,HOME:root,BRIDGE_CONFIG:bridgeConfig}});
  assert.equal(result.status,0,result.stderr.toString());
 };
 writeReview(0,'Implementation complete');const sendsBeforeReview=sends;
 await Promise.all([beat(),beat()]);assert.equal(sends,sendsBeforeReview+1);
 const reviewRun=await panelCall({action:'status'});
 assert.equal(reviewRun.loop.review_started,1);assert.equal(reviewRun.loop.review_revision,1);assert.equal(reviewRun.loop.used,1);
 assert.match(sentTexts.at(-1),/FINAL REVIEW —/);
 writeReview(1,'[BRIDGE_REVIEW:'+finalRun.loop.run_id+'] Inspected fixture result and relevant checks; no defects found.');
 await beat();const reviewedRun=await panelCall({action:'status'});
 assert.equal(reviewedRun.loop.state,'completed');assert.equal(reviewedRun.loop.review_completed_revision,2);
 await beat();assert.equal(sends,sendsBeforeReview+1);
 // Reconcile a real uncertain receipt through the background without another send.
 mark='DONE';const repairRun=await panelCall({action:'loop_start',goal:'Delivery recovery fixture',criteria:'Text-only task completes',work_request_id:crypto.randomUUID(),limit:1,snapshot:snap});
 assert.equal(repairRun.ok,true,repairRun.error);
 const repairWrite=cp.spawnSync('/usr/bin/python3',['-B','-c',saveCode,__dirname,root,repairRun.loop.work_task_id],{env:{...process.env,HOME:root,BRIDGE_CONFIG:bridgeConfig}});assert.equal(repairWrite.status,0,repairWrite.stderr.toString());
 // Simulate a lost confirmation; retain the observed Chrome message.
 await panelCall({action:'loop_result',id:repairRun.loop.request_id,state:'uncertain'});
 // observed_sent receipts cannot be downgraded; use a fresh native fixture receipt with uncertainty.
 host({action:'loop_stop',url:tab.url,tab:tab.id,client:store.client});
 const markUncertain=`import sqlite3,sys;d=sqlite3.connect(sys.argv[1]);d.execute("UPDATE requests SET state='uncertain' WHERE id=?",(sys.argv[2],));d.execute("UPDATE goal_loops SET state='stopped',reason='Delivery uncertain. No retry.' WHERE request_id=?",(sys.argv[2],));d.commit()`;
 const dbFix=cp.spawnSync('/usr/bin/python3',['-c',markUncertain,root+'/.local/state/bridge-companion/state.sqlite3',repairRun.loop.request_id]);assert.equal(dbFix.status,0,dbFix.stderr.toString());
 const beforeRepair=sends;
 const repair=await panelCall({action:'verify_delivery',id:repairRun.loop.request_id,run_id:repairRun.loop.run_id,snapshot:{last_user_text:'forged'}});
 assert.equal(repair.ok,true,repair.error);assert.equal(repair.verified,true);assert.equal(repair.loop.state,'completed');assert.equal(sends,beforeRepair);
 await beat();assert.equal(sends,beforeRepair);
 verificationChanged=true;const staleProof=await panelCall({action:'verify_delivery',id:repairRun.loop.request_id,run_id:repairRun.loop.run_id});verificationChanged=false;assert.equal(staleProof.ok,false);assert.match(staleProof.error,/conversation changed during verification/);assert.equal(sends,beforeRepair);
 assert.equal((await popup({action:'verify_delivery',id:repairRun.loop.request_id,run_id:repairRun.loop.run_id})).ok,false);
 assert.equal((await call({kind:'native',payload:{action:'verify_delivery',snapshot:snap}},{tab})).ok,false);
 // Bounded persistent browser metadata; storage includes no goal, error or output text.
 for(let i=0;i<220;i++)await call({kind:'native',tab:tab.id,url:tab.url,payload:{action:'prepare',id:'safe-id-'+i,text:''}});
 const debugExport=await panelCall({action:'diagnostics_export',run_id:(await panelCall({action:'status'})).loop.run_id});
 assert.equal(debugExport.ok,true,debugExport.error);
 assert.equal(store.bridgeDiagnostics.length,200);
 assert.equal(JSON.stringify(store.bridgeDiagnostics).includes('Choose'),false);
 assert.equal(JSON.stringify(store.bridgeDiagnostics).includes('Quality fixture'),false);
 assert.ok(store.bridgeDiagnostics.every(e=>Object.keys(e).every(k=>['created','conversation','run_id','action','code','request_id','metadata'].includes(k))));
 assert.equal((await call({kind:'panel_list'},{tab})).ok,false);
 assert.equal((await call({kind:'panel_disconnect',url:tab.url},{tab})).ok,false);
 assert.equal((await call({kind:'panel_disconnect',url:tab.url},panelSender)).ok,true);
 console.log('PASS real native-host framing + background: bounded continuations, concurrent ticks, DONE, transport latch, Stop during send, sender restrictions');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>fs.rmSync(root,{recursive:true,force:true}));
