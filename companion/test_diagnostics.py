import json
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from coordinator import Coordinator
import run_diagnostics as diagnostics

class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.c=Coordinator(self.tmp.name)
        self.base={'url':'https://chatgpt.com/c/diagnostic-test','client':'client','tab':1}
        self.s={'document_id':'document-1234567890','state':'idle','user_count':2,'terminal':True,'marker':None}
        self.call('bind',project=self.tmp.name,task='')
        self.call('observe',state='idle')
    def tearDown(self):self.c.db.close();self.tmp.cleanup()
    def call(self,action,**kwargs):return self.c.dispatch({**self.base,'action':action,**kwargs})
    def start(self,debug=False):return self.call('loop_start',goal='Do secret task PASSWORD-DO-NOT-LOG',limit=1,snapshot=self.s,debug=debug)
    def export(self,run):return self.call('diagnostics_export',run_id=run['loop']['run_id'])['diagnostic_export']
    def test_debug_does_not_change_prompt_or_continuation(self):
        with patch('goal_loop.uuid.uuid4') as u:
            u.side_effect=[SimpleNamespace(hex=v) for v in ['1'*32,'a'*32,'2'*32,'3'*32,'a'*32,'4'*32]]
            off=self.start(False);self.call('loop_stop');on=self.start(True)
        self.assertEqual(off['send']['text'],on['send']['text'])
        self.call('loop_result',id=on['send']['id'],state='observed_sent')
        answer={**self.s,'user_count':on['loop']['expected_users'],'marker':{'nonce':on['loop']['nonce'],'decision':'PENDING'}}
        next_run=self.call('loop_tick',snapshot=answer)
        self.assertEqual(next_run['loop']['used'],1)
        self.call('loop_result',id=next_run['send']['id'],state='uncertain',error='New message found, but its text did not match. PASSWORD-DO-NOT-LOG')
        for _ in range(3):self.assertNotIn('send',self.call('loop_tick',snapshot=answer))
        exported=self.export(on);self.assertNotIn('PASSWORD-DO-NOT-LOG',json.dumps(exported))
        self.assertIn('delivery_text_mismatch',[x['code'] for x in exported['events']])
    def test_trace_off_on_toggle_and_run_scope(self):
        run=self.start();self.call('loop_validate',id=run['send']['id'])
        self.assertNotIn('loop_validate',[x['action'] for x in self.export(run)['events']])
        self.call('diagnostics_set',run_id=run['loop']['run_id'],enabled=True)
        self.call('loop_validate',id=run['send']['id'])
        self.assertIn('loop_validate',[x['action'] for x in self.export(run)['events']])
        self.call('diagnostics_set',run_id=run['loop']['run_id'],enabled=False)
        count=len(self.export(run)['events']);self.call('loop_validate',id=run['send']['id'])
        self.assertEqual(count,len(self.export(run)['events']))
        with self.assertRaises(ValueError):self.call('diagnostics_set',run_id='other',enabled=True)
        self.call('loop_stop');next_run=self.start()
        self.assertFalse(self.call('status')['diagnostics']['enabled'])
        self.assertNotEqual(run['loop']['run_id'],next_run['loop']['run_id'])
    def test_rejected_action_no_content_and_owner_only(self):
        run=self.start()
        with self.assertRaises(ValueError):self.call('prepare',id='id-test',text='secret-private-text')
        exported=self.export(run)
        self.assertIn('rejected',[x['code'] for x in exported['events']])
        count=len(exported['events'])
        with self.assertRaises(ValueError):self.c.dispatch({**self.base,'action':'diagnostics_export','client':'other','run_id':run['loop']['run_id']})
        self.assertEqual(count,len(self.export(run)['events']))
        self.assertNotIn('secret-private-text',json.dumps(exported))
    def test_logger_failure_preserves_delivery_and_status(self):
        with patch('run_diagnostics.record',side_effect=RuntimeError('storage failed')):
            run=self.start(True)
            self.assertEqual(self.call('loop_result',id=run['send']['id'],state='observed_sent')['loop']['state'],'waiting')
        with patch('run_diagnostics.summary',side_effect=__import__('sqlite3').OperationalError('database failed')):
            result=self.call('status');self.assertEqual(result['loop']['state'],'waiting');self.assertFalse(result['diagnostics']['available'])
    def test_bounds_and_whitelist(self):
        run=self.start(True)
        for i in range(diagnostics.PER_RUN+20):
            self.call('loop_validate',id=run['send']['id'],diagnostic_meta={'text':'secret','output':'secret','error':'secret','elapsed_ms':i,'text_length':200,'clicked':True,'extension_version':'0.9.0'})
        exported=self.export(run)
        self.assertEqual(len(exported['events']),diagnostics.PER_RUN)
        self.assertNotIn('secret',json.dumps(exported))
        self.assertEqual(exported['events'][-1]['metadata']['text_length'],200)
        with patch.object(diagnostics,'TOTAL',10):
            self.call('loop_validate',id=run['send']['id'])
        self.assertEqual(len(self.export(run)['events']),10)
        self.c.db.execute('UPDATE diagnostic_events SET created=0');self.c.db.commit()
        self.call('loop_validate',id=run['send']['id']);self.assertEqual(len(self.export(run)['events']),1)
    def test_expired_runs_not_exported_and_setup_failure_optional(self):
        run=self.start(True)
        self.c.db.execute('UPDATE diagnostic_runs SET started=0');self.c.db.commit()
        self.assertEqual(self.call('status')['diagnostics']['runs'],[])
        with self.assertRaises(ValueError):self.export(run)
        self.c.db.execute('UPDATE goal_loops SET started=0');self.c.db.commit()
        self.assertEqual(self.call('status')['diagnostics']['run_id'],'')
        self.call('diagnostics_set',run_id='',enabled=True)
        self.assertTrue(self.call('status')['diagnostics']['enabled'])
        self.call('loop_stop')
        self.call('loop_start',goal='New task after expiry',limit=1,snapshot=self.s)
        self.assertTrue(self.call('status')['diagnostics']['enabled'])
        with tempfile.TemporaryDirectory() as tmp, patch('run_diagnostics.setup',side_effect=__import__('sqlite3').OperationalError('unavailable')):
            c=Coordinator(tmp)
            self.assertIn('bindings',c.dispatch({'action':'list','client':'client'}));c.db.close()

    def test_next_run_setting_and_restart(self):
        self.call('diagnostics_set',run_id='',enabled=True)
        result=self.call('loop_start',goal='test',limit=1,snapshot=self.s)
        self.assertTrue(self.call('status')['diagnostics']['enabled'])
        self.c.db.close();self.c=Coordinator(self.tmp.name)
        self.assertTrue(self.call('status')['diagnostics']['enabled'])
        self.assertTrue(self.export(result)['events'])

if __name__=='__main__':unittest.main()
