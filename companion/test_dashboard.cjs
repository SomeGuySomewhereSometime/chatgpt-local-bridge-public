const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const code=fs.readFileSync(__dirname+'/extension/dashboard.js','utf8');
async function fixture(withWork=false){
 const elements={},storage={},noticeTimers=new Map();let timerId=0;let tick,claims=0,sends=0,starts=0,failClaim=false,slowStart=null,loseUpdateReply=false;
 const element=(tag='div')=>({tag,value:'',textContent:'',hidden:false,disabled:false,children:[],events:{},click(){this.clicked=true;},remove(){this.removed=true;},append(...x){this.children.push(...x)},replaceChildren(...x){this.children=x},querySelectorAll(selector){return selector==='button'?this.children.flatMap(x=>[...(x.tag==='button'?[x]:[]),...(x.querySelectorAll?.(selector)||[])]):this.children},addEventListener(n,f){this.events[n]=f},scrollIntoView(){}});
 const $=id=>elements[id]??=(element());
 const tabs=[{id:7,windowId:1,title:'Sueca',url:'https://chatgpt.com/c/test'},{id:8,title:'Other',url:'https://chatgpt.com/c/other'}];
 let diagnostics={available:true,enabled:false,run_id:'diagnostic-run',runs:[{run_id:'diagnostic-run',started:Date.now()/1000,enabled:false}]},download=null,lastStart=null;
 let selectedURL=tabs[0].url,loop=withWork?{state:'stopped',goal:'Improve cards',used:1,max_steps:1,started:Date.now()/1000,last_seen:Date.now()/1000,run_id:'prior-run',request_id:'old-request'}:null,last=null,page='idle';
 const progress={summary:'Cards updated',completed:['New cards'],pending:[{description:'Check mobile'}],unverified:['Mobile'],next_step:'Check mobile in browser',files:[{path:'style.css'}],validation:[{description:'Rules pass',status:'passed',evidence:'Rules test receipt'}]};
 let work=withWork?{task_id:'bridge-task',updates:[],criteria:['Rules pass'],context:{selected_revision:1,checkpoint:{state:'paused',source:'Model report',created_at:Date.now()/1000,progress}},receipts:[{operation:'run_command',state:'completed',returncode:0,request_id:'test-receipt',cwd:'/tmp'}],receipt_count:1,unresolved_receipts:0,evidence_checks:[{description:'Rules pass',state:'receipt_confirmed',detail:'Command completion only.'}]}:null;const binding={tab:7,conversation:'test',project:'/tmp',task:'',title:'Sueca'};
 const native=async m=>{const p=m.payload;
 if(['status','connection_status'].includes(p.action))return {ok:true,binding:tabs.find(t=>t.id===m.tab)?.url?.includes('/c/test')?binding:null,loop,work,requests:last?[last]:[],events:[],diagnostics};
 if(p.action==='diagnostics_set'){diagnostics={...diagnostics,enabled:p.enabled};return {ok:true,diagnostics};}
 if(p.action==='diagnostics_export'){return {ok:true,diagnostic_export:{run:{run_id:p.run_id},events:[]}};}
 if(p.action==='observe')return {ok:true};
 if(p.action==='verify_delivery'){last.state='observed_sent';loop={...loop,state:'completed',review_completed_revision:work.context.selected_revision};return {ok:true,verified:true,loop};}
 if(p.action==='reconnect'){binding.tab=m.tab;return {ok:true,binding,loop,continuation_retained:loop?.state==='waiting'};}
 if(p.action.startsWith('update_')){
  if(p.task_id!==work.task_id)return {ok:false,error:'The linked task changed.'};
  const row=work.updates.find(x=>x.id===p.id);
  if(p.action==='update_add'){
   if(row&&(row.text!==p.text||row.mode!==(p.mode||'next_reply')))return {ok:false,error:'Different update ID content'};
   if(!row)work.updates.push({id:p.id,text:p.text,mode:p.mode||'next_reply',revision:1,state:'queued',created:Date.now()/1000});
   if(loseUpdateReply){loseUpdateReply=false;throw Error('Lost update reply. Refresh before trying again.');}
  }else{
   if(!row||row.revision!==p.revision)return {ok:false,error:'The update changed. Refresh.'};
   if(['update_edit','update_cancel'].includes(p.action)&&row.state!=='queued')return {ok:false,error:'Only queued updates can be changed.'};
   if(p.action==='update_edit'){row.text=p.text;row.mode=p.mode||row.mode;row.revision++;}
   if(p.action==='update_cancel')row.state='cancelled';
   if(p.action==='update_reconcile'){row.state=p.resolution==='found'?'sent':'cancelled';row.confirmed_by=p.resolution==='found'?'user':'excluded';}
  }
  return {ok:true,updates:work.updates,loop};
 }

 if(p.action==='loop_start'){lastStart=p;if(p.resume&&p.resume.revision!==work.context.selected_revision)return {ok:false,error:'Checkpoint changed. Review the resume again.'};starts++;loop={state:'dispatching',goal:p.goal,used:0,max_steps:p.limit,started:Date.now()/1000,last_seen:Date.now()/1000,reason:'Sending'};if(slowStart)await slowStart;if(loop.state==='dispatching')loop={...loop,state:'waiting'};return {ok:true,loop,delivery:{state:'observed_sent'}};}
 if(p.action==='loop_stop'){loop={...loop,state:'stopped',reason:'Stopped'};return {ok:true,loop};}
 if(p.action==='work_resume_preview'){if(last?.state==='uncertain'&&p.ack_id!==last.id)return {ok:false,error:'Inspect uncertain delivery first.'};return {ok:true,work,goal:loop.goal,criteria:'Rules pass',resume:{run_id:loop.run_id,task_id:work.task_id,revision:work.context.selected_revision,ack_id:p.ack_id},checkpoint_text:'Checkpoint: Check mobile in browser'};}
 if(p.action==='work_save'){if(p.task_id!==work.task_id||p.revision!==work.context.selected_revision)return {ok:false,error:'Checkpoint revision conflict'};work.context.selected_revision++;return {ok:true,work};}
 if(p.action==='prepare'){last={...p,state:'pending',created:Date.now()/1000};return {ok:true,...last};}
 if(p.action==='claim'){claims++;last.state='uncertain';if(failClaim)throw Error('lost claim reply');return {ok:true,...last};}
 if(p.action==='result'){last.state=p.state;return {ok:true};}
 return {ok:true};};
 const context={Blob,URL:{createObjectURL:blob=>{download=blob;return 'blob:test';},revokeObjectURL:()=>{}},URLSearchParams,location:{search:'?tab=7'},Date,setInterval:f=>tick=f,setTimeout:f=>{noticeTimers.set(++timerId,f);return timerId;},clearTimeout:id=>noticeTimers.delete(id),crypto:{randomUUID:()=> 'request-test-123456'},document:{body:element(),getElementById:$,createElement:element},chrome:{runtime:{getManifest:()=>({version:'0.4.0'}),sendMessage:async m=>m.kind==='panel_list'?{ok:true,tabs,bindings:[binding]}:native(m)},storage:{local:{get:async k=>({[k]:storage[k]}),set:async v=>Object.assign(storage,v)}},tabs:{get:async id=>({...tabs.find(t=>t.id===id),url:(id===7||id===10)?selectedURL:tabs[1].url}),update:async()=>{},sendMessage:async (_,m)=>{if(m.kind==='inspect')return {url:selectedURL,state:page,document_id:'doc-test-1234567',user_count:1};sends++;return {state:'observed_sent'};}},windows:{update:async()=>{}}}};
 vm.runInNewContext(code,context);for(let i=0;i<15;i++)await new Promise(r=>setImmediate(r));
 return {$,context,download:()=>download,lastStart:()=>lastStart,expireNotices:()=>{for(const f of noticeTimers.values())f();noticeTimers.clear();},updates:()=>work.updates,loseUpdate:()=>loseUpdateReply=true,setWorkState:s=>work.context.checkpoint.state=s,setLoop:s=>loop={...loop,state:s},setLoopFields:fields=>loop={...loop,...fields},setReviewReport:()=>{work.context.checkpoint.state='completed';work.context.checkpoint.progress.completed.push('[BRIDGE_REVIEW:prior-run] Inspected final result.');},changeUpdate:(id,state)=>{const r=work.updates.find(x=>x.id===id);r.state=state;},reopen:()=>{const i=tabs.findIndex(t=>t.id===7);tabs[i]={...tabs[i],id:10};selectedURL=tabs[i].url;},tick:()=>tick(),setPage:p=>page=p,changeURL:u=>selectedURL=u,loseClaim:()=>failClaim=true,slow:p=>slowStart=p,changeRevision:()=>work.context.selected_revision++,uncertain:()=>last={id:'old-request',state:'uncertain',text:'Previous attempt'},counts:()=>({claims,sends,starts})};
}
module.exports=fixture;
if(require.main===module)(async()=>{
 let f=await fixture(true);f.setWorkState('completed');await f.tick();assert.equal(f.$('work-state').textContent,'Reported complete');assert.equal(f.$('run-state').textContent,'Automatic continuation stopped');f.setWorkState('blocked');await f.tick();assert.equal(f.$('work-state').textContent,'Reported blocked');
 f=await fixture();assert.equal(f.$('connection-badge').textContent,'Connected');
 f.$('goal').value='Improve cards';f.$('limit').value='2';await f.$('review-goal').onclick();assert.equal(f.counts().starts,0);assert.equal(f.$('goal-review').hidden,false);assert.match(f.$('goal-budget').textContent,/at most 2/);
 await Promise.all([f.$('start').onclick(),f.$('start').onclick()]);assert.equal(f.counts().starts,1);assert.equal(f.$('stop').disabled,false);
 await f.$('stop').onclick();assert.equal(f.$('run-state').textContent,'Automatic continuation stopped');
 f.$('goal').value='Unsaved changed goal';await f.tick();assert.equal(f.$('goal').value,'Unsaved changed goal');
 f=await fixture();f.$('goal').value='Test';f.$('limit').value='1';await f.$('review-goal').onclick();f.changeURL('https://chatgpt.com/c/wrong');await f.$('start').onclick();assert.equal(f.counts().starts,0);assert.match(f.$('notice').textContent,/changed/);f.expireNotices();assert.equal(f.$('notice').hidden,false);
 f=await fixture();f.$('goal').value='Test success notice';f.$('limit').value='1';await f.$('review-goal').onclick();assert.equal(f.$('notice').hidden,false);f.expireNotices();assert.equal(f.$('notice').hidden,true);assert.equal(f.$('goal-review').hidden,false);
 f=await fixture();f.$('message').value='Continue';await f.$('review-message').onclick();f.setPage('draft');await f.$('send').onclick();assert.equal(f.counts().claims,0);assert.equal(f.$('message-review').hidden,false);
 f.setPage('idle');f.loseClaim();await f.$('send').onclick();assert.equal(f.counts().claims,1);assert.equal(f.counts().sends,0);assert.equal(f.$('send').disabled,true);assert.match(f.$('delivery').textContent,/uncertain/);
 f=await fixture();f.$('goal').value='Before switching';await f.$('goal').events.input();await f.$('conversations').children[1].onclick();assert.equal(f.$('title').textContent,'Other');assert.equal(f.$('start').disabled,true);await f.$('conversations').children[0].onclick();assert.equal(f.$('goal').value,'Before switching');
 f=await fixture();f.$('goal').value='Test Stop';f.$('limit').value='1';await f.$('review-goal').onclick();let release;f.slow(new Promise(r=>release=r));const inFlight=f.$('start').onclick();for(let i=0;i<12;i++)await new Promise(r=>setImmediate(r));assert.equal(f.$('stop').disabled,false);await f.$('stop').onclick();release();await inFlight;assert.equal(f.$('run-state').textContent,'Automatic continuation stopped');
 f=await fixture(true);assert.match(f.$('reported-checks').children[0].textContent,/Reported/);assert.match(f.$('observed-operations').children[0].textContent,/Observed.*exit 0/);
 assert.match(f.$('reported-checks').children[0].children[0].textContent,/Command receipt confirmed/);
 await f.$('load-checkpoint').onclick();assert.equal(f.$('checkpoint-next').value,'Check mobile in browser');f.changeRevision();await f.$('save-checkpoint').onclick();assert.match(f.$('notice').textContent,/revision conflict/);assert.equal(f.$('checkpoint-next').value,'Check mobile in browser');
 f=await fixture(true);f.$('limit').value='1';f.uncertain();await f.$('resume-goal').onclick();assert.match(f.$('notice').textContent,/uncertain/);assert.equal(f.counts().starts,0);
 f.$('ack-uncertain').checked=true;await f.$('resume-goal').onclick();assert.match(f.$('resume-summary').textContent,/Check mobile/);assert.equal(f.counts().starts,0);assert.match(f.$('goal-budget').textContent,/NEW run/);
 f.changeRevision();await f.$('start').onclick();assert.equal(f.counts().starts,0);assert.match(f.$('notice').textContent,/changed/);
 f=await fixture(true);f.$('limit').value='1';await f.$('resume-goal').onclick();await Promise.all([f.$('start').onclick(),f.$('start').onclick()]);assert.equal(f.counts().starts,1);
 f=await fixture(true);f.setPage('generating');f.reopen();await f.$('refresh').onclick();assert.equal(f.$('connection-badge').textContent,'Reconnect required');assert.equal(f.$('reconnect').disabled,false);await f.$('reconnect').onclick();assert.equal(f.$('connection-badge').textContent,'Connected');assert.equal(f.$('page-state').textContent,'Responding');assert.deepEqual(f.counts(),{claims:0,sends:0,starts:0});assert.match(f.$('notice').textContent,/no message/i);
 console.log('PASS dashboard: review destination, double-click guard, changed URL, draft preservation, lost claim, saved drafts and Stop during Start');
})().catch(e=>{console.error(e);process.exitCode=1;});
