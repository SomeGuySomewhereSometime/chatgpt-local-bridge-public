const setup=require('./test_content.cjs'),assert=require('node:assert/strict');
const url='https://chatgpt.com/c/test',text='CURRENT TASK CONTEXT\n'+('abc á\n'.repeat(5000))+'\n[BRIDGE_LOOP:'+('a'.repeat(32))+':DONE]';
(async()=>{
 let f=setup({collapsed:true});let r=await f.call({kind:'send',url,text});
 assert.equal(r.state,'observed_sent');assert.equal(f.expanded(),1);assert.equal(f.counts().clicked,1);assert.equal(r.observation.last_user_id,'sent-message-1');
 // A correct prefix is insufficient; expand still rejects a changed/missing suffix.
 for(const opts of [{expandTo:text+' changed'},{expandNoEffect:true},{sameId:true,virtualized:true}]) {
   f=setup({collapsed:true,...opts});r=await f.call({kind:'send',url,text});assert.equal(r.state,'uncertain');assert.equal(f.counts().clicked,1);assert.ok(f.expanded()<=1);
 }
 f=setup({collapsed:true,expandNoEffect:true});r=await f.call({kind:'send',url,text});assert.equal(r.observation.last_user_id,'sent-message-1');
 // Verification reads existing text, even with a draft or response active.
 for(const state of [{},{draft:'preserve draft'},{stop:true}]) {
  f=setup({collapsed:true,existingText:text,...state});r=await f.call({kind:'verify_delivery',url,text,message_id:'sent-message-0'});
  assert.equal(r.verified,true);assert.equal(r.snapshot.last_user_text.replace(/\s+/g,' '),text.replace(/\s+/g,' '));
  assert.equal(f.expanded(),1);assert.deepEqual(f.counts(),{clicked:0,inserted:0});
 }
 for(const msg of [{url:'https://chatgpt.com/c/wrong',text},{url,text,message_id:'wrong-id'},{url,text:text+' wrong'}]) {
  f=setup({collapsed:true,existingText:text});r=await f.call({kind:'verify_delivery',...msg});assert.equal(r.ok,false);assert.deepEqual(f.counts(),{clicked:0,inserted:0});
 }
 console.log('PASS delivery DOM: expand only identified bubble, exact full comparison, prefix rejection, no replay, draft/response preserved');
})().catch(e=>{console.error(e);process.exitCode=1;});
