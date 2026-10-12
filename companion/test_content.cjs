const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const code=fs.readFileSync(__dirname+'/extension/content.js','utf8');
function setup({draft='',status='Resposta concluída',stop=false,dialog=false,history=false,answer='Historical answer',permit=true,multiline=false,corrupt=false,missingBody=false,wrongBody=false,virtualized=false,autolink=false,slowClock=0,observedBody=null,richBody=null,collapsed=false,expandTo=null,expandNoEffect=false,existingText=null,sameId=false}={}) {
 let listener,clickListener,clicked=0,inserted=0,expanded=0,turns=[],clock=0,polls=0;
 if(virtualized)turns.push({textContent:'You said:',parentElement:{querySelector:()=>({getAttribute:()=> 'old-message'}),querySelectorAll:()=>[{innerText:'Old message'}]}});
 const geometry={getClientRects:()=>[{}]};
 const edit={...geometry,textContent:draft,innerText:draft,focus(){},closest:()=>({querySelectorAll:()=>stop?[stopButton]:[sendButton]})};
 const makeMessage=text=>{
  let full=observedBody??(wrongBody?'Wrong message':autolink?text.replace(/http:\/\/127\.0\.0\.1:8765\//g,'[http://127.0.0.1:8765/](http://127.0.0.1:8765/)'):text);
  const body={...(richBody||{}),innerText:collapsed?full.slice(0,10240):full};
  const more={...geometry,disabled:false,getAttribute:()=> 'Mostrar mais',click(){expanded++;if(!expandNoEffect)body.innerText=expandTo??full;}};
  return {textContent:'O utilizador disse:',parentElement:{querySelector:()=>({getAttribute:()=>sameId?'old-message':'sent-message-'+clicked}),textContent:'O utilizador disse:'+text,innerText:'O utilizador disse:\n'+text,querySelectorAll:selector=>selector==='button'?(collapsed?[more]:[]):missingBody?[]:[body]}};
 };
 if(existingText!==null)turns.push(makeMessage(existingText));
 const button=label=>({...geometry,getAttribute:()=>label,textContent:'',disabled:false,click(){clicked++;if(virtualized)turns=[];turns.push(makeMessage(edit.innerText));edit.textContent='';edit.innerText='';}});
 const stopButton=button('Parar'),sendButton=button('Enviar');
 const document={querySelector:()=>edit,querySelectorAll(selector){if(selector.startsWith('[contenteditable'))return [{textContent:'hidden unrelated draft',getClientRects:()=>[]},edit];if(selector==='[data-talvt-turn-state]')return history?[{getAttribute:()=> 'complete',querySelector:()=>null,querySelectorAll:s=>s==='h4'?[{textContent:'O ChatGPT disse:',parentElement:{cloneNode:()=>({querySelectorAll:()=>[],textContent:answer})}}]:[button('Gerar resposta novamente')]}]:[];if(selector==='form button')return stop?[stopButton]:[sendButton];if(selector==='h4')return turns;if(selector.includes('dialog'))return dialog?[button('Permitir')]:[];return [{textContent:status}];},addEventListener(_,fn){clickListener=fn;},execCommand(_,__,text){inserted++;edit.textContent=multiline?text.replace(/\n/g,''):text;edit.innerText=multiline?text.replace(/\n/g,'\n\n'):text;if(corrupt)edit.innerText+=' unexpected';return true;}};
 const sandbox={crypto:{randomUUID:()=> 'document-test-12345678'},document,getComputedStyle:()=>({visibility:'visible'}),location:{href:'https://chatgpt.com/c/test'},Date:slowClock?{now:()=>clock}:Date,setInterval(){},setTimeout(fn,ms){if(ms===250)polls++;clock+=slowClock||ms;fn();},chrome:{runtime:{id:'test',onMessage:{addListener(fn){listener=fn;}},sendMessage:async()=>({allowed:permit})}}};vm.runInNewContext(code,sandbox);
 return {call:msg=>new Promise(resolve=>listener(msg,{id:'test'},resolve)),expanded:()=>expanded,counts:()=>({clicked,inserted}),polls:()=>polls,stop:()=>clickListener({target:{closest:()=>stopButton}})};
}
module.exports=setup;
if(require.main===module)(async()=>{
 for(const [opts,state] of [[{draft:'my draft'},'draft'],[{stop:true},'generating'],[{dialog:true},'approval'],[{status:''},'unknown'],[{},'completed'],[{status:'',history:true},'idle']]) {const x=setup(opts);assert.equal((await x.call({kind:'inspect'})).state,state);}
 for(const opts of [{draft:'preserve'}, {stop:true},{status:''},{dialog:true}]) {const x=setup(opts);assert.equal((await x.call({kind:'send',url:'https://chatgpt.com/c/test',text:'Continue'})).state,'uncertain');assert.deepEqual(x.counts(),{clicked:0,inserted:0});}
 const x=setup();assert.equal((await x.call({kind:'send',url:'https://chatgpt.com/c/other',text:'Continue'})).ok,false);assert.equal(x.counts().clicked,0);
 const y=setup();y.stop();assert.equal((await y.call({kind:'inspect'})).state,'interrupted');
 const ready=setup({status:'',history:true});assert.equal((await ready.call({kind:'send',url:'https://chatgpt.com/c/test',text:'Continue'})).state,'observed_sent');
 const z=setup();assert.equal((await z.call({kind:'send',url:'https://chatgpt.com/c/test',text:'Continue'})).state,'observed_sent');assert.equal(z.counts().clicked,1);
 const longText='First paragraph\n'+('Test the continuation loop. '.repeat(50))+'\n\nFinal paragraph';
 for(const loop of [false,true]) {
   const m=setup({multiline:true});
   assert.equal((await m.call({kind:'send',loop,document_id:'document-test-12345678',user_count:0,url:'https://chatgpt.com/c/test',id:'multiline',text:longText})).state,'observed_sent');
   assert.equal(m.counts().clicked,1);
 }
 for(const opts of [{missingBody:true},{wrongBody:true}]) {
   const m=setup(opts);assert.equal((await m.call({kind:'send',url:'https://chatgpt.com/c/test',text:longText})).state,'uncertain');assert.equal(m.counts().clicked,1);
 }
 const v=setup({virtualized:true,multiline:true});
 assert.equal((await v.call({kind:'inspect'})).user_count,1);
 assert.equal((await v.call({kind:'send',loop:true,document_id:'document-test-12345678',user_count:1,url:'https://chatgpt.com/c/test',id:'virtualized',text:longText})).state,'observed_sent');
 assert.equal((await v.call({kind:'inspect'})).user_count,2);
 assert.equal(v.counts().clicked,1);
 // Real ChatGPT 0.7.0 failure: the rich composer serialized a bare URL
 // inside checkpoint JSON as a redundant Markdown link after Send.
 const urlText='CURRENT TASK CONTEXT\n'+JSON.stringify({completed:['Opened http://127.0.0.1:8765/ and verified the table.']})+'\nUSER UPDATES\nMake the table yellow.';
 const linked=setup({multiline:true,autolink:true,virtualized:true});
 assert.equal((await linked.call({kind:'send',loop:true,document_id:'document-test-12345678',user_count:1,url:'https://chatgpt.com/c/test',id:'autolink',text:urlText})).state,'observed_sent');
 assert.equal(linked.counts().clicked,1);assert.equal(linked.polls(),1);
 // This normalization must not turn changed URL destinations or labels into a
 // match. Existing user count, identity and pre-click draft checks still apply.
 for(const text of [urlText.replace('http://127.0.0.1:8765/','[different label](http://127.0.0.1:8765/)'),urlText.replace('http://127.0.0.1:8765/','[http://127.0.0.1:8765/](http://127.0.0.1:8787/)')]) {
   const changedURL=setup({autolink:true,observedBody:urlText});
   assert.equal((await changedURL.call({kind:'send',url:'https://chatgpt.com/c/test',text})).state,'uncertain');
 }
 // A rich Markdown link consumed the JSON array delimiter in the actual
 // resumed checkpoint. Reconstruct source, retaining the target for comparison.
 const textNode=text=>({nodeType:3,textContent:text});
 const element=(tag,children,href)=>({nodeType:1,tagName:tag,childNodes:children,getAttribute:()=>href});
 const richExpected='Pending: [{"description":"Open http://localhost:4176", "state":"ready"}]';
 const richTree=target=>({...element('DIV',[textNode('Pending: '),element('A',[textNode('{"description":"Open [http://localhost:4176')],target),textNode('", "state":"ready"}]')]),querySelector:()=>true});
 for(const [target,expected] of [['http://localhost:4176','observed_sent'],['http://localhost:9999','uncertain']]) {
   const rich=setup({richBody:richTree(target),observedBody:'Pending: {"description":"Open [http://localhost:4176", "state":"ready"}]'});
   assert.equal((await rich.call({kind:'send',url:'https://chatgpt.com/c/test',text:richExpected})).state,expected);
   assert.equal(rich.counts().clicked,1);
 }
 const throttled=setup({missingBody:true,slowClock:3000});
 assert.equal((await throttled.call({kind:'send',url:'https://chatgpt.com/c/test',text:'Unconfirmed'})).state,'uncertain');
 assert.equal(throttled.polls(),4);assert.equal(throttled.counts().clicked,1);
 const changed=setup({multiline:true,corrupt:true});
 assert.equal((await changed.call({kind:'send',url:'https://chatgpt.com/c/test',text:longText})).state,'uncertain');
 assert.equal(changed.counts().clicked,0);
 const denied=setup({multiline:true,permit:false});
 assert.equal((await denied.call({kind:'send',loop:true,document_id:'document-test-12345678',user_count:0,url:'https://chatgpt.com/c/test',id:'denied',text:longText})).state,'uncertain');
 assert.equal(denied.counts().clicked,0);
 const nonce='a'.repeat(32);
 const marker=await setup({history:true,answer:`Result [BRIDGE_LOOP:${nonce}:PENDING]`}).call({kind:'inspect'});
 assert.equal(marker.marker.decision,'PENDING');assert.equal(marker.terminal,true);
 for(const answer of [`[BRIDGE_LOOP:${nonce}:PENDING] trailing text`,`[BRIDGE_LOOP:${nonce}:DONE] [BRIDGE_LOOP:${nonce}:PENDING]`])assert.equal((await setup({history:true,answer}).call({kind:'inspect'})).marker,null);
 const halted=setup({permit:false});assert.equal((await halted.call({kind:'send',loop:true,document_id:'document-test-12345678',user_count:0,url:'https://chatgpt.com/c/test',id:'req',text:'Goal'})).state,'uncertain');assert.equal(halted.counts().clicked,0);
 const reloaded=setup();await reloaded.call({kind:'send',loop:true,document_id:'old-document',user_count:0,url:'https://chatgpt.com/c/test',text:'Goal'});assert.equal(reloaded.counts().inserted,0);
 console.log('PASS: DOM states, interruption, draft preservation, wrong conversation, one click and observed message');
})().catch(e=>{console.error(e);process.exitCode=1;});
