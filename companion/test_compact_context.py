import unittest,json
from work_context import compact_checkpoint,continuation_review,resume_context
import test_work_context as h

class CompactTests(unittest.TestCase):
    setUp=h.WorkTests.setUp
    call=h.WorkTests.call
    start=h.WorkTests.start
    checkpoint=h.WorkTests.checkpoint
    receipt=h.WorkTests.receipt

    def test_success_history_stays_durable_and_does_not_fill_chat_context(self):
        r=self.start();self.checkpoint(r,completed=['Successfully checked '+str(i)+' '+('x'*250) for i in range(20)])
        for i in range(10):self.receipt(r,rid='check-'+str(i))
        work=self.call('status')['work'];p=work['context']['checkpoint']['progress']
        before=json.dumps(work,ensure_ascii=False);compact=json.loads(compact_checkpoint(work))
        self.assertEqual(compact['detail_in_task_context']['completed_entries'],20)
        self.assertEqual(compact['detail_in_task_context']['passed_validation_entries'],2)
        self.assertEqual(len(compact['recent_receipts']),3)
        self.assertLess(len(compact_checkpoint(work)),len(before)//2)
        self.assertEqual(json.dumps(work,ensure_ascii=False),before)
        self.assertEqual(len(self.call('status')['work']['context']['checkpoint']['progress']['completed']),20)

    def test_pending_unknown_recovery_failed_checks_remain_complete(self):
        r=self.start();self.checkpoint(r,next_step='Read original evidence')
        work=self.call('status')['work'];p=work['context']['checkpoint']['progress']
        p.update(pending=[{'description':'Never repeat uncertain action','state':'unknown'}],unverified=['Physical device untested'],recovery=['Restore from exact backup'],validation=[{'description':'Important check','status':'failed','evidence':'Error details must survive'}])
        work['evidence_checks']=[{'state':'invalid','detail':'Missing receipt must survive'}]
        compact=json.loads(compact_checkpoint(work))
        for k in ['pending','unverified','recovery']:self.assertEqual(compact[k],p[k])
        self.assertEqual(compact['checks_needing_attention'],p['validation'])
        self.assertEqual(compact['invalid_evidence'],work['evidence_checks'])
        self.assertIn('Read task_context',continuation_review(work,'PENDING')['context'])
        self.assertIn('Never repeat uncertain action',resume_context(work))

    def test_oversized_critical_context_stops_instead_of_truncating(self):
        r=self.start();self.checkpoint(r);work=self.call('status')['work']
        for name,value in [('pending',[{'description':'x'*7000}]),('unverified',['x'*7000]),('recovery',['x'*7000])]:
            work['context']['checkpoint']['progress'][name]=value
            self.assertTrue(continuation_review(work,'PENDING')['blocked'])
            with self.assertRaises(ValueError):resume_context(work)
            work['context']['checkpoint']['progress'][name]=[]

    def test_summary_omission_is_explicit_but_checks_remain_full(self):
        r=self.start();self.checkpoint(r);work=self.call('status')['work']
        work['context']['checkpoint']['progress']['summary']='x'*2000
        compact=json.loads(compact_checkpoint(work))
        self.assertEqual(len(compact['summary']),1200)
        self.assertEqual(compact['detail_in_task_context']['summary_characters'],800)

if __name__=='__main__':unittest.main()
