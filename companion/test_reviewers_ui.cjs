const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const fixture=require('./test_dashboard.cjs');
(async()=>{
 const f=await fixture(true),purposes=['plan','code','tests','visual','question'];
 const create=f.context.document.createElement;
 f.context.document.createElement=tag=>Object.assign(create(tag),{dataset:{}});
 const inputs=p=>f.$('review-model-'+p).children.map(row=>row.children.find(x=>x.tag==='input'));
 const choice=(p,id)=>inputs(p).find(input=>input.value===id);
 const selected=p=>inputs(p).filter(input=>input.checked).map(input=>input.value);
 const toggle=(p,id,checked)=>{const input=choice(p,id);input.checked=checked;input.events.change();};
 for(const p of purposes)f.$('review-model-'+p).querySelectorAll=()=>inputs(p);
 let catalogueModels=[{id:'opencode/free-model',name:'Free model',inputs:['text','image']},{id:'opencode/other-model',name:'Other model',inputs:['text','image']}];
 let catalogueGate=null,saveGate=null;
 let r={policy:{version:0,settings:{enabled:false,mode:'automatic',stages:['plan','code'],models:Object.fromEntries(purposes.map(p=>[p,['opencode/free-model']])),concurrency:3,limit:0,timeout:600,tools:'snapshots',context_budget_bytes:524288}},task_id:'bridge-task',next_policy_version:0,jobs:[]};
 let detailBody=JSON.stringify({packet:{purpose:'code',strategy:'critique',question:'Question',reason:'Reason',context:'Context',files:[],images:[]},result:{response:'Full response '+'.'.repeat(26000),findings:[{id:'finding-1',description:'Actual finding',severity:'medium',evidence:'code.py:2',recommendation:'Investigate'}]},decision:null});
 let calls=[];const send=f.context.chrome.runtime.sendMessage;
 f.context.chrome.runtime.sendMessage=async m=>{
  if(m.kind==='native'){
   const p=m.payload;
   if(p.action.startsWith('reviews_')){
    calls.push(p);
    if(p.action==='reviews_configure'){if(saveGate)await saveGate;r={...r,policy:{version:r.policy.version+1,settings:p.settings}};return {ok:true,reviewers:r};}
    if(p.action==='reviews_catalog'){if(catalogueGate)await catalogueGate;return {ok:true,catalogue:{models:catalogueModels,stale:false,updated:Date.now()/1000}};}
    if(p.action==='reviews_request')return {ok:true,reviews:{jobs:[]}};
    if(p.action==='reviews_detail'){const offset=p.offset||0,end=Math.min(detailBody.length,offset+24000);return {ok:true,review_detail:{id:p.job_id,text:detailBody.slice(offset,end),version:'v1',next_offset:end<detailBody.length?end:null,total_characters:detailBody.length}};}
    return {ok:true};
   }
   const result=await send(m);if(['status','connection_status'].includes(p.action))result.reviewers=r;return result;
  }
  return send(m);
 };
 vm.runInNewContext(fs.readFileSync(__dirname+'/extension/reviewers.js','utf8'),f.context);
 await f.tick();assert.match(f.$('review-policy-state').textContent,/OFF/);assert.equal(calls.length,0);
 f.$('review-enabled').checked=true;f.$('review-enabled').events.change();f.$('review-mode').value='required';f.$('review-mode').events.change();
 await f.tick();assert.equal(f.$('review-mode').value,'required');assert.match(f.$('review-policy-state').textContent,/Unsaved/);
 f.$('review-tools').value='plan';f.$('review-tools').events.change();
 await f.$('review-save').onclick();assert.equal(calls[0].settings.tools,'plan');assert.equal(calls[0].action,'reviews_configure');assert.equal(calls[0].settings.mode,'required');assert.equal(calls[0].settings.enabled,true);assert.equal(calls.filter(c=>c.action==='reviews_request').length,0);
 await f.$('review-refresh').onclick();assert.match(f.$('review-catalogue-state').textContent,/2 zero-cost/);assert.deepEqual(selected('code'),['opencode/free-model']);
 toggle('code','opencode/other-model',true);assert.deepEqual(selected('code'),['opencode/free-model','opencode/other-model']);assert.equal(f.$('review-model-count-code').textContent,'2 selected');
 f.$('review-model-search').value='other';f.$('review-model-search').events.input();
 assert.equal(f.$('review-model-code').children[0].hidden,true);assert.equal(f.$('review-model-code').children[1].hidden,false);
 await f.tick();assert.deepEqual(selected('code'),['opencode/free-model','opencode/other-model']);
 await f.$('review-save').onclick();assert.deepEqual(JSON.parse(JSON.stringify(r.policy.settings.models.code)),['opencode/free-model','opencode/other-model']);
 let release;catalogueGate=new Promise(resolve=>release=resolve);const refreshing=f.$('review-refresh').onclick();await new Promise(resolve=>setImmediate(resolve));
 toggle('code','opencode/free-model',false);release();await refreshing;catalogueGate=null;assert.deepEqual(selected('code'),['opencode/other-model']);
 await f.$('review-save').onclick();
 toggle('code','opencode/free-model',true);saveGate=new Promise(resolve=>release=resolve);const saving=f.$('review-save').onclick();await new Promise(resolve=>setImmediate(resolve));
 toggle('code','opencode/free-model',false);release();await saving;saveGate=null;
 assert.deepEqual(selected('code'),['opencode/other-model']);assert.match(f.$('review-policy-state').textContent,/Unsaved/);
 await f.$('review-save').onclick();assert.deepEqual(JSON.parse(JSON.stringify(r.policy.settings.models.code)),['opencode/other-model']);
 f.$('review-model-search').value='';f.$('review-model-search').events.input();
 catalogueModels.push(...Array.from({length:7},(_,i)=>({id:'opencode/extra-'+i,name:'Extra '+i,inputs:['text']})));
 await f.$('review-refresh').onclick();assert.equal(inputs('visual').length,2);
 for(const input of inputs('code'))if(!input.checked)toggle('code',input.value,true);
 assert.equal(selected('code').length,8);assert.match(f.$('notice').textContent,/up to 8 models/);
 await f.$('review-save').onclick();assert.equal(calls.filter(c=>c.action==='reviews_request').length,0);
 f.$('review-question').value='Find an actual bug in division';f.$('review-justification').value='Division behavior is user-visible';f.$('review-purpose').value='code';f.$('review-strategy').value='critique';f.$('review-context').value='Requirements';f.$('review-files').value='code.py:2:9';f.$('review-images').value='';
 await f.$('review-request').onclick();const requested=calls.find(c=>c.action==='reviews_request');assert.equal(requested.task_id,'bridge-task');assert.deepEqual(JSON.parse(JSON.stringify(requested.files)),[{path:'code.py',start_line:2,end_line:9}]);
 r.jobs=[{id:'job',purpose:'code',model:'opencode/free-model',state:'failed',error:'Provider unavailable',findings:[],summary:'',created:Date.now()/1000,stale_reason:''}];
 await f.tick();assert.match(f.$('review-jobs').children[0].children.map(x=>x.textContent).join(' '),/Provider unavailable/);
 r.jobs=[{id:'full-job',purpose:'code',model:'opencode/free-model',state:'completed',findings:[],summary:'Short summary',created:Date.now()/1000,stale_reason:''}];
 await f.tick();let card=f.$('review-jobs').children[0];const fullButton=card.children.find(x=>x.textContent==='View full review');await fullButton.onclick();
 assert.equal(calls.filter(x=>x.action==='reviews_detail').length,2);
 const fullBox=card.children.at(-1);assert.match(fullBox.children[0].textContent,/Full response/);
 await f.tick();assert.equal(f.$('review-jobs').children[0],card);
 const investigate=fullBox.children.find(x=>x.textContent==='Ask ChatGPT to investigate finding-1');await investigate.onclick();assert.equal(calls.find(x=>x.action==='reviews_investigate').finding_id,'finding-1');
 r.jobs[0].decision={summary:'Updated assessment',findings:[]};detailBody=JSON.stringify({...JSON.parse(detailBody),decision:r.jobs[0].decision});await f.tick();card=f.$('review-jobs').children[0];await card.children.find(x=>x.textContent==='View full review').onclick();
 assert.equal(calls.filter(x=>x.action==='reviews_detail').length,4);assert.match(card.children.at(-1).children.at(-1).children[1].textContent,/Updated assessment/);
 assert.equal(calls.filter(x=>x.action==='reviews_request').length,1);
 f.$('review-enabled').checked=false;f.$('review-enabled').events.change();await f.$('review-save').onclick();assert.equal(r.policy.settings.enabled,false);assert.equal(f.$('review-request').disabled,true);
 console.log('reviewer UI: checkbox selection, filtering, refresh/save races, eight-model limit, Off and task-scoped requests passed');
})().catch(e=>{console.error(e);process.exit(1)});
