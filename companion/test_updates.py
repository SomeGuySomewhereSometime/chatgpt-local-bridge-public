import sqlite3
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
import test_work_context as helpers
from coordinator import Coordinator


class UpdateTests(unittest.TestCase):
    setUp=helpers.WorkTests.setUp
    call=helpers.WorkTests.call
    start=helpers.WorkTests.start
    finish=helpers.WorkTests.finish
    checkpoint=helpers.WorkTests.checkpoint
    receipt=helpers.WorkTests.receipt

    def add(self,r,text='Keep the Solo mode intact',uid=None):
        uid=uid or uuid.uuid4().hex
        return uid,self.call('update_add',task_id=r['loop']['work_task_id'],id=uid,text=text)

    def rows(self):return self.call('status')['work']['updates']

    def test_add_is_durable_idempotent_and_does_not_send(self):
        r=self.start();uid,result=self.add(r)
        self.add(r,uid=uid)
        self.assertEqual(len(self.rows()),1)
        self.assertEqual(self.rows()[0]['state'],'queued')
        self.assertEqual(len(self.call('status')['requests']),1)
        c=Coordinator(self.root/'companion',work=self.work)
        try:self.assertEqual(len(c.dispatch({**self.base,'action':'status'})['work']['updates']),1)
        finally:c.db.close()
        with self.assertRaisesRegex(ValueError,'different content'):self.add(r,'Different',uid)

    def test_edit_cancel_and_version_history(self):
        r=self.start();uid,_=self.add(r)
        self.call('update_edit',task_id=r['loop']['work_task_id'],id=uid,revision=1,text='Use a light theme')
        self.assertEqual(self.rows()[0]['revision'],2)
        with self.assertRaisesRegex(ValueError,'changed'):
            self.call('update_cancel',task_id=r['loop']['work_task_id'],id=uid,revision=1)
        self.call('update_cancel',task_id=r['loop']['work_task_id'],id=uid,revision=2)
        self.assertEqual(self.rows()[0]['state'],'cancelled')
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM update_versions WHERE id=?',(uid,)).fetchone()[0],2)
        self.checkpoint(r,next_step='Continue')
        self.assertNotIn('Use a light theme',self.finish(r,'PENDING')['send']['text'])

    def test_update_on_done_uses_budget_and_requires_new_checkpoint(self):
        r=self.start();self.checkpoint(r);self.add(r)
        following=self.finish(r)
        self.assertIn('Keep the Solo mode intact',following['send']['text'])
        self.assertEqual(following['loop']['used'],1)
        self.assertEqual(following['loop']['feedback_count'],0)
        self.assertEqual(self.rows()[0]['state'],'uncertain')
        self.call('loop_result',id=following['send']['id'],state='observed_sent')
        self.assertEqual(self.rows()[0]['state'],'sent')
        ended=self.finish(following)
        self.assertEqual(ended['loop']['state'],'review_required')
        self.assertIn('predates',ended['loop']['reason'])

    def test_fresh_checkpoint_after_update_can_complete(self):
        r=self.start();self.checkpoint(r);self.add(r)
        following=self.finish(r)
        self.call('loop_result',id=following['send']['id'],state='observed_sent')
        self.checkpoint(r,summary='Update applied and tested')
        self.assertEqual(self.finish(following)['loop']['state'],'completed')

    def test_new_update_during_dispatch_waits_for_next_message(self):
        r=self.call('loop_start',goal='Test',limit=3,criteria='Rules pass\nLayout works',work_request_id=uuid.uuid4().hex,snapshot=self.snap)
        first,_=self.add(r,'First change');self.checkpoint(r,next_step='Continue')
        following=self.finish(r,'PENDING')
        second,_=self.add(r,'Second change')
        self.assertNotIn('Second change',following['send']['text'])
        self.call('loop_result',id=following['send']['id'],state='observed_sent')
        rows={x['id']:x['state'] for x in self.rows()}
        self.assertEqual(rows,{first:'sent',second:'queued'})
        self.checkpoint(r,next_step='More work')
        later=self.finish(following,'PENDING')
        self.assertIn('First change',later['send']['text'])
        self.assertIn('Second change',later['send']['text'])

    def test_reserved_update_cannot_be_edited_or_cancelled(self):
        r=self.start();uid,_=self.add(r);self.checkpoint(r);following=self.finish(r)
        for action in ['update_edit','update_cancel']:
            with self.assertRaisesRegex(ValueError,'Only queued'):
                self.call(action,task_id=r['loop']['work_task_id'],id=uid,revision=1,text='Changed')
        self.assertEqual(self.c.db.execute('SELECT text FROM update_deliveries WHERE id=?',(uid,)).fetchone()[0],'Keep the Solo mode intact')

    def test_uncertain_delivery_stops_and_needs_explicit_resolution(self):
        r=self.start();uid,_=self.add(r);self.checkpoint(r);following=self.finish(r)
        self.call('loop_result',id=following['send']['id'],state='uncertain')
        self.assertEqual(self.rows()[0]['state'],'uncertain')
        with self.assertRaisesRegex(ValueError,'Resolve uncertain updates'):
            self.call('work_resume_preview',ack_id=following['send']['id'])
        self.call('update_reconcile',task_id=r['loop']['work_task_id'],id=uid,revision=1,resolution='found')
        self.assertEqual(self.rows()[0]['confirmed_by'],'user')
        self.assertEqual(self.c.db.execute('SELECT state FROM requests WHERE id=?',(following['send']['id'],)).fetchone()[0],'uncertain')
        reviewed=self.call('work_resume_preview',ack_id=following['send']['id'])
        resumed=self.call('loop_start',goal=r['loop']['goal'],limit=1,snapshot={**self.snap,'user_count':following['loop']['expected_users']},resume=reviewed['resume'])
        self.assertIn('previously sent instruction',resumed['send']['text'])
        self.assertEqual(self.rows()[0]['state'],'sent')

    def test_excluding_uncertain_update_preserves_delivery_history(self):
        r=self.start();uid,_=self.add(r);self.checkpoint(r);following=self.finish(r)
        self.call('loop_result',id=following['send']['id'],state='uncertain')
        self.call('update_reconcile',task_id=r['loop']['work_task_id'],id=uid,revision=1,resolution='exclude')
        self.assertEqual(self.rows()[0]['state'],'cancelled')
        self.assertEqual(self.c.db.execute('SELECT state FROM update_deliveries WHERE id=?',(uid,)).fetchone()[0],'uncertain')

    def test_reconcile_cannot_run_during_dispatch(self):
        r=self.start();uid,_=self.add(r);self.checkpoint(r);following=self.finish(r)
        with self.assertRaisesRegex(ValueError,'Stop the loop'):
            self.call('update_reconcile',task_id=r['loop']['work_task_id'],id=uid,revision=1,resolution='found')

    def test_done_with_updates_and_exhausted_budget_waits(self):
        r=self.start();self.checkpoint(r,next_step='Continue')
        following=self.finish(r,'PENDING');self.call('loop_result',id=following['send']['id'],state='observed_sent')
        self.checkpoint(r);self.add(r)
        ended=self.finish(following)
        self.assertEqual(ended['loop']['state'],'updates_pending')
        self.assertNotIn('send',ended)
        self.assertEqual(self.rows()[0]['state'],'queued')
        self.assertEqual(ended['loop']['used'],1)

    def test_queued_updates_survive_stop_and_resume(self):
        r=self.start();self.call('loop_result',id=r['send']['id'],state='observed_sent')
        self.checkpoint(r,next_step='Continue');self.add(r);self.call('loop_stop')
        reviewed=self.call('work_resume_preview')
        resumed=self.call('loop_start',goal=r['loop']['goal'],limit=1,snapshot={**self.snap,'user_count':1},resume=reviewed['resume'])
        self.assertIn('Keep the Solo mode intact',resumed['send']['text'])
        self.assertEqual(self.rows()[0]['request_id'],resumed['send']['id'])

    def test_updates_do_not_override_needs_user(self):
        r=self.start();self.add(r)
        ended=self.finish(r,'NEEDS_USER')
        self.assertEqual(ended['loop']['state'],'stopped')
        self.assertEqual(self.rows()[0]['state'],'queued')
        self.assertNotIn('send',ended)

    def test_adding_after_completion_does_not_restart(self):
        r=self.start();self.checkpoint(r);self.assertEqual(self.finish(r)['loop']['state'],'completed')
        self.add(r)
        self.assertEqual(self.call('status')['loop']['state'],'updates_pending')
        self.assertEqual(len(self.call('status')['requests']),1)

    def test_cancel_after_completion_restores_prior_completion(self):
        r=self.start();self.checkpoint(r);self.finish(r)
        uid,_=self.add(r)
        self.call('update_cancel',task_id=r['loop']['work_task_id'],id=uid,revision=1)
        self.assertEqual(self.call('status')['loop']['state'],'completed')

    def test_lost_add_reply_retried_after_delivery_does_not_reopen(self):
        r=self.start();self.checkpoint(r);uid,_=self.add(r)
        following=self.finish(r);self.call('loop_result',id=following['send']['id'],state='observed_sent')
        self.checkpoint(r);self.finish(following)
        self.add(r,uid=uid)
        self.assertEqual(self.call('status')['loop']['state'],'completed')
        self.assertEqual(self.rows()[0]['state'],'sent')

    def test_new_task_and_rebind_never_inherit_updates(self):
        r=self.start();self.add(r);self.call('loop_stop')
        new=self.start()
        self.assertNotIn('Keep the Solo mode intact',new['send']['text'])
        self.assertEqual(self.rows(),[])
        with self.assertRaisesRegex(ValueError,'linked task changed'):
            self.add(r,'Wrong task')
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM task_updates').fetchone()[0],1)

    def test_wrong_client_and_tab_cannot_queue(self):
        r=self.start()
        for extra in [{'client':'other'},{'tab':9}]:
            with self.assertRaisesRegex(ValueError,'does not match'):
                self.c.dispatch({**self.base,**extra,'action':'update_add','task_id':r['loop']['work_task_id'],'id':uuid.uuid4().hex,'text':'Wrong'})
        self.assertEqual(self.rows(),[])

    def test_concurrent_ticks_reserve_update_exactly_once(self):
        r=self.start();self.checkpoint(r);uid,_=self.add(r);self.call('loop_result',id=r['send']['id'],state='observed_sent')
        s={**self.snap,'user_count':1,'marker':{'nonce':r['loop']['nonce'],'decision':'DONE'}}
        def tick(_):
            c=Coordinator(self.root/'companion',work=self.work)
            try:return c.dispatch({**self.base,'action':'loop_tick','snapshot':s})
            finally:c.db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(tick,range(2)))
        self.assertEqual(sum('send' in x for x in results),1)
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM update_deliveries WHERE id=?',(uid,)).fetchone()[0],1)

    def test_size_limits_preserve_existing_updates(self):
        r=self.start()
        for i in range(4):self.add(r,str(i)*2000)
        with self.assertRaisesRegex(ValueError,'8,000'):self.add(r,'More')
        self.assertEqual(len(self.rows()),4)

    def test_restart_during_reserved_delivery_never_requeues(self):
        r=self.start();self.checkpoint(r);self.add(r);following=self.finish(r)
        c=Coordinator(self.root/'companion',work=self.work)
        try:
            state=c.dispatch({**self.base,'action':'status'})
            self.assertEqual(state['work']['updates'][0]['state'],'uncertain')
            ended=c.dispatch({**self.base,'action':'loop_tick','snapshot':{**self.snap,'user_count':1,'marker':{'nonce':r['loop']['nonce'],'decision':'DONE'}}})
            self.assertNotIn('send',ended)
        finally:c.db.close()

    def test_status_keeps_latest_message_and_older_receipt_metadata(self):
        r=self.start();self.checkpoint(r);self.add(r);following=self.finish(r)
        requests=self.call('status')['requests']
        self.assertEqual(requests[0]['text'],following['send']['text'])
        self.assertTrue(requests[1]['text_omitted'])
        self.assertNotIn('text',requests[1])
        self.assertEqual(self.c.db.execute('SELECT text FROM requests WHERE id=?',(r['send']['id'],)).fetchone()[0],r['send']['text'])
