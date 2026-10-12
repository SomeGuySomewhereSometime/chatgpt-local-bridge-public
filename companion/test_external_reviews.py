import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from external_reviews import ReviewStore, DEFAULT, canonical, free_catalogue, instructions
from external_review_worker import parse_events, cli_environment, main, project_snapshot, reviewer_prompt, verify_project_copy
from test_work_context import WorkTests

MODEL='opencode/mimo-v2.6-flash-free'


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.project=self.root/'project';self.project.mkdir()
        self.file=self.project/'code.py';self.file.write_text('def ratio(a,b):\n    return a / b\n')
        self.store=ReviewStore(self.root/'reviews');self.task='task-one'
        self.policy=json.loads(canonical(DEFAULT));self.policy.update(enabled=True,mode='required',stages=['code'])
        self.store.configure(str(self.project),self.task,self.policy,0)
        with self.store.connection() as db:db.execute('INSERT INTO catalogue VALUES(1,?,?)',(time.time(),canonical({'models':[{'id':MODEL,'name':'MiMo','inputs':['text','image']}],'updated':time.time(),'stale':False})))
        self.addCleanup(patch.stopall)
        patch.dict(os.environ,{'BRIDGE_COMPANION_DIR':str(self.root/'companion')}).start()

    def request(self,rid='test-review-001',**kwargs):
        return self.store.request(str(self.project),self.task,rid,'code','Find concrete defects in division.','Division must handle zero safely.',files=[{'path':'code.py'}],launch=False,**kwargs)

    def completed(self,result=None):
        r=self.request();jid=r['jobs'][0]['id']
        result=result or {'summary':'Possible zero divisor','findings':[{'id':'finding-1','description':'Zero divisor raises','evidence':'code.py:2','severity':'medium'}]}
        with self.store.connection() as db:db.execute("UPDATE jobs SET state='completed',result=? WHERE id=?",(canonical(result),jid))
        return jid

    def decide(self,jid,disposition='rejected'):
        return self.store.decision(str(self.project),self.task,jid,'Checked the finding against the required input contract.',
            [{'id':'finding-1','disposition':disposition,'reason':'The input contract excludes zero divisors.','evidence':'Contract and division checks.'}])

    def test_off_has_no_prompt_or_calls(self):
        self.assertEqual(instructions(DEFAULT),'')
        self.store.configure(str(self.project),self.task,DEFAULT,1)
        with patch('external_reviews.fetch_json',side_effect=AssertionError('Network while off')):
            self.assertIsNone(self.store.task_summary(str(self.project),self.task))
            with self.assertRaisesRegex(ValueError,'off'):self.request()

    def test_policy_versions_and_model_identity(self):
        with self.assertRaisesRegex(ValueError,'changed'):self.store.configure(str(self.project),self.task,self.policy,0)
        for model in ['opencode/jev-1.13-free','openai/gpt','opencode/../paid']:
            p=json.loads(canonical(self.policy));p['models']['code']=[model]
            with self.assertRaises(ValueError):self.store.configure(str(self.project),self.task,p,1)

    def test_free_catalogue_requires_price_and_live_availability(self):
        metadata={'opencode':{'models':{'paid-free':{'cost':{'input':1,'output':0}},'unknown':{},'gone':{'cost':{'input':0,'output':0}},'big-pickle':{'cost':{'input':0,'output':0}},'jev-1.13-free':{'cost':{'input':0,'output':0}}}}}
        live={'data':[{'id':x} for x in ['paid-free','unknown','big-pickle','jev-1.13-free']]}
        self.assertEqual([m['id'] for m in free_catalogue(metadata,live)],['opencode/big-pickle'])

    def test_idempotence_and_reuse_no_new_jobs(self):
        first=self.request();second=self.request();third=self.request('new-review-002')
        self.assertEqual(first['jobs'][0]['id'],second['jobs'][0]['id']);self.assertTrue(third['reused'])
        repeated=self.request('explicit-review-003',repeat=True)
        self.assertNotEqual(first['jobs'][0]['id'],repeated['jobs'][0]['id'])
        with self.assertRaisesRegex(ValueError,'different parameters'):
            self.store.request(str(self.project),self.task,'test-review-001','code','Another question','A justified new question.',files=[{'path':'code.py'}],launch=False)

    def test_unknown_job_not_replayed(self):
        first=self.request();jid=first['jobs'][0]['id']
        with self.store.connection() as db:db.execute("UPDATE jobs SET owner_pid=99999999,owner_birth='bad' WHERE id=?",(jid,))
        again=self.request();self.assertEqual(again['jobs'][0]['id'],jid);self.assertEqual(again['jobs'][0]['state'],'uncertain')
        with self.assertRaisesRegex(ValueError,'uncertain'):self.request('new-uncertain-review',repeat=True)
        self.store.cancel(str(self.project),self.task,jid)
        self.assertNotEqual(self.request('new-explicit-review',repeat=True)['jobs'][0]['id'],jid)

    def test_plan_copy_excludes_dependencies_config_and_credentials(self):
        for name in ['node_modules','.opencode','.git']:
            (self.project/name).mkdir();(self.project/name/'excluded.txt').write_text('excluded')
        (self.project/'opencode.json').write_text('{"mcp":{}}');(self.project/'.env').write_text('excluded')
        output=self.root/'copy';output.mkdir()
        versions,omitted=project_snapshot(self.store,self.project,output)
        self.assertEqual({v['path'] for v in versions},{'code.py'});self.assertIn('.env',omitted)
        self.assertEqual((output/'code.py').read_bytes(),self.file.read_bytes());self.assertFalse((output/'.opencode').exists())

    def test_plan_uses_native_permissions_without_bridge_tool_allowlist(self):
        folder=self.root/'cli-plan';folder.mkdir();env=cli_environment(folder,self.project,'plan')
        cfg=json.loads((folder/'opencode.json').read_text())
        self.assertNotIn('permission',cfg);self.assertNotIn('agent',cfg)
        self.assertNotIn('OPENCODE_PERMISSION',env);self.assertNotIn('OPENCODE_DISABLE_LSP_DOWNLOAD',env)
        self.assertEqual(cfg['mcp'],{});self.assertEqual(cfg['plugin'],[])
        self.assertNotIn('MCP_TOKEN',env)

    def test_plan_copy_accepts_test_artifacts_without_source_changes(self):
        output=self.root/'plan-artifacts';output.mkdir()
        versions,_=project_snapshot(self.store,self.project,output)
        (output/'test-output.txt').write_text('Diagnostic result')
        verify_project_copy(self.store,output,versions)

    def test_plan_copy_rejects_source_changes_or_replaced_files(self):
        output=self.root/'plan-source';output.mkdir()
        versions,_=project_snapshot(self.store,self.project,output)
        (output/'code.py').write_text('changed source')
        with self.assertRaisesRegex(ValueError,'source changed'):verify_project_copy(self.store,output,versions)
        (output/'code.py').unlink();(output/'code.py').symlink_to(self.file)
        with self.assertRaisesRegex(ValueError,'source changed'):verify_project_copy(self.store,output,versions)

    def test_plan_accepts_native_tools_but_other_modes_still_restrict_them(self):
        response={'summary':'Diagnostic command examined division','findings':[],'limitations':[]}
        for tool in ('bash','shell','websearch','todowrite','lsp'):
            data='\n'.join([canonical({'type':'tool_use','part':{'tool':tool,'state':{'status':'completed'}}}),
                           canonical({'type':'text','part':{'text':canonical(response)}})])
            self.assertEqual(parse_events(data,None)['snapshot_tools_used'],[tool])
            for allowed in ((),('read','grep','glob')):
                with self.assertRaisesRegex(ValueError,'unavailable'):parse_events(data,allowed)
        for tool in (None,''):
            with self.assertRaisesRegex(ValueError,'unavailable'):
                parse_events(canonical({'type':'tool_use','part':{'tool':tool}}),None)

    def test_plan_does_not_accept_native_permission_failures_as_success(self):
        event={'type':'tool_use','part':{'tool':'edit','state':{'status':'error','error':'Permission denied'}}}
        with self.assertRaisesRegex(ValueError,'unavailable'):parse_events(canonical(event),None)

    def test_review_prompts_match_tools_and_keep_review_responsibility(self):
        plan=reviewer_prompt('plan');self.assertIn('diagnostic commands and tests',plan)
        self.assertIn('do not implement fixes',plan);self.assertNotIn('Do not claim you ran tests',plan)
        for tools in ('snapshots','context'):
            prompt=reviewer_prompt(tools);self.assertIn('Never browse, run commands, edit or delegate',prompt)
            self.assertIn('Do not claim you ran tests',prompt)
        p=json.loads(canonical(self.policy));p['tools']='plan'
        self.assertIn('native OpenCode Plan tools',instructions(p));self.assertNotIn('they cannot edit or run commands',instructions(p))
        p['tools']='snapshots';self.assertIn('they cannot edit or run commands',instructions(p))

    def test_snapshot_permissions_remain_read_search_only(self):
        folder=self.root/'cli-snapshots';folder.mkdir();env=cli_environment(folder,self.project,'snapshots')
        cfg=json.loads((folder/'opencode.json').read_text())
        self.assertEqual(cfg['permission']['*'],'ask');self.assertEqual(cfg['permission']['glob'],'allow')
        self.assertEqual(cfg['permission']['grep'],'allow');self.assertNotIn('webfetch',cfg['permission'])
        self.assertEqual(json.loads(env['OPENCODE_PERMISSION']),cfg['permission'])

    def test_atomic_task_and_future_configuration(self):
        p=json.loads(canonical(self.policy));p['concurrency']=4
        with self.assertRaisesRegex(ValueError,'changed'):
            self.store.configure_many(str(self.project),[(self.task,p,1),('future',p,99)])
        self.assertEqual(self.store.policy(str(self.project),self.task)['settings']['concurrency'],3)

    def test_cross_project_and_task_isolation(self):
        self.request();self.assertEqual(self.store.status(str(self.project),'other')['jobs'],[])
        jid=self.completed()
        with self.assertRaisesRegex(ValueError,'completed'):self.store.decision('/other',self.task,jid,'Not this project context.',[])

    def test_required_real_result_and_every_finding_evaluation(self):
        self.assertTrue(self.store.task_summary(str(self.project),self.task)['missing'])
        jid=self.completed();self.assertTrue(self.store.task_summary(str(self.project),self.task)['missing'])
        with self.assertRaisesRegex(ValueError,'every'):self.store.decision(str(self.project),self.task,jid,'Evaluated actual code context.',[])
        self.decide(jid,'needs_context');self.assertTrue(self.store.task_summary(str(self.project),self.task)['missing'])
        self.decide(jid);self.assertEqual(self.store.task_summary(str(self.project),self.task)['missing'],[])

    def test_changed_files_and_policy_invalidate_required_review(self):
        jid=self.completed();self.decide(jid);self.file.write_text('new code\n')
        self.assertTrue(self.store.task_summary(str(self.project),self.task)['missing'])
        with self.assertRaisesRegex(ValueError,'stale'):self.decide(jid)
        self.file.write_text('def ratio(a,b):\n    return a / b\n')
        p=json.loads(canonical(self.policy));p['concurrency']=2;self.store.configure(str(self.project),self.task,p,1)
        self.assertTrue(self.store.task_summary(str(self.project),self.task)['missing'])

    def test_selected_context_ranges_and_file_boundary(self):
        p=self.store.packet(self.project,self.task,'code','Question?','Specific reason.','critique','', [{'path':'code.py','start_line':2,'end_line':2}],[])
        self.assertEqual(p['files'][0]['content'],'    return a / b\n');self.assertEqual(p['files'][0]['start_line'],2)
        (self.project/'link.py').symlink_to(self.file)
        (self.project/'.env').write_text('unused')
        for path in ['../outside','link.py','.env']:
            with self.assertRaises(ValueError):self.store.packet(self.project,self.task,'code','Question?','Specific reason.','critique','',[{'path':path}],[])

    def test_visual_requires_real_image_and_capability(self):
        with self.assertRaisesRegex(ValueError,'image'):self.store.packet(self.project,self.task,'visual','Hierarchy?','Inspect readability.','critique','',[],[])
        (self.project/'image.png').write_bytes(b'\x89PNG\r\n\x1a\n')
        p=self.store.packet(self.project,self.task,'visual','Hierarchy?','Inspect readability.','critique','',[],['image.png']);self.assertEqual(len(p['images']),1)

    def test_all_selected_models_and_stages_required(self):
        p=json.loads(canonical(self.policy));p['models']['code']=[MODEL,'opencode/big-pickle'];self.store.configure(str(self.project),self.task,p,1)
        r=self.request(models=[MODEL]);jid=r['jobs'][0]['id']
        with self.store.connection() as db:db.execute("UPDATE jobs SET state='completed',result=? WHERE id=?",(canonical({'summary':'Division finding','findings':[{'id':'finding-1','description':'Check divisor'}]}),jid))
        self.decide(jid)
        missing=self.store.task_summary(str(self.project),self.task)['missing'];self.assertEqual(missing,['code · opencode/big-pickle'])

    def test_optional_limit_and_cancellation(self):
        p=json.loads(canonical(self.policy));p['limit']=1;self.store.configure(str(self.project),self.task,p,1)
        r=self.request();self.store.cancel(str(self.project),self.task,r['jobs'][0]['id'])
        self.assertEqual(self.store.status(str(self.project),self.task)['jobs'][0]['state'],'cancelled')
        with self.assertRaisesRegex(ValueError,'limit'):self.request('second-review-002',repeat=True)

    def test_errors_tool_calls_and_truncated_outputs_not_success(self):
        for event in [{'type':'tool_use'},{'type':'error','error':{'name':'APIError','data':{'message':'denied'}}},
                      {'type':'step_finish','part':{'reason':'length'}}]:
            with self.assertRaises(ValueError):parse_events(canonical(event))
        response={'summary':'No issue found in supplied snippet','findings':[],'limitations':['No runtime checks.']}
        parsed=parse_events(canonical({'type':'text','part':{'text':canonical(response)}}))
        self.assertEqual(parsed['findings'],[]);self.assertIn('No runtime',parsed['limitations'][0])
        padded='I inspected the supplied copy.\n```json\n'+canonical(response)+'\n```'
        self.assertEqual(parse_events(canonical({'type':'text','part':{'text':padded}}))['summary'],response['summary'])

    def test_cli_environment_denies_tools_no_inherited_credentials(self):
        d=self.root/'cli';d.mkdir()
        with patch.dict(os.environ,{'OPENAI_API_KEY':'never-copy','SSH_AUTH_SOCK':'never-copy'}):env=cli_environment(d)
        self.assertNotIn('OPENAI_API_KEY',env);self.assertNotIn('SSH_AUTH_SOCK',env)
        cfg=json.loads((d/'opencode.json').read_text());self.assertEqual(cfg['permission'],{'*':'ask'});self.assertEqual(cfg['mcp'],{});self.assertEqual(cfg['plugin'],[])

    def test_worker_preflight_price_or_policy_change_no_model_call(self):
        jid=self.request()['jobs'][0]['id']
        with patch.object(ReviewStore,'catalogue',return_value={'stale':False,'models':[]}),patch('external_review_worker.execute_cli',side_effect=AssertionError('Must not call')):
            main(str(self.store.root),jid)
        self.assertEqual(self.store.status(str(self.project),self.task)['jobs'][0]['state'],'failed')

    def test_parallel_workers_obey_concurrency(self):
        p=json.loads(canonical(self.policy));p['concurrency']=2;self.store.configure(str(self.project),self.task,p,1)
        jobs=[self.request('parallel-review-'+str(i),repeat=True)['jobs'][0]['id'] for i in range(4)]
        guard=threading.Lock();running=0;peak=0
        def execute(*args):
            nonlocal running,peak
            with guard:running+=1;peak=max(peak,running)
            time.sleep(.15)
            with guard:running-=1
            return {'summary':'Examined supplied division context.','findings':[]}
        with patch.object(ReviewStore,'catalogue',return_value={'stale':False,'models':[{'id':MODEL}]}),patch('external_review_worker.execute_cli',side_effect=execute):
            threads=[threading.Thread(target=main,args=(str(self.store.root),j)) for j in jobs]
            for t in threads:t.start()
            for t in threads:t.join(10)
        self.assertEqual(peak,2);self.assertTrue(all(j['state']=='completed' for j in self.store.status(str(self.project),self.task)['jobs']))


