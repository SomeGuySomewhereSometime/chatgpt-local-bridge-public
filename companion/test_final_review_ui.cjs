const fixture=require('./test_dashboard.cjs'),assert=require('node:assert/strict');
(async()=>{
 let f=await fixture();
 assert.equal(f.$('final-review').checked,true);
 f.$('goal').value='Review the delivered result';f.$('limit').value='2';
 await f.$('review-goal').onclick();
 assert.match(f.$('goal-budget').textContent,/Final review is ON and uses this allowance/);
 assert.equal(f.$('final-review').disabled,true);
 await f.$('start').onclick();assert.equal(f.lastStart().final_review,true);
 f=await fixture();f.$('final-review').checked=false;await f.$('final-review').events.input();
 await f.$('conversations').children[1].onclick();await f.$('conversations').children[0].onclick();
 assert.equal(f.$('final-review').checked,false);
 f.$('goal').value='Simple task';f.$('limit').value='1';await f.$('review-goal').onclick();
 assert.match(f.$('goal-budget').textContent,/final review is OFF/);await f.$('start').onclick();assert.equal(f.lastStart().final_review,false);
 f=await fixture(true);f.$('final-review').checked=true;f.$('limit').value='1';
 await f.$('resume-goal').onclick();assert.match(f.$('goal-budget').textContent,/ON for this new run/);
 await f.$('start').onclick();assert.equal(f.lastStart().final_review,true);
 console.log('PASS final review UI: reviewed opt-in, default, persistence, disabled toggle during preview, explicit resume');
})().catch(e=>{console.error(e);process.exitCode=1;});
