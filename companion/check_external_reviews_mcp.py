"""Real MCP transport with disposable policy, jobs and checkpoints. No paid/model calls."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
import uuid

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from external_reviews import ReviewStore, DEFAULT, canonical


async def main():
    root=Path(__file__).resolve().parent.parent
    python=Path(os.environ.get('BRIDGE_TEST_PYTHON',str(root/'.venv/bin/python')))
    with tempfile.TemporaryDirectory(prefix='external-review-mcp-') as temp:
        base=Path(temp);project=base/'project';project.mkdir();(project/'code.py').write_text('result = 1\n')
        config={'filesystem':{'denied_paths':[str(base/'private')],'read_only_paths':[str(base/'control')]},'memory':{'enabled':True,'path':str(base/'private/memory.sqlite3')},'operations':{'directory':str(base/'private/operations')}}
        cfg=base/'config.json';cfg.write_text(json.dumps(config))
        env=dict(os.environ,BRIDGE_CONFIG=str(cfg),BRIDGE_WORKSPACE=str(base),BRIDGE_OPERATIONS_DIR=str(base/'private/operations'),BRIDGE_REVIEWS_DIR=str(base/'reviews'),BRIDGE_COMPANION_DIR=str(base/'companion'),BRIDGE_VALIDATION_DIR=str(base/'validations'))
        params=StdioServerParameters(command=str(python),args=['-B',str(root/'mcp_server.py')],env=env)
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await asyncio.wait_for(session.initialize(),20)
                names={x.name for x in (await session.list_tools()).tools}
                assert {'external_review_catalog','external_review_request','external_review_status','external_review_decision','external_review_cancel'}<=names
                assert 'desktop_act' not in names
                async def call(name,arguments,error=False):
                    r=await asyncio.wait_for(session.call_tool(name,arguments),20);assert bool(r.is_error)==error,(name,r)
                    if error:return
                    return r.structured_content or json.loads(r.content[0].text)
                task=await call('task_begin',{'cwd':str(project),'title':'Fixture','goal':'Check fixture','source':'Acceptance fixture','request_id':uuid.uuid4().hex})
                tid=task['task_id']
                await call('external_review_request',{'cwd':str(project),'task_id':tid,'request_id':'off-test-review','purpose':'code','question':'Check code','reason':'Concrete code issue.'},True)
                store=ReviewStore(base/'reviews',config);policy=json.loads(canonical(DEFAULT));policy.update(enabled=True,mode='required',stages=['code'])
                store.configure(str(project),tid,policy,0)
                with store.connection() as db:db.execute('INSERT INTO catalogue VALUES(1,?,?)',(time.time(),canonical({'models':[{'id':'opencode/mimo-v2.6-flash-free','inputs':['text']}],'updated':time.time(),'stale':False})))
                checkpoint={'cwd':str(project),'task_id':tid,'expected_revision':0,'state':'completed','source':'Fixture','request_id':'fixture-checkpoint',
                    'checkpoint':{'summary':'Fixture inspected','completed':['Selected result inspected'], 'validation':[{'description':'Fixture check','status':'passed','evidence':'Disposable fixture inspected.'}]}}
                await call('task_checkpoint',checkpoint,True)
                # Deliberately fixture-provider data; the transport must not treat a claim alone as a real review.
                job=store.request(str(project),tid,'fixture-review-001','code','Verify supplied result.','Concrete verification of a fixture.',files=[{'path':'code.py'}],launch=False)
                jid=job['jobs'][0]['id']
                status=await call('external_review_status',{'cwd':str(project),'task_id':tid,'job_id':jid})
                assert status['jobs'][0]['state']=='queued'
                with store.connection() as db:db.execute("UPDATE jobs SET state='completed',result=? WHERE id=?",(canonical({'summary':'No issue found in this fixture.','findings':[]}),jid))
                await call('external_review_decision',{'cwd':str(project),'task_id':tid,'job_id':jid,'summary':'Evaluated the fixture result and its supplied context. No unresolved finding.','findings':[]})
                saved=await call('task_checkpoint',checkpoint)
                assert saved['revision']==1 and saved['external_reviews']['missing']==[]
                context=await call('task_context',{'cwd':str(project),'task_id':tid});assert 'EXTERNAL REVIEWERS' in context['external_review_instructions']
                (project/'code.py').write_text('result = 2\n')
                checkpoint.update(expected_revision=1,request_id='changed-fixture-checkpoint')
                await call('task_checkpoint',checkpoint,True)
                # A second isolated task validates worker capture and source freshness.
                check_task=await call('task_begin',{'cwd':str(project),'title':'Versioned checks','goal':'Validate versions','source':'Acceptance fixture','request_id':uuid.uuid4().hex})
                vid_task=check_task['task_id'];rid='versioned-command-0001'
                result=await call('run_command',{'cwd':str(project),'project_root':str(project),'task_id':vid_task,'request_id':rid,'command':'python3 -c "assert 1 + 1 == 2"','validation_paths':['code.py']})
                while result['running']:
                    result=await call('command_session',{'session_id':result['session_id'],'wait_ms':1000})
                assert result['returncode']==0 and not result.get('validation_warning'),result
                ref=result['validation_reference'];vid=ref[len('[validation:'):-1]
                proof=await call('validation_evidence',{'cwd':str(project),'task_id':vid_task,'action':'status','validation_id':vid})
                assert proof['freshness']=='current' and proof['receipt_confirmed'],proof
                fresh_cp={'cwd':str(project),'task_id':vid_task,'expected_revision':0,'state':'completed','source':'Fixture','request_id':'versioned-checkpoint-001','checkpoint':{'summary':'Versioned command checked','validation':[{'description':'Command passed','status':'passed','evidence':ref+' [receipt:'+rid+']'}]}}
                saved=await call('task_checkpoint',fresh_cp);assert saved['revision']==1
                (project/'code.py').write_text('result = 3\n')
                proof=await call('validation_evidence',{'cwd':str(project),'task_id':vid_task,'action':'status','validation_id':vid});assert proof['freshness']=='stale'
                fresh_cp.update(expected_revision=1,request_id='versioned-checkpoint-002')
                await call('task_checkpoint',fresh_cp,True)
                # Manual/browser captures do not invent a command receipt.
                started=await call('validation_evidence',{'cwd':str(project),'task_id':vid_task,'action':'begin','request_id':'browser-version-check-001','paths':['code.py']})
                finished=await call('validation_evidence',{'cwd':str(project),'task_id':vid_task,'action':'finish','validation_id':started['id'],'description':'Reported visual inspection','status':'passed'})
                assert finished['outcome_basis']=='assistant_report' and not finished['receipt_confirmed']
                print('PASS real MCP worker: command before/after snapshots, current proof, stale checkpoint refusal and explicit manual outcome.',flush=True)
                print('PASS real Core MCP: reviewer catalogue, Off rejection, Required gate, durable status, decisions, attachment and stale-file refusal.',flush=True)
        env['BRIDGE_PROFILE']='desktop'
        params.env=env
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize();names={x.name for x in (await session.list_tools()).tools}
                assert not any(name.startswith('external_review_') for name in names)
                print('PASS Desktop catalogue unchanged: no external reviewer tools.',flush=True)


if __name__=='__main__':asyncio.run(main())
