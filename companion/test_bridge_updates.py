import sqlite3
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import test_work_context as helpers
from companion import bridge_updates
from coordinator import Coordinator


class BridgeUpdateTests(unittest.TestCase):
    setUp=helpers.WorkTests.setUp
    call=helpers.WorkTests.call
    start=helpers.WorkTests.start
    finish=helpers.WorkTests.finish
    checkpoint=helpers.WorkTests.checkpoint
    receipt=helpers.WorkTests.receipt

    def running(self):
        run=self.start();self.call('loop_result',id=run['send']['id'],state='observed_sent');return run

    def add(self,run,text='Keep the original game intact',mode='automatic'):
        uid=uuid.uuid4().hex
        self.call('update_add',task_id=run['loop']['work_task_id'],id=uid,text=text,mode=mode)
        return uid

    def offer(self,run,**kwargs):
        return bridge_updates.attach({'ok':True,'result':'Original operation result'},str(self.root),run['loop']['work_task_id'],self.work.path,root=self.root/'companion',operation='test_contact',**kwargs)

    def rows(self):return self.call('status')['work']['updates']

    def ack(self,run,offered):
        markers=[x['acknowledgement']+' Instruction handled and validated.' for x in offered['workspace_updates']['updates']]
        saved=self.checkpoint(run,completed=markers)
        return self.offer(run,checkpoint_revision=saved['revision'])

    def test_offer_ack_completes_same_response_without_chat_message(self):
        run=self.running();uid=self.add(run);offered=self.offer(run)
        self.assertEqual(offered['result'],'Original operation result')
        self.assertEqual(offered['workspace_updates']['status'],'offered_via_bridge')
        self.assertEqual(self.rows()[0]['state'],'uncertain')
        self.assertEqual(self.rows()[0]['delivery_channel'],'bridge')
        self.ack(run,offered)
        row=self.rows()[0];self.assertEqual(row['state'],'sent');self.assertEqual(row['ack_revision'],1)
        self.assertEqual(row['confirmed_by'],'bridge_checkpoint')
        ended=self.finish(run);self.assertEqual(ended['loop']['state'],'completed');self.assertEqual(ended['loop']['used'],0)
        self.assertEqual(len(self.call('status')['requests']),1)
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM update_deliveries WHERE id=?',(uid,)).fetchone()[0],1)

    def retrieve(self,run):
        return bridge_updates.attach({'ok':True},str(self.root),run['loop']['work_task_id'],self.work.path,root=self.root/'companion',operation='task_context')

    def test_lost_first_output_can_be_read_without_new_delivery(self):
        run=self.running();uid=self.add(run,'Use AMARELO');first=self.offer(run)
        recovered=self.retrieve(run);again=self.retrieve(run)
        self.assertTrue(recovered['workspace_updates']['retrieved_existing_delivery'])
        self.assertEqual(recovered['workspace_updates']['updates'],first['workspace_updates']['updates'])
        self.assertEqual(again,recovered)
        self.assertEqual(self.rows()[0]['state'],'uncertain')
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM update_deliveries WHERE id=?',(uid,)).fetchone()[0],1)
        self.ack(run,recovered)
        self.assertEqual(self.finish(run)['loop']['state'],'completed')
        self.assertEqual(len(self.call('status')['requests']),1)

    def test_retrieval_after_stop_preserves_stop_and_queued_updates(self):
        run=self.running();self.add(run);first=self.offer(run);self.add(run,'A later instruction')
        self.call('loop_stop');recovered=self.retrieve(run)
        self.assertEqual(recovered['workspace_updates']['loop_state'],'stopped')
        self.assertEqual(recovered['workspace_updates']['updates'],first['workspace_updates']['updates'])
        self.assertEqual([r['state'] for r in self.rows()],['uncertain','queued'])
        self.assertEqual(self.call('status')['loop']['state'],'stopped')

    def test_chat_delivery_is_not_retrieved_as_bridge_instruction(self):
        run=self.running();self.add(run);self.checkpoint(run);self.finish(run)
        self.assertNotIn('updates',self.retrieve(run)['workspace_updates'])

    def test_next_response_mode_uses_existing_chat_path(self):
        run=self.running();self.add(run,mode='next_reply');self.assertNotIn('workspace_updates',self.offer(run))
        self.checkpoint(run);following=self.finish(run)
        self.assertIn('Keep the original game intact',following['send']['text'])
        self.assertEqual(self.rows()[0]['delivery_channel'],'chat')
        self.assertEqual(following['loop']['used'],1)

    def test_unacknowledged_bridge_offer_blocks_fallback_and_reoffer(self):
        run=self.running();self.add(run);offered=self.offer(run);again=self.offer(run)
        self.assertEqual(again['workspace_updates']['status'],'awaiting_acknowledgement')
        self.assertNotIn('updates',again['workspace_updates'])
        self.checkpoint(run);ended=self.finish(run)
        self.assertEqual(ended['loop']['state'],'review_required');self.assertNotIn('send',ended)
        self.assertEqual(self.rows()[0]['request_id'],offered['workspace_updates']['delivery_id'])

    def test_chat_reservation_wins_before_bridge(self):
        run=self.running();self.add(run);self.checkpoint(run);following=self.finish(run)
        self.assertIn('send',following);offered=self.offer(run)
        self.assertNotIn('updates',offered['workspace_updates']);self.assertEqual(self.rows()[0]['delivery_channel'],'chat')

    def test_bridge_reservation_survives_reopen_lost_reply(self):
        run=self.running();uid=self.add(run);self.offer(run)
        c=Coordinator(self.root/'companion',work=self.work)
        try:self.assertEqual(c.dispatch({**self.base,'action':'status'})['work']['updates'][0]['state'],'uncertain')
        finally:c.db.close()
        self.assertNotIn('updates',self.offer(run)['workspace_updates'])
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM update_deliveries WHERE id=?',(uid,)).fetchone()[0],1)

    def test_stop_blocks_updates_but_elapsed_time_does_not(self):
        for stop in [True,False]:
            run=self.running();self.add(run)
            if stop:self.call('loop_stop')
            else:
                self.c.db.execute('UPDATE goal_loops SET started=started-1900');self.c.db.commit()
            if stop:
                self.assertNotIn('workspace_updates',self.offer(run));self.assertEqual(self.rows()[0]['state'],'queued')
            else:
                self.assertIn('workspace_updates',self.offer(run));self.assertEqual(self.rows()[0]['state'],'uncertain')
            # A separate reviewed task remains isolated from the old queue.

    def test_wrong_project_task_or_binding_does_not_receive_text(self):
        run=self.running();self.add(run)
        out=bridge_updates.attach({'ok':True},str(self.root/'other'),run['loop']['work_task_id'],self.work.path,root=self.root/'companion')
        self.assertNotIn('workspace_updates',out)
        altered={**run,'loop':{**run['loop'],'work_task_id':'different-task'}}
        self.assertNotIn('workspace_updates',self.offer(altered))
        self.call('unbind');self.assertNotIn('workspace_updates',self.offer(run))

    def test_only_matching_committed_checkpoint_marker_acknowledges(self):
        run=self.running();self.add(run);offer=self.offer(run)
        marker=offer['workspace_updates']['updates'][0]['acknowledgement']
        for bad in [marker.replace(':1:',':2:'),marker.replace(offer['workspace_updates']['delivery_id'],'bridge-update-'+uuid.uuid4().hex),'Handled without marker']:
            cp=self.checkpoint(run,completed=[bad]);self.offer(run,checkpoint_revision=cp['revision'])
            self.assertEqual(self.rows()[0]['state'],'uncertain')
        self.ack(run,offer);self.assertEqual(self.rows()[0]['state'],'sent')

    def test_new_update_waits_for_ack_and_then_uses_fresh_floor(self):
        run=self.running();first=self.add(run);offer=self.offer(run);second=self.add(run,'Use a yellow background')
        self.assertNotIn('updates',self.offer(run)['workspace_updates'])
        next_offer=self.ack(run,offer)
        self.assertEqual(next_offer['workspace_updates']['updates'][0]['id'],second)
        self.assertEqual({x['id']:x['state'] for x in self.rows()},{first:'sent',second:'uncertain'})
        self.assertEqual(self.rows()[1]['revision_floor'],1)
        self.ack(run,next_offer);self.assertEqual(self.finish(run)['loop']['state'],'completed')

    def test_edit_mode_version_and_idempotency(self):
        run=self.running();uid=self.add(run)
        self.call('update_add',task_id=run['loop']['work_task_id'],id=uid,text='Keep the original game intact',mode='automatic')
        with self.assertRaisesRegex(ValueError,'different content'):
            self.call('update_add',task_id=run['loop']['work_task_id'],id=uid,text='Keep the original game intact',mode='next_reply')
        self.call('update_edit',task_id=run['loop']['work_task_id'],id=uid,revision=1,text='Wait until next response',mode='next_reply')
        self.assertEqual(self.rows()[0]['mode'],'next_reply')
        self.assertEqual([x[0] for x in self.c.db.execute('SELECT mode FROM update_versions WHERE id=? ORDER BY revision',(uid,))],['automatic','next_reply'])
        self.assertNotIn('workspace_updates',self.offer(run))

    def test_concurrent_bridge_and_chat_claim_exactly_one_delivery(self):
        run=self.running();uid=self.add(run);self.checkpoint(run)
        def chat():
            c=Coordinator(self.root/'companion',work=self.work)
            try:return c.dispatch({**self.base,'action':'loop_tick','snapshot':{**self.snap,'user_count':1,'marker':{'nonce':run['loop']['nonce'],'decision':'DONE'}}})
            finally:c.db.close()
        with ThreadPoolExecutor(2) as pool:
            a=pool.submit(chat);b=pool.submit(self.offer,run);chat_result=a.result();bridge_result=b.result()
        delivered=int('send' in chat_result)+int(bridge_result.get('workspace_updates',{}).get('status')=='offered_via_bridge')
        self.assertEqual(delivered,1)
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM update_deliveries WHERE id=?',(uid,)).fetchone()[0],1)

    def test_operation_inference_requires_unique_retained_task_scope(self):
        run=self.running();self.receipt(run)
        linked=bridge_updates.operation_task(self.work.path,self.work.operations_path,'operation-1',str(self.root))
        self.assertEqual(linked,(str(self.root),run['loop']['work_task_id']))
        self.assertIsNone(bridge_updates.operation_task(self.work.path,self.work.operations_path,'operation-1',str(self.root/'wrong')))
        self.op.attach('operation-1','session-one')
        self.assertEqual(bridge_updates.operation_task(self.work.path,self.work.operations_path,session_id='session-one'),linked)

    def test_late_ack_records_fact_without_resuming_stop(self):
        run=self.running();self.add(run);offer=self.offer(run);self.call('loop_stop')
        self.ack(run,offer);status=self.call('status')
        self.assertEqual(status['work']['updates'][0]['state'],'sent')
        self.assertEqual(status['loop']['state'],'stopped');self.assertEqual(len(status['requests']),1)

    def test_two_bridge_contacts_reserve_only_once(self):
        run=self.running();uid=self.add(run)
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda _:self.offer(run),range(2)))
        self.assertEqual(sum(x['workspace_updates']['status']=='offered_via_bridge' for x in results),1)
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM update_deliveries WHERE id=?',(uid,)).fetchone()[0],1)

    def test_legacy_schema_preserves_next_response_mode(self):
        from companion import task_updates
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
        db.executescript('''CREATE TABLE task_updates(id TEXT PRIMARY KEY,task_id TEXT,text TEXT,revision INTEGER,state TEXT,request_id TEXT,revision_floor INTEGER,confirmed_by TEXT,created REAL,updated REAL);
        CREATE TABLE update_versions(id TEXT,revision INTEGER,text TEXT,created REAL,PRIMARY KEY(id,revision));
        CREATE TABLE update_deliveries(id TEXT,revision INTEGER,request_id TEXT,state TEXT,text TEXT,created REAL,PRIMARY KEY(id,revision,request_id));
        INSERT INTO task_updates VALUES('legacy-id','task','Keep original',1,'queued','',-1,'',1,1);
        INSERT INTO update_versions VALUES('legacy-id',1,'Keep original',1);''')
        task_updates.setup(db)
        row=dict(db.execute('SELECT * FROM task_updates').fetchone())
        self.assertEqual(row['text'],'Keep original');self.assertEqual(row['mode'],'next_reply');self.assertEqual(row['delivery_channel'],'chat');self.assertEqual(row['ack_revision'],-1)
        self.assertEqual(db.execute('SELECT count(*) FROM update_versions').fetchone()[0],1);db.close()

    def test_unavailable_metadata_preserves_original_operation_result(self):
        run=self.running();self.add(run)
        path=self.root/'bad';path.mkdir();(path/'state.sqlite3').write_text('not sqlite')
        result=bridge_updates.attach({'returncode':0},str(self.root),run['loop']['work_task_id'],self.work.path,root=path)
        self.assertEqual(result['returncode'],0);self.assertEqual(result['workspace_updates']['status'],'unavailable')


if __name__=='__main__':unittest.main()
