"""Real MCP acceptance in disposable state; ChatGPT/page observations are simulated."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import tempfile
import uuid
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from coordinator import Coordinator
from work_context import WorkContext


async def main():
    bridge=Path(os.environ.get('BRIDGE_TEST_ROOT',Path(__file__).resolve().parent.parent))
    with tempfile.TemporaryDirectory(prefix='bridge-update-mcp-') as tmp:
        root=Path(tmp);project=root/'project';project.mkdir();config={'filesystem':{'denied_paths':[str(root/'private')],'read_only_paths':[str(root/'config.json')]},'memory':{'enabled':True,'path':str(root/'private/memory.sqlite3')},'operations':{'directory':str(root/'private/operations')}}
        cfg=root/'config.json';cfg.write_text(json.dumps(config))
        env=dict(os.environ,BRIDGE_CONFIG=str(cfg),BRIDGE_WORKSPACE=str(root),BRIDGE_COMPANION_DIR=str(root/'companion'),BRIDGE_OPERATIONS_DIR=str(root/'private/operations'),BRIDGE_MEMORY_DISABLED='0',PYTHONDONTWRITEBYTECODE='1',BRIDGE_PROFILE='core')
        control=Coordinator(root/'companion',work=WorkContext(config));users=0
        base={'url':'https://chatgpt.com/c/tool-update-acceptance','client':'fixture-client','tab':1}
        call=lambda action,**p:control.dispatch({**base,'action':action,**p})
        call('bind',project=str(project),task='');call('observe',state='idle')
        snap={'state':'idle','terminal':True,'document_id':'document-update-test-1234','user_count':0}
        def start():
            nonlocal users
            run=call('loop_start',goal='Verify task-scoped updates without touching any real project',criteria='The fixture result is preserved',work_request_id=uuid.uuid4().hex,limit=1,snapshot={**snap,'user_count':users})
            users+=1;call('loop_result',id=run['send']['id'],state='observed_sent');return run
        def add(run,mode='automatic'):
            uid=uuid.uuid4().hex;call('update_add',task_id=run['loop']['work_task_id'],id=uid,text='Preserve the original fixture result and report this instruction.',mode=mode);return uid
        def finish(run):
            return call('loop_tick',snapshot={**snap,'state':'completed','user_count':users,'marker':{'nonce':run['loop']['nonce'],'decision':'DONE'}})
        async def tool(session,name,args,allow_error=False):
            result=await asyncio.wait_for(session.call_tool(name,args),30)
            assert allow_error or not result.is_error,(name,result)
            if result.structured_content is not None:return result.structured_content
            return json.loads(result.content[0].text)
        async def checkpoint(session,run,revision,offered=None,receipt=None):
            marker=offered['workspace_updates']['updates'][0]['acknowledgement'] if offered else ''
            cp={'summary':'Fixture preserved; update handled when supplied.','completed':[marker+' Fixture instruction handled without replay.'], 'validation':[{'description':'The fixture result is preserved','status':'passed','evidence':'[receipt:'+receipt+'] expected printf output preserved' if receipt else 'Disposable acceptance fixture inspected; no project mutation.'}]}
            return await tool(session,'task_checkpoint',{'cwd':str(project),'task_id':run['loop']['work_task_id'],'expected_revision':revision,'checkpoint':cp,'state':'completed','source':'MCP acceptance fixture','request_id':uuid.uuid4().hex})
        params=StdioServerParameters(command=str(bridge/'.venv/bin/python'),args=['-B',str(bridge/'mcp_server.py')],env=env)
        try:
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write) as session:
                    await asyncio.wait_for(session.initialize(),20)
                    catalog=await session.list_tools();assert len(catalog.tools)==39,len(catalog.tools)
                    # Direct task context offers an update; a successful command is
                    # preserved while acknowledgement is outstanding.
                    run=start();add(run);tid=run['loop']['work_task_id']
                    context=await tool(session,'get_session_context',{'cwd':str(project),'task_id':tid})
                    offer=context['continuity'];assert offer['workspace_updates']['status']=='offered_via_bridge'
                    assert context['workspace_updates']==offer['workspace_updates']
                    # Simulate a caller retaining only selected fields of the first
                    # tool result. Retrieve the durable instruction without replay.
                    recovered=await tool(session,'task_context',{'cwd':str(project),'task_id':tid})
                    assert recovered['workspace_updates']['retrieved_existing_delivery']
                    assert recovered['workspace_updates']['updates']==offer['workspace_updates']['updates']
                    offer=recovered
                    assert control.db.execute('SELECT count(*) FROM update_deliveries').fetchone()[0]==1
                    rid=uuid.uuid4().hex
                    executed=await tool(session,'run_command',{'cwd':str(project),'project_root':str(project),'task_id':tid,'request_id':rid,'command':'printf bridge-contact-ok','timeout':10})
                    receipt=await tool(session,'command_request',{'cwd':str(project),'request_id':rid})
                    assert receipt['state']=='completed' and receipt['returncode']==0,receipt
                    await checkpoint(session,run,0,offer,rid)
                    ended=finish(run);assert ended['loop']['state']=='completed' and ended['loop']['used']==0,ended
                    assert len(call('status')['requests'])==1
                    assert call('status')['work']['updates'][0]['confirmed_by']=='bridge_checkpoint'
                    print('PASS real MCP: direct tool delivery + checkpoint acknowledgement + DONE without extra chat message')
                    # Explicit next-response delivery remains unchanged.
                    run=start();add(run,'next_reply');tid=run['loop']['work_task_id']
                    context=await tool(session,'task_context',{'cwd':str(project),'task_id':tid});assert 'workspace_updates' not in context
                    await checkpoint(session,run,0);following=finish(run);assert 'send' in following
                    users+=1;call('loop_result',id=following['send']['id'],state='observed_sent')
                    await checkpoint(session,run,1);assert finish(following)['loop']['state']=='completed'
                    assert call('status')['work']['updates'][0]['delivery_channel']=='chat'
                    print('PASS real MCP: Next response uses existing bounded chat continuation')
                    # Code Mode forwards the instruction even when JS emits only
                    # selected text and prevents the next mutation in that batch.
                    run=start();add(run);tid=run['loop']['work_task_id']
                    script='const r=tools.task_context('+json.dumps({'cwd':str(project),'task_id':tid})+'); text("selected output only"); tools.run_command('+json.dumps({'cwd':str(project),'project_root':str(project),'task_id':tid,'command':'touch must-not-exist','timeout':10})+');'
                    batch=await tool(session,'code_mode',{'code':script,'request_id':uuid.uuid4().hex},True)
                    assert batch['paused_for_updates'] and batch['workspace_updates']['status']=='offered_via_bridge',batch
                    assert len(batch['calls'])==1 and batch['calls'][0]['state']=='returned',batch
                    assert not (project/'must-not-exist').exists()
                    await checkpoint(session,run,0,batch);assert finish(run)['loop']['state']=='completed'
                    print('PASS real MCP: Code Mode forwards hidden update and pauses before next tool mutation')
                    # A uniquely linked retained command receipt can deliver later
                    # updates without re-executing that command.
                    run=start();tid=run['loop']['work_task_id'];rid=uuid.uuid4().hex
                    executed=await tool(session,'run_command',{'cwd':str(project),'project_root':str(project),'task_id':tid,'request_id':rid,'command':'printf retained-receipt-ok','timeout':10})
                    await tool(session,'command_request',{'cwd':str(project),'request_id':rid});add(run)
                    offer=await tool(session,'command_request',{'cwd':str(project),'request_id':rid})
                    assert offer['workspace_updates']['operation']=='command_request'
                    await checkpoint(session,run,0,offer,rid);assert finish(run)['loop']['state']=='completed'
                    assert call('status')['work']['receipt_count']==1
                    print('PASS real MCP: command receipt inference, no command replay')
                    # Background/session output reads use the unique receipt link.
                    run=start();tid=run['loop']['work_task_id'];rid=uuid.uuid4().hex
                    executed=await tool(session,'run_command',{'cwd':str(project),'project_root':str(project),'task_id':tid,'request_id':rid,'command':'printf session-output-ok','background':True,'timeout':10})
                    assert executed.get('session_id'),executed
                    add(run)
                    offer=await tool(session,'command_session',{'session_id':executed['session_id'],'wait_ms':1000})
                    assert offer['workspace_updates']['operation']=='command_session_read',offer
                    await tool(session,'command_request',{'cwd':str(project),'request_id':rid})
                    await checkpoint(session,run,0,offer,rid);assert finish(run)['loop']['state']=='completed'
                    print('PASS real MCP: managed command-session result delivers the task update')
                    # Lost/ignored delivery is never converted to a new chat send.
                    run=start();add(run);tid=run['loop']['work_task_id']
                    offered=await tool(session,'task_context',{'cwd':str(project),'task_id':tid})
                    await checkpoint(session,run,0)
                    ended=finish(run);assert ended['loop']['state']=='review_required' and 'send' not in ended
                    print('PASS real MCP: missing acknowledgement stops instead of duplicating via chat')
        finally:control.db.close()

if __name__=='__main__':asyncio.run(main())
