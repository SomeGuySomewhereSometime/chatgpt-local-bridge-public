"""MCP wire acceptance using only our disposable GTK fixture."""
import asyncio
import json
import os
from pathlib import Path
import subprocess

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    root = Path(__file__).resolve().parent
    from security import minimal_environment
    env = minimal_environment(desktop=True)
    env['BRIDGE_WORKSPACE'] = '/home/bridge-user'
    fixture = subprocess.Popen(['/usr/bin/python3', '-B', str(root/'desktop_test_window.py')],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    try:
        await asyncio.sleep(1)
        async with stdio_client(StdioServerParameters(
            command=str(Path(__file__).resolve().parent / '.venv/bin/python'), args=['-B', str(root/'mcp_server.py')], env=env)) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), 10)
                tools = await asyncio.wait_for(session.list_tools(), 10)
                names = [t.name for t in tools.tools]
                assert {'desktop_observe','desktop_act','desktop_query','command_session'} <= set(names), names
                print('MCP_CATALOG', len(names), [n for n in names if n.startswith('desktop_')], flush=True)
                async def call(name, arguments):
                    r = await asyncio.wait_for(session.call_tool(name, arguments), 20)
                    assert not r.is_error, r
                    return r.structured_content or json.loads(r.content[0].text)
                job = await call('run_command', {'command':"printf 'ready'; sleep .3; printf 'done'",
                                                 'background':True,'timeout':5})
                last = await call('command_session', {'session_id':job['session_id'],
                    'stdout_offset':job['stdout_offset'],'stderr_offset':job['stderr_offset'],'wait_ms':2000})
                assert job['stdout'] + last['stdout'] == 'readydone', (job,last)
                assert not last['running'] and last['returncode'] == 0, last
                print('MCP_WIRE_COMMAND_SESSION_PASS', flush=True)
                status = await call('desktop_status', {})
                assert status['input']['state'] == 'closed'
                r = await call('desktop_observe', {'application':'bridge-desktop-test','max_elements':1})
                assert len(r['windows']) == 1
                queried = await call('desktop_query', {'snapshot_id':r['snapshot_id'], 'locator':{'name':'Test name'}})
                assert queried['snapshot_id'] == r['snapshot_id']
                entry = next(e for e in queried['elements'] if e['name'] == 'Test name')
                result = await call('desktop_act', {'snapshot_id':r['snapshot_id'], 'actions':[
                    {'kind':'set_text','locator':{'name':'Test name','window':'Bridge Desktop Test'},'text':'MCP verificado: ação'}]})
                assert result['ok'] and result['results'][0]['verified'], result
                print('MCP_WIRE_UNICODE_READBACK_PASS', flush=True)
                stale = await asyncio.wait_for(session.call_tool('desktop_act', {
                    'snapshot_id':r['snapshot_id'],'actions':[{'kind':'set_text','element':entry['id'],'text':'must not execute'}]}),10)
                assert stale.is_error and 'Stale snapshot' in str(stale.content), stale
                print('MCP_WIRE_STALE_ERROR_PASS', flush=True)
                r = result['observation']
                checked = await call('desktop_act', {'snapshot_id':r['snapshot_id'], 'actions':[
                    {'kind':'activate','locator':{'name':'Test checkbox'}}]})
                assert checked['ok'] and checked['results'][0]['verified'], checked
                print('MCP_WIRE_LOCATOR_TOGGLE_VERIFIED', flush=True)
    finally:
        fixture.terminate()
        fixture.wait(timeout=5)


if __name__ == '__main__':
    asyncio.run(main())
