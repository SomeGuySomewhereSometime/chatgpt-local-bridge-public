"""Exercise review decisions against real coordinator/checkpoint/receipt stores."""
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from coordinator import Coordinator
import test_work_context as helpers


class FinalReviewTests(unittest.TestCase):
    setUp=helpers.WorkTests.setUp
    call=helpers.WorkTests.call
    finish=helpers.WorkTests.finish
    checkpoint=helpers.WorkTests.checkpoint
    receipt=helpers.WorkTests.receipt

    def start(self,limit=3,enabled=True):
        return self.call('loop_start',goal='Improve the result',criteria='Rules pass\nLayout works',limit=limit,
                         final_review=enabled,work_request_id=uuid.uuid4().hex,snapshot=self.snap)

    def acknowledge(self,r,**extra):
        self.checkpoint(r,completed=['[BRIDGE_REVIEW:'+r['loop']['run_id']+'] Inspected the delivered path; corrected a concrete layout defect and rechecked it.'],**extra)

    def review(self,limit=3):
        r=self.start(limit);self.checkpoint(r)
        following=self.finish(r)
        self.assertEqual(following['loop']['review_started'],1)
        return following

    def test_single_review_then_fresh_report_completes(self):
        r=self.review(limit=1)
        self.assertIn('FINAL REVIEW —',r['send']['text'])
        self.assertIn('first save an in_progress',r['send']['text'])
        self.assertEqual(r['loop']['review_revision'],1)
        self.assertEqual(r['loop']['used'],1)
        self.acknowledge(r)
        done=self.finish(r)
        self.assertEqual(done['loop']['state'],'completed')
        self.assertEqual(done['loop']['review_completed_revision'],2)
        self.assertNotIn('send',done)
        self.assertEqual(len(self.call('status')['requests']),2)
        self.assertTrue(any('final review' in e['detail'] for e in self.call('status')['events']))

    def test_off_retains_existing_completion_behavior(self):
        r=self.start(enabled=False);self.checkpoint(r)
        done=self.finish(r)
        self.assertEqual(done['loop']['state'],'completed')
        self.assertEqual(done['loop']['used'],0)

    def test_old_checkpoint_cannot_stand_in_for_review(self):
        r=self.review();feedback=self.finish(r)
        self.assertIn('Final review needs a new checkpoint',feedback['send']['text'])
        self.assertEqual(feedback['loop']['review_completed_revision'],-1)
        stopped=self.finish(feedback)
        self.assertEqual(stopped['loop']['state'],'review_required')
        self.assertNotIn('send',stopped)

    def test_new_checkpoint_without_acknowledgement_is_not_accepted(self):
        r=self.review();self.checkpoint(r)
        feedback=self.finish(r)
        self.assertIn('BRIDGE_REVIEW',feedback['send']['text'])
        self.acknowledge(r)
        self.assertEqual(self.finish(feedback)['loop']['state'],'completed')

    def test_bare_or_foreign_acknowledgement_is_not_accepted(self):
        for entry in ['[BRIDGE_REVIEW:another-run] Inspected result','bare']:
            r=self.review()
            entry=('[BRIDGE_REVIEW:'+r['loop']['run_id']+']') if entry=='bare' else entry
            self.checkpoint(r,completed=[entry])
            self.assertNotEqual(self.finish(r)['loop']['state'],'completed')
            self.call('loop_stop')

    def test_review_can_continue_corrections_without_restarting_review(self):
        r=self.review()
        self.acknowledge(r,next_step='Correct verified layout defect and recheck')
        following=self.finish(r,'PENDING')
        self.assertEqual(following['loop']['review_revision'],1)
        self.assertEqual(following['loop']['used'],2)
        self.assertIn('Correct verified layout defect',following['send']['text'])
        self.acknowledge(r)
        self.assertEqual(self.finish(following)['loop']['state'],'completed')

    def test_budget_exhausted_before_review_is_explicit(self):
        r=self.start(limit=1);self.checkpoint(r,next_step='Finish implementation')
        following=self.finish(r,'PENDING');self.checkpoint(r)
        ended=self.finish(following)
        self.assertEqual(ended['loop']['state'],'review_required')
        self.assertEqual(ended['loop']['review_started'],0)
        self.assertIn('final review has not run',ended['loop']['reason'])
        self.assertNotIn('send',ended)

    def test_unknown_receipt_blocks_review_without_replay(self):
        r=self.start();self.checkpoint(r);self.receipt(r)
        with self.op.connection() as db:db.execute("UPDATE requests SET state='unknown'")
        done=self.finish(r)
        self.assertEqual(done['loop']['state'],'review_required')
        self.assertEqual(done['loop']['review_started'],0)
        self.assertNotIn('send',done)

    def test_delivery_uncertainty_stop_and_needs_user_never_restart(self):
        for reason in ['uncertain','stop','needs_user']:
            r=self.review()
            if reason=='uncertain':self.call('loop_result',id=r['send']['id'],state='uncertain')
            elif reason=='stop':self.call('loop_stop')
            else:self.finish(r,'NEEDS_USER')
            ended=self.call('loop_tick',snapshot={**self.snap,'user_count':r['loop']['expected_users'],'marker':{'nonce':r['loop']['nonce'],'decision':'DONE'}})
            self.assertNotIn('send',ended)
            self.assertEqual(ended['loop']['state'],'stopped')
            self.assertEqual(len(self.call('status')['requests']),2 if reason=='uncertain' else (4 if reason=='stop' else 6))

    def test_updates_before_review_take_priority(self):
        r=self.start();self.checkpoint(r)
        self.call('update_add',task_id=r['loop']['work_task_id'],id=uuid.uuid4().hex,text='Preserve mobile layout',mode='next_reply')
        following=self.finish(r)
        self.assertEqual(following['loop']['review_started'],0)
        self.assertIn('Preserve mobile layout',following['send']['text'])
        self.call('loop_result',id=following['send']['id'],state='observed_sent');self.checkpoint(r)
        review=self.finish(following)
        self.assertEqual(review['loop']['review_started'],1)
        self.assertIn('Preserve mobile layout',review['send']['text'])
        self.acknowledge(r)
        self.assertEqual(self.finish(review)['loop']['state'],'completed')

    def test_update_during_review_requires_new_report_and_keeps_review_instructions(self):
        r=self.review();self.acknowledge(r)
        self.call('update_add',task_id=r['loop']['work_task_id'],id=uuid.uuid4().hex,text='Use the latest requirement',mode='next_reply')
        update=self.finish(r)
        self.assertIn('FINAL REVIEW —',update['send']['text'])
        self.call('loop_result',id=update['send']['id'],state='observed_sent')
        feedback=self.finish(update)
        self.assertIn('predates the sent task updates',feedback['send']['text'])
        self.acknowledge(r)
        self.assertEqual(self.finish(feedback)['loop']['state'],'completed')

    def test_concurrent_done_reserves_one_review(self):
        r=self.start();self.checkpoint(r);self.call('loop_result',id=r['send']['id'],state='observed_sent')
        s={**self.snap,'user_count':r['loop']['expected_users'],'marker':{'nonce':r['loop']['nonce'],'decision':'DONE'}}
        def tick(_):
            c=Coordinator(self.root/'companion',work=self.work)
            try:return c.dispatch({**self.base,'action':'loop_tick','snapshot':s})
            finally:c.db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(tick,range(2)))
        self.assertEqual(sum('send' in r for r in results),1)
        self.assertEqual(self.call('status')['loop']['used'],1)

    def test_flag_validation_precedes_task_creation(self):
        with self.assertRaisesRegex(ValueError,'on or off'):
            self.start(enabled='true')
        with self.assertRaisesRegex(ValueError,'full workspace'):
            self.call('loop_start',goal='test',limit=1,final_review=True,snapshot=self.snap)
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM workspace_tasks').fetchone()[0],0)


if __name__=='__main__':unittest.main()
