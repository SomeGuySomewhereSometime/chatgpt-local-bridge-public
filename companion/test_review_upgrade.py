import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from coordinator import Coordinator
from work_context import WorkContext, evidence_checks
from external_reviews import ReviewStore,DEFAULT,canonical
from external_review_worker import execute_cli
from validation_evidence import ValidationStore,source_snapshot
from operation_store import OperationStore
from review_context import git_diff

MODEL='opencode/mimo-v2.6-flash-free'

class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name);self.project=self.base/'project';self.project.mkdir()
        self.file=self.project/'code.py';self.file.write_text('result = 1\n')
        self.config={'memory':{'enabled':True,'path':str(self.base/'private/memory.sqlite3')},'filesystem':{'denied_paths':[str(self.base/'private')]},'operations':{'directory':str(self.base/'operations')}}
        self.env=patch.dict(os.environ,{'BRIDGE_REVIEWS_DIR':str(self.base/'reviews'),'BRIDGE_COMPANION_DIR':str(self.base/'companion'),'BRIDGE_VALIDATION_DIR':str(self.base/'validations')});self.env.start();self.addCleanup(self.env.stop)
        self.work=WorkContext(self.config);self.c=Coordinator(self.base/'companion',work=self.work);self.addCleanup(self.c.db.close)
        self.args={'url':'https://chatgpt.com/c/upgrade-test','client':'test','tab':1}
        self.call('bind',project=str(self.project),task='upgrade')
        self.run=self.call('loop_start',goal='Exact test goal',criteria='Exact criterion',limit=2,work_request_id='upgrade-work-0001',snapshot={'document_id':'upgrade-document-1234','state':'idle','user_count':0,'terminal':True})
        self.task=self.run['loop']['work_task_id'];self.reviews=ReviewStore(self.base/'reviews',self.config)
        self.policy=json.loads(canonical(DEFAULT));self.policy.update(enabled=True)
        self.reviews.configure(str(self.project),self.task,self.policy,0)
        with self.reviews.connection() as db:db.execute('INSERT INTO catalogue VALUES(1,?,?)',(time.time(),canonical({'models':[{'id':MODEL,'inputs':['text']}],'updated':time.time(),'stale':False})))
        self.validations=ValidationStore(self.config)
    def call(self,action,**kwargs):return self.c.dispatch(dict(self.args,action=action,**kwargs))
    def request(self,rid='upgrade-review-0001',**kwargs):return self.reviews.request(str(self.project),self.task,rid,'code','Find actual defects.','Concrete code behavior.',files=[{'path':'code.py'}],launch=False,**kwargs)
    def test_automatic_exact_context_active_updates_checkpoint(self):
        self.call('update_add',task_id=self.task,id='update-exact-123456',text='Use yellow instead.',mode='automatic')
        self.call('update_add',task_id=self.task,id='update-cancelled-123456',text='Ignore this.',mode='automatic')
        self.call('update_cancel',task_id=self.task,id='update-cancelled-123456',revision=1)
        self.work.store.checkpoint(str(self.project),task_id=self.task,expected_revision=0,checkpoint={'summary':'Actual checkpoint','next_step':'Verify'},state='in_progress',source='Fixture',request_id='context-checkpoint-001')
        job=self.request()['jobs'][0];packet=self.reviews.status(str(self.project),self.task,job_id=job['id'],include_result=True)['jobs'][0]['packet'];c=packet['task_context']
        self.assertEqual(c['goal'],'Exact test goal');self.assertEqual(c['criteria'],['Exact criterion']);self.assertEqual(c['checkpoint_revision'],1);self.assertEqual(c['checkpoint']['progress']['summary'],'Actual checkpoint')
        self.assertEqual([x['text'] for x in c['active_instructions']],['Use yellow instead.'])
        with self.assertRaises(Exception):self.reviews.packet(self.project,'another-task','code','Question','Reason','critique','',[],[])
    def test_same_id_retains_original_context_after_source_checkpoint_changes(self):
        first=self.request()['jobs'][0];self.file.write_text('changed')
        self.work.store.checkpoint(str(self.project),task_id=self.task,expected_revision=0,checkpoint={'summary':'Changed checkpoint','next_step':'Verify'},state='in_progress',source='Fixture',request_id='changed-checkpoint-001')
        again=self.request()['jobs'][0];self.assertEqual(first['id'],again['id'])
        packet=self.reviews.status(str(self.project),self.task,job_id=first['id'],include_result=True)['jobs'][0]['packet'];self.assertEqual(packet['files'][0]['content'],'result = 1\n')
    def test_budget_configurable_never_silently_loses_required_context(self):
        self.policy['context_budget_bytes']=32768;self.reviews.configure(str(self.project),self.task,self.policy,1)
        with self.assertRaisesRegex(ValueError,'configured budget'):self.request(context='x'*40000)
        self.assertEqual(self.reviews.status(str(self.project),self.task)['jobs'],[])
        self.policy['context_budget_bytes']=524288;self.reviews.configure(str(self.project),self.task,self.policy,2)
        self.assertTrue(self.request(context='x'*160000)['jobs'])
    def test_independent_avoids_checkpoint_solution_and_diff(self):
        self.work.store.checkpoint(str(self.project),task_id=self.task,expected_revision=0,checkpoint={'summary':'Our proposed architecture','next_step':'Verify'},state='in_progress',source='Fixture',request_id='independent-check-001')
        packet=self.reviews.packet(self.project,self.task,'plan','Independent approach','Consequential choice','independent','',[],[])
        self.assertIsNone(packet['task_context']['checkpoint']);self.assertFalse(packet['diff']['available']);self.assertEqual(packet['task_context']['goal'],'Exact test goal')
    def test_git_diff_explicit_baseline_bounded_without_task_start_claim(self):
        subprocess.run(['git','init','-q',str(self.project)],check=True)
        subprocess.run(['git','-C',str(self.project),'add','code.py'],check=True)
        subprocess.run(['git','-C',str(self.project),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','base'],check=True)
        self.file.write_text('result = 2\n'+'new content\n'*1000)
        diff=git_diff(self.project,['code.py'],limit=100);self.assertTrue(diff['available']);self.assertTrue(diff['truncated']);self.assertIn('not a task-start',diff['baseline']);self.assertLessEqual(len(diff['text']),100)
        self.assertFalse(git_diff(self.project,[],base='--bad')['available'])
    def test_automatic_diff_excludes_tracked_credentials(self):
        secret=self.project/'.env';secret.write_text('PLACEHOLDER_FIRST')
        subprocess.run(['git','init','-q',str(self.project)],check=True)
        subprocess.run(['git','-C',str(self.project),'add','.'],check=True)
        subprocess.run(['git','-C',str(self.project),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','base'],check=True)
        secret.write_text('PLACEHOLDER_SECOND');self.file.write_text('result = 4\n')
        diff=git_diff(self.project,[]);self.assertEqual(diff['excluded_files'],1)
        self.assertNotIn('PLACEHOLDER_SECOND',diff['text']);self.assertIn('result = 4',diff['text'])

    def test_stdin_preserves_long_unicode_multiline_payload_without_argv(self):
        job=self.request(context=('long áβ line\n'*18000))['jobs'][0]
        with self.reviews.connection() as db:row=dict(db.execute('SELECT * FROM jobs WHERE id=?',(job['id'],)).fetchone())
        cli=self.base/'fixture-cli';cli.write_text('''#!/usr/bin/python3
import sys,json,hashlib
from pathlib import Path
raw=sys.stdin.buffer.read()
Path('stdin-proof.json').write_text(json.dumps({'length':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'argv':sys.argv}))
print(json.dumps({'type':'text','part':{'text':json.dumps({'summary':'Checked fixture stdin','findings':[]})}}))
''');cli.chmod(0o700)
        with patch.dict(os.environ,{'BRIDGE_REVIEW_OPENCODE':str(cli)}):result=execute_cli(self.reviews,row,self.policy)
        folder=self.reviews.root/'runs'/job['id'];proof=json.loads((folder/'workspace/stdin-proof.json').read_text());sent=(folder/'prompt.stdin').read_bytes()
        self.assertGreater(proof['length'],180000);self.assertEqual(proof['sha256'],hashlib.sha256(sent).hexdigest());self.assertLess(sum(map(len,proof['argv'])),1000);self.assertEqual(result['findings'],[])
    def test_large_full_results_paginated_version_checked(self):
        jid=self.request(context='x'*300000)['jobs'][0]['id']
        with self.reviews.connection() as db:db.execute("UPDATE jobs SET state='completed',result=? WHERE id=?",(canonical({'response':'r'*70000,'summary':'Summary','findings':[]}),jid))
        offset=0;version='';body=''
        while True:
            page=self.reviews.detail_page(str(self.project),self.task,jid,offset,version);self.assertLess(len(canonical(page).encode()),200000)
            version=page['version'];body+=page['text'];offset=page['next_offset']
            if offset is None:break
        self.assertEqual(json.loads(body)['result']['response'],'r'*70000)
        with self.assertRaisesRegex(ValueError,'changed'):self.reviews.detail_page(str(self.project),self.task,jid,24000,'wrong')
    def test_investigate_queues_once_no_chat_send_no_provider(self):
        self.call('loop_stop');jid=self.request()['jobs'][0]['id']
        with self.reviews.connection() as db:db.execute("UPDATE jobs SET state='completed',result=? WHERE id=?",(canonical({'summary':'Finding','findings':[{'id':'finding-1','description':'Check one behavior'}]}),jid))
        with patch.dict(os.environ,{'BRIDGE_REVIEWS_DIR':str(self.reviews.root)}):
            one=self.call('reviews_investigate',task_id=self.task,job_id=jid,finding_id='finding-1',id='investigate-test-123456');two=self.call('reviews_investigate',task_id=self.task,job_id=jid,finding_id='finding-1',id='investigate-test-123456')
        self.assertFalse(one['message_sent']);self.assertEqual(len(two['updates']),1);self.assertIn('not proof',two['updates'][0]['text'])
    def test_validation_tracks_changes_during_after_and_added_files(self):
        check=self.validations.begin(self.project,self.task,'version-check-0001',['.'])
        end=self.validations.finish(self.project,self.task,check['id'],'Manual render inspected','passed');self.assertEqual(end['freshness'],'current');self.assertEqual(end['outcome_basis'],'assistant_report')
        self.file.write_text('changed');self.assertEqual(self.validations.status(self.project,self.task,check['id'])['freshness'],'stale')
        later=self.validations.begin(self.project,self.task,'version-check-0002',['.']);(self.project/'new.py').write_text('new')
        self.assertEqual(self.validations.finish(self.project,self.task,later['id'],'Test','passed')['freshness'],'changed_during_check')
        self.assertEqual(evidence_checks([{'description':'Check','evidence':end['reference']}],[],{check['id']:self.validations.status(self.project,self.task,check['id'])})[0]['state'],'invalid')
    def test_validation_command_proof_scope_and_no_cross_task(self):
        check=self.validations.begin(self.project,self.task,'command-check-0001',['code.py'])
        op=OperationStore(self.base/'operations');op.reserve('command-receipt-0001',str(self.project),'run_command',['fixture']);op.finish('command-receipt-0001',0)
        self.work.store.link_operation(str(self.project),self.task,'command-receipt-0001',str(self.project))
        end=self.validations.finish(self.project,self.task,check['id'],'Command validation','passed','command-receipt-0001',str(self.project));self.assertTrue(end['receipt_confirmed'])
        with self.assertRaises(ValueError):self.validations.status(self.project,'another-task',check['id'])
        with self.assertRaisesRegex(ValueError,'immutable'):self.validations.finish(self.project,self.task,check['id'],'Changed description','passed')
        self.assertEqual(evidence_checks([{'description':'Check','evidence':end['reference']}],[],{check['id']:end})[0]['state'],'version_confirmed')
    def test_validation_symlinks_denied_and_excluded_scope(self):
        (self.project/'link.py').symlink_to(self.file)
        for path in ['link.py','../private','.env']:
            with self.assertRaises((OSError,ValueError)):source_snapshot(self.project,[path],self.config)
        (self.project/'node_modules').mkdir();(self.project/'node_modules/new.js').write_text('ignored')
        data=source_snapshot(self.project,['.'],self.config);self.assertEqual(set(data['files']),{'code.py'})
    def test_malformed_version_reference_invalid(self):
        self.assertEqual(evidence_checks([{'description':'Check','evidence':'[validation:invalid]'}],[])[0]['state'],'invalid')

if __name__=='__main__':unittest.main()
