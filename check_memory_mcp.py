"""Disposable stdio MCP acceptance: two server lifetimes, real shell, no production DB."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    root = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix='bridge-memory-wire-') as tmp:
        workspace = Path(tmp)
        for name in ('project', 'other', 'private', 'control'):
            (workspace / name).mkdir()
        config = {'filesystem': {'denied_paths': [str(workspace / 'private')],
                                 'read_only_paths': [str(workspace / 'control')]},
                  'execution': {'mode': 'host'},
                  'memory': {'enabled': True, 'path': str(workspace / 'private' / 'memory' / 'memory.db')}}
        config_path = workspace / 'control' / 'config.json'
        config_path.write_text(json.dumps(config))
        env = dict(os.environ, BRIDGE_WORKSPACE=tmp, BRIDGE_CONFIG=str(config_path), BRIDGE_MEMORY_DISABLED='0', BRIDGE_OPERATIONS_DIR=str(workspace / 'private' / 'operations'), PYTHONDONTWRITEBYTECODE='1')

        @asynccontextmanager
        async def server():
            async with stdio_client(StdioServerParameters(command=str(root / '.venv/bin/python'),
                    args=['-B', str(root / 'mcp_server.py')], env=env)) as (read, write):
                async with ClientSession(read, write) as session:
                    await asyncio.wait_for(session.initialize(), 15)
                    yield session

        async def call(session, name, args):
            result = await asyncio.wait_for(session.call_tool(name, args), 20)
            assert not result.is_error, (name, result)
            return result.structured_content or json.loads(result.content[0].text)

        async with server() as session:
            names = {t.name for t in (await session.list_tools()).tools}
            assert {'memory_save', 'memory_get', 'memory_search', 'memory_delete'} <= names
            args = dict(cwd='project', title='Retomar verificação', body='Concluído: configuração. Próximo passo: validar persistência.',
                        source='agent: isolated MCP acceptance', kind='checkpoint', request_id='checkpoint-1')
            first = (await call(session, 'memory_save', args))['result']
            replay = (await call(session, 'memory_save', args))['result']
            assert first['id'] == replay['id'] and replay['replayed']
            sync = await call(session, 'run_command', dict(cwd='project', command="printf 'sync-ok'", timeout=5))
            assert sync['stdout'] == 'sync-ok' and sync['history']['status'] == 'recorded'
            background = await call(session, 'run_command', dict(cwd='project', command="printf 'async-ok'", timeout=5, background=True))
            done = await call(session, 'command_session', dict(session_id=background['session_id'], wait_ms=2000))
            assert done['stdout'] == 'async-ok' and not done['running'] and done['history']['status'] == 'recorded'
            denied = await session.call_tool('read_file', dict(path=str(workspace / 'private' / 'memory' / 'memory.db')))
            assert denied.is_error
            sandbox = await call(session, 'run_command', dict(cwd='project', command=f'test -e {workspace / "private" / "memory" / "memory.db"}', timeout=5))
            assert sandbox['returncode'] == 0
            print('PASS: catalogue, idempotency, synchronous/background command history, file-tool policy and explicit host access', flush=True)

        async with server() as session:
            context = await call(session, 'get_session_context', {'cwd': 'project'})
            assert context['memory']['checkpoint'][0]['id'] == first['id']
            assert 'Próximo passo' in context['memory']['checkpoint'][0]['body']
            other = await call(session, 'memory_search', {'cwd': 'other'})
            assert other['result'] == []
            foreign = await session.call_tool('memory_get', {'cwd': 'other', 'entry_id': first['id']})
            assert foreign.is_error
            history = (await call(session, 'memory_search', {'cwd': 'project', 'kind': 'command'}))['result']
            assert len(history) == 3 and all(r['state'] == 'completed' and r['returncode'] == 0 for r in history)
            assert all(not r['body'] for r in history)
            await call(session, 'memory_delete', dict(cwd='project', entry_id=first['id'], expected_version=first['version']))
            after = (await call(session, 'memory_search', {'cwd': 'project', 'query': 'persistência'}))['result']
            assert after == []
            print('PASS: fresh MCP process recovered checkpoint and command history; cross-project lookup denied; deletion verified', flush=True)


if __name__ == '__main__':
    asyncio.run(main())