class ReviewCoordinatorTests(WorkTests):
    def setUp(self):
        super().setUp()
        patcher=patch.dict(os.environ,{'BRIDGE_REVIEWS_DIR':str(self.root/'reviews'),'BRIDGE_COMPANION_DIR':str(self.root/'companion')})
        patcher.start();self.addCleanup(patcher.stop)

    def test_off_dispatch_text_identical(self):
        r=self.start();self.assertNotIn('EXTERNAL REVIEWERS',r['send']['text'])
        self.assertNotIn('external_reviews',self.call('status')['work'])

    def test_inherit_required_instructions_and_gate(self):
        p=json.loads(canonical(DEFAULT));p.update(enabled=True,mode='required',stages=['code'])
        self.call('reviews_configure',task_id='',settings=p,version=0)
        r=self.start();self.assertIn('EXTERNAL REVIEWERS: REQUIRED',r['send']['text'])
        self.checkpoint(r)
        self.call('loop_result',id=r['send']['id'],state='observed_sent')
        ended=self.finish(r);self.assertNotEqual(ended['loop']['state'],'completed');self.assertIn('Required external review',ended['send']['text'])

    def test_active_reviews_wait_without_spending_continuation(self):
        p=json.loads(canonical(DEFAULT));p.update(enabled=True,mode='automatic')
        self.call('reviews_configure',task_id='',settings=p,version=0)
        r=self.start();self.checkpoint(r)
        store=ReviewStore();task=r['loop']['work_task_id'];project=r['loop']['work_project']
        with store.connection() as db:
            db.execute('INSERT INTO catalogue VALUES(1,?,?)',(time.time(),canonical({'models':[{'id':MODEL,'inputs':['text']}],'updated':time.time(),'stale':False})))
        store.request(project,task,'waiting-review-001','question','Inspect the reported result.','A substantial user-facing change.',launch=False)
        self.call('loop_result',id=r['send']['id'],state='observed_sent')
        ended=self.finish(r);self.assertNotIn('send',ended);self.assertEqual(ended['loop']['used'],0);self.assertIn('Waiting for external',ended['loop']['reason'])

if __name__=='__main__':unittest.main()
