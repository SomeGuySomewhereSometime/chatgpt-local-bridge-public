const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/extension/popup.js','utf8');
async function fixture() {
 const elements={};const $=id=>elements[id]??=( {value:'',textContent:'',disabled:false,hidden:false,open:false});
 let state='idle',claims=0,sends=0,last=null,failClaim=false,loop=null;
 const tab={id:7,title:'Sueca',url:'https://chatgpt.com/c/test'};
 const calls=[];
 const context={setInterval(){},document:{getElementById:$},crypto:{randomUUID:()=> 'test-request-123456'},chrome:{
 storage:{local:{get:async()=>({}),set:async()=>{}}},
 tabs:{query:async()=>[tab],get:async()=>tab,sendMessage:async(_,m)=>{if(m.kind==='inspect')return {url:tab.url,state,document_id:'popup-document-123456',user_count:0,terminal:true};sends++;return {state:'observed_sent'};}},
 runtime:{sendMessage:async m=>{calls.push(m.payload.action);switch(m.payload.action){
 case 'status':return {ok:true,binding:{project:'/tmp',task:''},loop,requests:last?[last]:[]};
 case 'loop_start':loop={goal:m.payload.goal,state:'waiting',used:0,max_steps:m.payload.limit,reason:'Waiting'};return {ok:true,loop,delivery:{state:'observed_sent'}};
 case 'loop_stop':loop={...loop,state:'stopped',reason:'Stopped by user'};return {ok:true,loop};
 case 'prepare':last={...m.payload,state:'pending'};return {ok:true,...last};
 case 'claim':claims++;if(failClaim)throw Error('lost reply');return {ok:true,...last};
 case 'result':last.state=m.payload.state;return {ok:true};default:return {ok:true};
 }}}
 }};
 vm.runInNewContext(source,context);
 for(let i=0;i<12;i++)await new Promise(r=>setImmediate(r));
 return {$,context,calls,counts:()=>({claims,sends}),setState:s=>state=s,loseClaim:()=>failClaim=true};
}
(async()=>{
 let f=await fixture();assert.equal(f.$('connection').textContent,'Connected to: /tmp');assert.equal(f.$('task').value,'');
 f.$('text').value='Continue';await f.$('preview').onclick();assert.equal(f.$('send').disabled,false);
 f.setState('draft');await f.$('send').onclick();assert.equal(f.counts().claims,0);assert.equal(f.$('cancel').disabled,false);assert.equal(f.$('send').disabled,false);
 f.setState('idle');await Promise.all([f.$('send').onclick(),f.$('send').onclick()]);assert.deepEqual(f.counts(),{claims:1,sends:1});assert.match(f.$('delivery').textContent,/Message sent/);assert.equal(f.$('send').disabled,true);
 f=await fixture();f.$('text').value='Continue';await f.$('preview').onclick();f.loseClaim();await f.$('send').onclick();assert.deepEqual(f.counts(),{claims:1,sends:0});assert.equal(f.$('send').disabled,true);assert.match(f.$('delivery').textContent,/uncertain/);
 f=await fixture();await f.$('unbind').onclick();assert.equal(f.$('preview').disabled,true);assert.equal(f.$('connection').textContent,'Not connected');
 f=await fixture();f.$('goal').value='Verify rules';f.$('limit').value='3';await f.$('goal-review').onclick();assert.equal(f.calls.includes('loop_start'),false);assert.match(f.$('goal-summary').textContent,/at most 3/);assert.equal(f.$('goal-start').disabled,false);
 await f.$('goal-start').onclick();assert.equal(f.calls.filter(c=>c==='loop_start').length,1);assert.match(f.$('loop-status').textContent,/0\/3/);assert.equal(f.$('preview').disabled,true);assert.equal(f.$('goal-stop').disabled,false);
 await f.$('goal-stop').onclick();assert.match(f.$('loop-status').textContent,/Stopped/);assert.equal(f.$('goal-review').disabled,false);
 console.log('PASS popup: optional reference, draft-after-preview recovery, double-click guard, lost claim, disconnect');
})().catch(e=>{console.error(e);process.exitCode=1;});
