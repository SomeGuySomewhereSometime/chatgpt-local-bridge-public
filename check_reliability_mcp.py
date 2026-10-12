"""Isolated real MCP/host acceptance; no production state or desktop operations."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import tempfile
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    source=Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix='bridge-reliability-wire-') as tmp:
        root=Path(tmp)
        for name in ('project','private','control'): (root/name).mkdir()
        config={'filesystem':{'denied_paths':[str(root/'private')], 'read_only_paths':[str(root/'control')]}}
        cfg=root/'control/config.json';cfg.write_text(json.dumps(config))
        (root/'project/text.txt').write_text('line\n'*1000)
        env=dict(os.environ, BRIDGE_WORKSPACE=tmp, BRIDGE_CONFIG=str(cfg),
                 BRIDGE_MEMORY_DISABLED='1', BRIDGE_OPERATIONS_DIR=str(root/'private/operations'), PYTHONDONTWRITEBYTECODE='1')
        @asynccontextmanager
        async def server():
            async with stdio_client(StdioServerParameters(command=str(source/'.venv/bin/python'),
                    args=['-B',str(source/'mcp_server.py')],env=env)) as (read,write):
                async with ClientSession(read,write) as session:
                    await asyncio.wait_for(session.initialize(),15)
                    yield session
        async def call(session,name,args):
            result=await asyncio.wait_for(session.call_tool(name,args),20)
            assert not result.is_error, (name,result)
            return result.structured_content or json.loads(result.content[0].text)
        params={'cwd':'project','command':'printf x >> count; printf done','request_id':'wire-once','timeout':5}
        async with server() as session:
            tools={t.name:t for t in (await session.list_tools()).tools}
            assert 'command_request' in tools
            context=await call(session,'get_session_context',{'cwd':'project'})
            assert context['execution_mode']=='host' and context['sandboxed'] is False
            assert context['host_ipc_isolated'] is False and context['shell_network'] is True
            assert 'expected_version' in tools['read_file'].input_schema['properties']
            first=await call(session,'run_command',params)
            assert first['sandboxed'] is False and first['execution_mode']=='host'
            assert first['stdout']=='done', {k:first.get(k) for k in ('returncode','stdout','stderr')}
            replay=await call(session,'run_command',params)
            assert replay['replayed'] and replay['state']=='completed'
            page=await call(session,'read_file',{'path':'project/text.txt','line_count':2})
            assert page['content']=='line\n'*2 and page['has_more']
            next_page=await call(session,'read_file',{'path':'project/text.txt','line_count':2,
                'byte_offset':page['next_byte_offset'],'start_line':page['next_line'],'expected_version':page['version']})
            assert next_page['start_line']==3
            matches=await call(session,'search',{'query':'line','path':'project','limit':2})
            assert matches['count']==2 and matches['truncated']
            denied=await session.call_tool('read_file',{'path':str(root/'private/operations/requests.sqlite3')})
            assert denied.is_error
            protected=await call(session,'run_command',{'cwd':'project','command':f'test -e {root}/private/operations/requests.sqlite3','request_id':'protection','timeout':5})
            assert protected['returncode']==0
            bg=await call(session,'run_command',{'cwd':'project','command':'sleep .2; printf bg','request_id':'background','background':True,'timeout':5})
            await call(session,'command_session',{'session_id':bg['session_id'],'wait_ms':3000})
            state=await call(session,'command_request',{'cwd':'project','request_id':'background'})
            assert state['state']=='completed' and state['stdout']=='bg'
        async with server() as session:
            replay=await call(session,'run_command',params)
            assert replay['replayed'] and replay['output_available']
            assert (root/'project/count').read_text()=='x'
            conflict=await session.call_tool('run_command',dict(params,command='printf duplicate >> count'))
            assert conflict.is_error and 'conflict' in ''.join(c.text for c in conflict.content if c.type=='text')
        print('PASS: real MCP catalogue, once-only command across two server lifetimes, background receipt, file pages, bounded search and explicit host access')


if __name__=='__main__':
    asyncio.run(main())
