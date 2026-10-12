const fixture=require('./test_dashboard.cjs'),assert=require('node:assert/strict');
(async()=>{
 const f=await fixture();
 assert.equal(f.$('debug-enabled').checked,false);
 assert.equal(f.$('export-diagnostics').disabled,false);
 f.$('debug-enabled').checked=true;await f.$('debug-enabled').onclick();
 assert.equal(f.$('debug-enabled').checked,true);
 assert.equal(f.counts().starts,0);assert.equal(f.counts().sends,0);
 f.$('goal').value='Debug UI fixture';f.$('limit').value='1';
 await f.$('review-goal').onclick();await f.$('start').onclick();
 assert.equal(f.lastStart().debug,true);assert.equal(f.counts().starts,1);
 await f.$('export-diagnostics').onclick();
 const exported=JSON.parse(await f.download().text());assert.equal(exported.run.run_id,'diagnostic-run');
 assert.equal(f.counts().starts,1);assert.equal(f.counts().sends,0);
 console.log('PASS debug UI: opt-in, same start, local export, no extra messages');
})().catch(e=>{console.error(e);process.exitCode=1;});
