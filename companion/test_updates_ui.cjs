const assert=require('node:assert/strict');
const fixture=require('./test_dashboard.cjs');
(async()=>{
 let f=await fixture(true);f.setLoop('waiting');f.setPage('generating');await f.tick();
 assert.equal(f.$('queue-update').disabled,false);
 f.$('update-text').value='Keep the Solo mode intact';await f.$('update-text').events.input();
 await Promise.all([f.$('queue-update').onclick(),f.$('queue-update').onclick()]);
 assert.equal(f.updates().length,1);assert.equal(f.updates()[0].state,'queued');
 assert.equal(f.$('update-text').value,'');assert.match(f.$('notice').textContent,/No message/);
 assert.equal(f.counts().sends,0);
 // Inline Edit uses the common editor; polling must preserve dirty text.
 await f.$('task-updates').children[0].children[3].children[0].onclick();
 f.$('update-text').value='Keep Solo and use a light theme';await f.$('update-text').events.input();await f.tick();
 assert.equal(f.$('update-text').value,'Keep Solo and use a light theme');
 await f.$('queue-update').onclick();assert.equal(f.updates()[0].revision,2);
 // Reservation racing an edit disables Save. Cancel edit preserves a new correction draft.
 await f.$('task-updates').children[0].children[3].children[0].onclick();
 f.changeUpdate(f.updates()[0].id,'uncertain');await f.tick();
 assert.equal(f.$('queue-update').disabled,true);
 await f.$('cancel-update-edit').onclick();assert.equal(f.$('queue-update').disabled,false);
 assert.equal(f.$('update-text').value,'Keep Solo and use a light theme');
 f.setLoop('stopped');await f.tick();
 await f.$('task-updates').children[0].children[3].children[0].onclick();
 assert.equal(f.updates()[0].state,'sent');assert.match(f.$('task-updates').children[0].children[0].textContent,/confirmed by you/);
 // Lost native reply keeps the same add ID. A retry does not duplicate the queue entry.
 f=await fixture(true);f.$('update-text').value='One correction';f.loseUpdate();await f.$('queue-update').onclick();
 assert.equal(f.updates().length,1);assert.equal(f.$('update-text').value,'One correction');
 await f.$('queue-update').onclick();assert.equal(f.updates().length,1);
 // Task-specific draft survives conversation switching.
 f.$('update-text').value='Unsent draft';await f.$('update-text').events.input();
 await f.$('conversations').children[1].onclick();assert.equal(f.$('queue-update').disabled,true);
 await f.$('conversations').children[0].onclick();assert.equal(f.$('update-text').value,'Unsent draft');
 await f.$('task-updates').children[0].children[3].children[1].onclick();
 assert.equal(f.updates()[0].state,'cancelled');
 // Delivery mode is explicit, editable while queued, and retained in drafts.
 f=await fixture(true);f.$('update-mode').value='next_reply';await f.$('update-mode').events.change();
 f.$('update-text').value='Use chat delivery only';await f.$('update-text').events.input();await f.$('queue-update').onclick();
 assert.equal(f.updates()[0].mode,'next_reply');
 await f.$('task-updates').children[0].children[3].children[0].onclick();assert.equal(f.$('update-mode').value,'next_reply');
 f.$('update-mode').value='automatic';await f.$('update-mode').events.change();await f.$('queue-update').onclick();assert.equal(f.updates()[0].mode,'automatic');
 f.updates()[0].state='uncertain';f.updates()[0].delivery_channel='bridge';f.setLoop('waiting');await f.tick();
 assert.match(f.$('task-updates').children[0].children[0].textContent,/awaiting checkpoint acknowledgement/);
 assert.equal(f.$('task-updates').children[0].children[3].children[0].disabled,true);
 f.updates()[0].state='sent';f.updates()[0].confirmed_by='bridge_checkpoint';f.updates()[0].ack_revision=3;await f.tick();
 assert.match(f.$('task-updates').children[0].children[0].textContent,/Acknowledged via Bridge.*3/);
 console.log('PASS update UI: queue during generation, no direct send, double click, edit/cancel, dirty draft, reservation race, uncertain resolution, lost reply idempotency and scoped draft');
})().catch(e=>{console.error(e);process.exitCode=1;});
