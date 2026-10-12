"""Two-lifetime acceptance, with disposable config, memory, jobs and project files."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    root=Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix='bridge-continuity-wire-') as temporary:
        work=Path(temporary)
        for name in ('project','other','private','control'):(work/name).mkdir()
        config={'filesystem':{'denied_paths':[str(work/'private')],'read_only_paths':[str(work/'control')]},
                'memory':{'enabled':True,'path':str(work/'private/memory/memory.sqlite3')}}
        cfg=work/'control/config.json';cfg.write_text(json.dumps(config))
        env=dict(os.environ,BRIDGE_WORKSPACE=temporary,BRIDGE_CONFIG=str(cfg),BRIDGE_MEMORY_DISABLED='0',
                 BRIDGE_OPERATIONS_DIR=str(work/'private/operations'),PYTHONDONTWRITEBYTECODE='1')
        @asynccontextmanager
        async def server():
            async with stdio_client(StdioServerParameters(command=str(root/'.venv/bin/python'),args=['-B',str(root/'mcp_server.py')],env=env)) as (read,write):
                async with ClientSession(read,write) as session:
                    await asyncio.wait_for(session.initialize(),15)
                    yield session
        async def call(session,name,args):
            result=await asyncio.wait_for(session.call_tool(name,args),20)
            assert not result.is_error,(name,result)
            return result.structured_content or json.loads(result.content[0].text)
        async with server() as session:
            names={t.name for t in (await session.list_tools()).tools}
            assert {'task_begin','task_checkpoint','task_context'}<=names
            args={'cwd':'project','title':'Teste de continuidade','goal':'Executar uma vez e recuperar o resultado',
                  'source':'user: acceptance','request_id':'begin','constraints':['Não repetir comandos']}
            task=await call(session,'task_begin',args)
            assert (await call(session,'task_begin',args))['task_id']==task['task_id']
            checkpoint={'cwd':'project','task_id':task['task_id'],'expected_revision':0,
                        'checkpoint':{'pending':[{'description':'Verificação lenta','request_id':'once','cwd':str(work/'project')}],
                                      'next_step':'Consultar a sessão, depois validar o ficheiro count'},
                        'source':'agent: before dispatch','request_id':'checkpoint-before'}
            await call(session,'task_checkpoint',checkpoint)
            command={'cwd':'project','command':"printf x >> count; sleep .4; printf done",'timeout':5,
                     'yield_after':0,'request_id':'once','task_id':task['task_id']}
            job=await call(session,'run_command',command)
            assert job['session_id'] and job['promoted_to_background'] and job['running']
            replay=await call(session,'run_command',command)
            assert replay['replayed'] and replay['session_id']==job['session_id']
            done=await call(session,'command_session',{'session_id':job['session_id'],'wait_ms':3000})
            assert not done['running'] and done['execution_state']=='completed' and done['stdout']=='done', done
            assert (work/'project/count').read_text()=='x'
            checkpoint.update(expected_revision=1,request_id='checkpoint-after',state='completed',
                              checkpoint={'completed':['Ficheiro alterado uma vez'],
                                          'validation':[{'description':'count contém x','status':'passed','evidence':'count'}]})
            await call(session,'task_checkpoint',checkpoint)
            context=await call(session,'task_context',{'cwd':'project','task_id':task['task_id']})
            assert context['operations'][0]['state']=='completed' and not context['automatic_retry']
            assert 'stdout' not in json.dumps(context)
            denied=await session.call_tool('task_context',{'cwd':'other','task_id':task['task_id']})
            assert denied.is_error
            print('PASS: real MCP catalogue, slow foreground promotion, incremental completion, idempotency and project isolation',flush=True)
        async with server() as session:
            context=await call(session,'get_session_context',{'cwd':'project','task_id':task['task_id']})
            assert context['continuity']['task']['revision']==2
            old=await call(session,'task_context',{'cwd':'project','task_id':task['task_id'],'revision':1})
            assert old['historical'] and old['checkpoint']['progress']['pending']
            exported=await call(session,'task_context',{'cwd':'project','task_id':task['task_id'],'as_markdown':True})
            assert 'Registo histórico' in exported['markdown'] and 'count contém x' in exported['markdown']
            receipt=await call(session,'command_request',{'cwd':'project','request_id':'once'})
            assert receipt['state']=='completed' and receipt['output_available']
            assert (work/'project/count').read_text()=='x'
            print('PASS: new server recovered task revisions, export and durable receipt; no action replayed',flush=True)


if __name__=='__main__':asyncio.run(main())
