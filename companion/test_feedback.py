import unittest
import uuid
from unittest.mock import patch

import test_work_context as helpers


class FeedbackTests(unittest.TestCase):
    setUp=helpers.WorkTests.setUp
    call=helpers.WorkTests.call
    start=helpers.WorkTests.start
    finish=helpers.WorkTests.finish
    checkpoint=helpers.WorkTests.checkpoint
    receipt=helpers.WorkTests.receipt

    def test_pending_uses_latest_checkpoint_not_initial_goal_only(self):
        r=self.start()
        self.checkpoint(r,next_step='Verify reconnect on mobile')
        self.receipt(r,rid='actual-check')
        following=self.finish(r,'PENDING')
        self.assertIn('CURRENT TASK CONTEXT',following['send']['text'])
        self.assertIn('Verify reconnect on mobile',following['send']['text'])
        self.assertIn('actual-check',following['send']['text'])
        self.assertIn('"revision":1',following['send']['text'])
        self.assertEqual(following['loop']['feedback_count'],0)

    def test_done_feedback_then_corrected_checkpoint_completes(self):
        r=self.start();self.checkpoint(r,next_step='Check actual result')
        feedback=self.finish(r)
        self.assertIn('Your DONE response was not accepted',feedback['send']['text'])
        self.assertEqual(feedback['loop']['used'],1)
        self.receipt(r,rid='verified-result')
        self.checkpoint(r,validation=[{'description':d,'status':'passed','evidence':'[receipt:verified-result] Result inspected'} for d in ['Rules pass','Layout works']])
        finished=self.finish(feedback)
        self.assertEqual(finished['loop']['state'],'completed')
        self.assertEqual(len(self.call('status')['requests']),2)
        checks=self.call('status')['work']['evidence_checks']
        self.assertEqual({x['state'] for x in checks},{'receipt_confirmed'})

    def test_only_one_done_feedback_even_with_budget_remaining(self):
        r=self.call('loop_start',goal='Test',criteria='Rules pass',limit=4,work_request_id=uuid.uuid4().hex,snapshot=self.snap)
        feedback=self.finish(r);ended=self.finish(feedback)
        self.assertEqual(ended['loop']['state'],'review_required')
        self.assertEqual(ended['loop']['used'],1)
        self.assertNotIn('send',ended)

    def test_no_feedback_when_continuation_budget_exhausted(self):
        r=self.start();self.checkpoint(r,next_step='Tests remain')
        following=self.finish(r,'PENDING')
        ended=self.finish(following)
        self.assertEqual(ended['loop']['state'],'review_required')
        self.assertEqual(ended['loop']['feedback_count'],0)
        self.assertNotIn('send',ended)

    def test_unknown_operation_blocks_pending_without_replay(self):
        r=self.start();self.receipt(r)
        with self.op.connection() as db:db.execute("UPDATE requests SET state='unknown'")
        ended=self.finish(r,'PENDING')
        self.assertEqual(ended['loop']['state'],'review_required')
        self.assertNotIn('send',ended)
        self.assertEqual(len(self.call('status')['requests']),1)

    def test_needs_user_bypasses_feedback(self):
        r=self.start();ended=self.finish(r,'NEEDS_USER')
        self.assertEqual(ended['loop']['state'],'stopped')
        self.assertEqual(ended['loop']['feedback_count'],0)
        self.assertNotIn('send',ended)

    def test_invalid_nonce_and_reload_never_trigger_feedback(self):
        for change in [{'document_id':'another-document-12345'},{'marker':{'nonce':'old','decision':'DONE'}}]:
            r=self.start();self.call('loop_result',id=r['send']['id'],state='observed_sent')
            s={**self.snap,'user_count':r['loop']['expected_users'],'marker':{'nonce':r['loop']['nonce'],'decision':'DONE'},**change}
            ended=self.call('loop_tick',snapshot=s)
            self.assertNotIn('send',ended)
            self.assertEqual(ended['loop']['feedback_count'],0)
            self.call('loop_stop')

    def test_reference_must_belong_to_task_and_be_successful(self):
        self.op.reserve('foreign',str(self.root),'run_command',['fixture']);self.op.finish('foreign',0)
        for ref in ['foreign','missing','failed']:
            r=self.start();self.receipt(r,rid='failed',code=1)
            self.checkpoint(r,validation=[{'description':d,'status':'passed','evidence':f'[receipt:{ref}]'} for d in ['Rules pass','Layout works']])
            self.assertEqual(self.call('status')['work']['evidence_checks'][0]['state'],'invalid')
            feedback=self.finish(r)
            self.assertIn('Receipt evidence needs correction',feedback['send']['text'])
            self.call('loop_stop')

    def test_plain_evidence_stays_reported(self):
        r=self.start();self.receipt(r);self.checkpoint(r)
        self.assertEqual({x['state'] for x in self.call('status')['work']['evidence_checks']},{'reported'})

    def test_pruned_receipt_cannot_be_claimed_as_confirmed(self):
        r=self.start()
        for i in range(101):self.receipt(r,rid='op-'+str(i))
        self.checkpoint(r,validation=[{'description':d,'status':'passed','evidence':'[receipt:op-0]'} for d in ['Rules pass','Layout works']])
        self.assertTrue(self.call('status')['work']['receipt_history_limited'])
        self.assertEqual(self.call('status')['work']['evidence_checks'][0]['state'],'invalid')
        self.assertIn('Receipt evidence needs correction',self.finish(r)['send']['text'])

    def test_context_failure_and_time_limit_do_not_send(self):
        r=self.start();self.call('loop_result',id=r['send']['id'],state='observed_sent')
        snapshot={**self.snap,'user_count':r['loop']['expected_users'],'marker':{'nonce':r['loop']['nonce'],'decision':'PENDING'}}
        with patch.object(self.work,'read',side_effect=OSError('unavailable')):
            ended=self.call('loop_tick',snapshot=snapshot)
        self.assertEqual(ended['loop']['state'],'review_required')
        self.assertNotIn('send',ended)
        r=self.start();self.call('loop_result',id=r['send']['id'],state='observed_sent')
        self.c.db.execute('UPDATE goal_loops SET started=0');self.c.db.commit()
        ended=self.call('loop_tick',snapshot=snapshot)
        self.assertEqual(ended['loop']['state'],'stopped')
        self.assertNotIn('send',ended)

    def test_feedback_delivery_uncertain_never_replayed(self):
        r=self.start();feedback=self.finish(r)
        self.call('loop_result',id=feedback['send']['id'],state='uncertain')
        ended=self.call('loop_tick',snapshot={**self.snap,'user_count':r['loop']['expected_users'],'marker':{'nonce':r['loop']['nonce'],'decision':'DONE'}})
        self.assertEqual(ended['loop']['state'],'stopped')
        self.assertNotIn('send',ended)
        self.assertEqual(len(self.call('status')['requests']),2)

    def test_paused_checkpoint_blocks_without_feedback(self):
        r=self.start();self.checkpoint(r)
        self.work.store.checkpoint(str(self.root),task_id=r['loop']['work_task_id'],expected_revision=1,
            checkpoint={'summary':'User review required','next_step':'Wait for user review'},state='paused',source='User',request_id=uuid.uuid4().hex)
        ended=self.finish(r)
        self.assertEqual(ended['loop']['state'],'review_required')
        self.assertIn('paused',ended['loop']['reason'])
        self.assertNotIn('send',ended)

    def test_concurrent_done_reserves_exactly_one_feedback(self):
        from concurrent.futures import ThreadPoolExecutor
        from coordinator import Coordinator
        r=self.start();self.call('loop_result',id=r['send']['id'],state='observed_sent')
        s={**self.snap,'user_count':r['loop']['expected_users'],'marker':{'nonce':r['loop']['nonce'],'decision':'DONE'}}
        def tick(_):
            c=Coordinator(self.root/'companion',work=self.work)
            try:return c.dispatch({**self.base,'action':'loop_tick','snapshot':s})
            finally:c.db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(tick,range(2)))
        self.assertEqual(sum('send' in x for x in results),1)
        self.assertEqual(self.call('status')['loop']['feedback_count'],1)

    def test_oversized_context_stops_without_hiding_pending_work(self):
        from work_context import continuation_review
        r=self.start();self.checkpoint(r)
        work=self.call('status')['work']
        work['context']['checkpoint']['progress']['pending']=[{'description':'x'*23000}]
        result=continuation_review(work,'PENDING')
        self.assertTrue(result['blocked'])
        self.assertNotIn('context',result)
