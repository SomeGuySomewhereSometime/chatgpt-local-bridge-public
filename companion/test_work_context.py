import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid

from coordinator import Coordinator
from work_context import WorkContext, completion_issue
from operation_store import OperationStore


class WorkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        config = {'memory':{'enabled':True,'path':str(self.root/'private/memory.sqlite3')},
            'filesystem':{'denied_paths':[str(self.root/'private')]},
            'operations':{'directory':str(self.root/'operations')}}
        self.work = WorkContext(config)
        self.c = Coordinator(self.root/'companion',work=self.work);self.addCleanup(self.c.db.close)
        self.op = OperationStore(self.root/'operations')
        self.base = {'url':'https://chatgpt.com/c/work-test','client':'one','tab':7}
        self.snap = {'document_id':'document-work-123456','state':'idle','user_count':0,'terminal':True}
        self.call('bind',project=str(self.root),task='Reference')
        self.call('observe',state='idle')

    def call(self,action,**p):
        return self.c.dispatch(dict(self.base,action=action,**p))

    def start(self,**extra):
        return self.call('loop_start',goal='Improve the local game',criteria='Rules pass\nLayout works',limit=1,
                         work_request_id=uuid.uuid4().hex,snapshot=self.snap,**extra)

    def finish(self,r,decision='DONE'):
        self.call('loop_result',id=r['send']['id'],state='observed_sent')
        return self.call('loop_tick',snapshot={**self.snap,'user_count':r['loop']['expected_users'],
                        'marker':{'nonce':r['loop']['nonce'],'decision':decision}})

    def checkpoint(self,r,**overrides):
        tid=r['loop']['work_task_id'];current=self.work.read(str(self.root),tid)['context']
        progress={'summary':'Implementation complete','completed':['Cards updated'],
                  'validation':[{'description':'Rules pass','status':'passed','evidence':'Receipt reference'},{'description':'Layout works','status':'passed','evidence':'Reported browser inspection'}]}
        progress.update(overrides)
        return self.work.store.checkpoint(str(self.root),task_id=tid,expected_revision=current['selected_revision'],
            checkpoint=progress,state='completed' if not overrides.get('next_step') else 'in_progress',
            source='Test model checkpoint',request_id=uuid.uuid4().hex)

    def receipt(self,r,rid='operation-1',code=0):
        self.op.reserve(rid,str(self.root),'run_command',['fixture'])
        self.op.finish(rid,code)
        self.work.store.link_operation(str(self.root),r['loop']['work_task_id'],rid,str(self.root))

    def test_goal_link_prompt_and_duplicate_start(self):
        r=self.start();tid=r['loop']['work_task_id']
        self.assertIn(tid,r['send']['text']);self.assertIn('PROJECT ROOT: '+str(self.root),r['send']['text'])
        self.assertIn('Rules pass',r['send']['text'])
        work=self.call('status')['work'];self.assertEqual(work['criteria'],['Rules pass','Layout works'])
        self.assertEqual(work['context']['selected_revision'],0)
        self.call('loop_stop')
        rid=self.c.db.execute('SELECT prepare_id FROM workspace_tasks').fetchone()[0]
        with self.assertRaisesRegex(ValueError,'already started'):
            self.call('loop_start',goal='Improve the local game',criteria='Rules pass\nLayout works',limit=1,work_request_id=rid,snapshot=self.snap)
        self.assertEqual(len(self.call('status')['requests']),1)

    def test_done_requires_completed_checkpoint(self):
        r=self.start();feedback=self.finish(r)
        self.assertIn('No checkpoint',feedback['send']['text'])
        self.assertEqual(feedback['loop']['feedback_count'],1)
        end=self.finish(feedback)
        self.assertEqual(end['loop']['state'],'review_required')
        self.assertIn('No checkpoint',end['loop']['reason'])

    def test_complete_reports_separate_real_receipt(self):
        r=self.start();self.receipt(r);self.checkpoint(r)
        end=self.finish(r);self.assertEqual(end['loop']['state'],'completed')
        work=self.call('status')['work'];self.assertEqual(work['receipts'][0]['returncode'],0)
        self.assertEqual(work['context']['checkpoint']['source'],'Test model checkpoint')

    def test_failed_receipt_is_visible_and_not_erased_by_correction(self):
        r=self.start();self.receipt(r,code=1);self.receipt(r,rid='correction',code=0);self.checkpoint(r)
        self.assertEqual(self.finish(r)['loop']['state'],'completed')
        self.assertEqual(self.call('status')['work']['failed_receipts'],1)

    def test_dead_owner_readonly_and_unresolved_gate(self):
        r=self.start();self.receipt(r);self.checkpoint(r)
        with self.op.connection() as db:
            db.execute("UPDATE requests SET state='running',owner_pid=0,owner_birth='dead'")
        end=self.finish(r);self.assertEqual(end['loop']['state'],'review_required')
        self.assertIn('uncertain',end['loop']['reason'])
        self.assertEqual(self.call('status')['work']['receipts'][0]['state'],'unknown')
        with sqlite3.connect(self.op.path) as db:
            self.assertEqual(db.execute('SELECT state FROM requests').fetchone()[0],'running')

    def test_all_linked_receipts_checked_beyond_display_window(self):
        r=self.start()
        for i in range(22):self.receipt(r,rid='op-'+str(i))
        with self.op.connection() as db:db.execute("UPDATE requests SET state='unknown' WHERE request_id='op-0'")
        self.checkpoint(r);self.assertEqual(self.finish(r)['loop']['state'],'review_required')
        work=self.call('status')['work'];self.assertEqual(len(work['receipts']),20)
        self.assertEqual(work['receipt_count'],22);self.assertEqual(work['unresolved_receipts'],1)

    def test_resume_requires_checkpoint_and_explicit_uncertainty_ack(self):
        r=self.start();self.call('loop_result',id=r['send']['id'],state='uncertain')
        with self.assertRaisesRegex(ValueError,'uncertain'):self.call('work_resume_preview')
        with self.assertRaisesRegex(ValueError,'checkpoint'):self.call('work_resume_preview',ack_id=r['send']['id'])
        self.checkpoint(r,next_step='Inspect current cards')
        reviewed=self.call('work_resume_preview',ack_id=r['send']['id'])
        self.assertIn('Inspect current cards',reviewed['checkpoint_text'])
        self.assertEqual(len(self.call('status')['requests']),1)
        resumed=self.call('loop_start',goal=r['loop']['goal'],limit=1,snapshot=self.snap,resume=reviewed['resume'])
        self.assertNotEqual(resumed['send']['id'],r['send']['id'])
        self.assertEqual(resumed['loop']['used'],0)
        self.assertIn('REVIEWED RESUME CONTEXT',resumed['send']['text'])
        self.assertEqual(resumed['loop']['work_task_id'],r['loop']['work_task_id'])

    def test_stale_resume_revision_run_and_project_blocked(self):
        r=self.start();self.call('loop_result',id=r['send']['id'],state='observed_sent');self.call('loop_stop');self.checkpoint(r,next_step='Inspect files')
        reviewed=self.call('work_resume_preview')
        self.checkpoint(r,next_step='Run rules tests')
        with self.assertRaisesRegex(ValueError,'changed'):
            self.call('loop_start',goal='Improve the local game',limit=1,snapshot=self.snap,resume=reviewed['resume'])
        self.call('bind',project=str(self.root),task='Changed');self.call('observe',state='idle')
        with self.assertRaisesRegex(ValueError,'linked'):self.call('work_resume_preview')

    def test_manual_save_revision_task_and_active_guards(self):
        r=self.start();tid=r['loop']['work_task_id'];fields={'summary':'Cards done','pending':'Test rules','next_step':'Run tests'}
        with self.assertRaisesRegex(ValueError,'Stop'):
            self.call('work_save',task_id=tid,revision=0,fields=fields,id=uuid.uuid4().hex)
        self.call('loop_stop');saved=self.call('work_save',task_id=tid,revision=0,fields=fields,id=uuid.uuid4().hex)
        self.assertEqual(saved['work']['context']['selected_revision'],1)
        self.assertIn('user-entered',saved['work']['context']['checkpoint']['source'])
        with self.assertRaisesRegex(ValueError,'revision conflict'):
            self.call('work_save',task_id=tid,revision=0,fields=fields,id=uuid.uuid4().hex)
        with self.assertRaisesRegex(ValueError,'linked task changed'):
            self.call('work_save',task_id='another-task',revision=1,fields=fields,id=uuid.uuid4().hex)

    def test_invalid_start_and_foreign_client_do_not_create_tasks(self):
        with self.assertRaises(ValueError):self.call('loop_start',goal='test',criteria='x'*301,limit=1,work_request_id=uuid.uuid4().hex,snapshot=self.snap)
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM workspace_tasks').fetchone()[0],0)
        with self.assertRaisesRegex(ValueError,'does not match'):
            self.c.dispatch({**self.base,'client':'other','action':'work_save'})
        self.assertEqual(len(self.call('status')['requests']),0)

    def test_declared_done_missing_criterion_or_evidence_requires_review(self):
        for validations in ([{'description':'Rules pass','status':'passed','evidence':'Rules test receipt'}],
                            [{'description':'Rules pass','status':'passed','evidence':''}]):
            r=self.start();self.checkpoint(r,validation=validations)
            feedback=self.finish(r);self.assertIn('send',feedback)
            result=self.finish(feedback);self.assertEqual(result['loop']['state'],'review_required')
            self.assertIn('Completion needs review',result['loop']['reason'])

    def test_bridge_disabled_stops_before_send(self):
        from memory_store import MemoryError
        with self.assertRaises(MemoryError):WorkContext({'memory':{'enabled':False}})

if __name__=='__main__':unittest.main()
