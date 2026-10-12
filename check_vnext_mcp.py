"""Actual stdio reconnection while a job runs, scoped Code Mode and deduplication."""
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
    with tempfile.TemporaryDirectory(prefix='bridge-vnext-wire-') as tmp:
        work=Path(tmp)
        for name in ('project','private','control'):(work/name).mkdir()
        cfg=work/'control/config.json'
        cfg.write_text(json.dumps({'filesystem':{'denied_paths':[str(work/'private')],
            'read_only_paths':[str(work/'control')]},'memory':{'enabled':False}}))
        env=dict(os.environ,BRIDGE_WORKSPACE=tmp,BRIDGE_CONFIG=str(cfg),BRIDGE_MEMORY_DISABLED='1',
                 BRIDGE_OPERATIONS_DIR=str(work/'private/operations'),PYTHONDONTWRITEBYTECODE='1')
        @asynccontextmanager
        async def connection(profile='core'):
            async with stdio_client(StdioServerParameters(command=str(root/'.venv/bin/python'),
                args=['-B',str(root/'mcp_server.py')],env=dict(env,BRIDGE_PROFILE=profile))) as (read,write):
                async with ClientSession(read,write) as client:
                    await client.initialize();yield client
        async def call(client,name,args):
            r=await asyncio.wait_for(client.call_tool(name,args),30)
            assert not r.is_error,(name,r)
            return r.structured_content or json.loads(r.content[0].text)
        script="const r=tools.write_file({path:'project/a.txt',content:'saved',expected_version:'missing'}); text(r); text(tools.read_file({path:'project/a.txt'}));"
        async with connection() as client:
            r=await call(client,'code_mode',{'code':script,'request_id':'script'})
            assert r['ok'] and len(r['calls'])==2,r
            r=await call(client,'run_command',{'cwd':'project','command':'printf x >> once; printf begin; sleep 4; printf end',
                'request_id':'command','background':True,'timeout':12})
            assert r['running'],r
            sid=r['session_id']
        # Closing the actual stdio connection above exits the first MCP process.
        async with connection() as client:
            r=await call(client,'code_mode',{'code':script,'request_id':'script'})
            assert r['replayed'],r
            r=await call(client,'command_request',{'cwd':'project','request_id':'command'})
            assert r['output_available'],r
            while r['running']:
                r=await call(client,'command_session',{'session_id':sid,'wait_ms':1000})
            assert r['stdout']=='beginend' and r['returncode']==0,r
            assert (work/'project/once').read_text()=='x'
            denied=await client.call_tool('desktop_status',{})
            assert denied.is_error
        async with connection('desktop') as client:
            names={t.name for t in (await client.list_tools()).tools}
            assert 'desktop_status' in names and 'code_mode' not in names and 'run_command' not in names
            denied=await client.call_tool('run_command',{'command':'true'})
            assert denied.is_error
        print('PASS: real MCP profiles, Code Mode write/read/replay and live-job reconnection without duplicate execution')


if __name__=='__main__':asyncio.run(main())
