const fixture=require('./test_dashboard.cjs'),assert=require('node:assert/strict');
(async()=>{
 let f=await fixture(true);f.uncertain();f.setLoopFields({review_started:1,review_revision:0,final_review:1});f.setReviewReport();await f.tick();
 assert.match(f.$('delivery-work-state').textContent,/Final review reported complete; message delivery confirmation pending/);
 assert.equal(f.$('verify-delivery').hidden,false);assert.equal(f.$('verify-delivery').disabled,false);
 await f.$('verify-delivery').onclick();assert.deepEqual(f.counts(),{claims:0,sends:0,starts:0});assert.equal(f.$('verify-delivery').hidden,true);
 assert.match(f.$('notice').textContent,/Nothing was sent/);
 f.setLoopFields({state:'stopped',reason:'Delivery observation expired.'});await f.tick();assert.equal(f.$('verify-delivery').hidden,false);assert.match(f.$('delivery-work-state').textContent,/completion reconciliation/);
 f=await fixture(true);f.uncertain();f.setLoop('waiting');await f.tick();assert.equal(f.$('verify-delivery').disabled,true);
 f.setLoop('stopped');f.reopen();await f.$('refresh').onclick();assert.equal(f.$('verify-delivery').disabled,true); // Ownership needs reconnect.
 console.log('PASS delivery UI: distinguish work/delivery, explicit no-resend action, active-loop and tab-ownership guards');
})().catch(e=>{console.error(e);process.exitCode=1;});
