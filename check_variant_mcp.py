"""Verify the relocated start.sh launcher with disposable MCP state only."""
import asyncio
import json
import os
from pathlib import Path
import shlex
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    source = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix='bridge-variant-launcher-') as temporary:
        root = Path(temporary)
        for name in ('project', 'control', 'private'):
            (root / name).mkdir()
        config = {'filesystem': {'denied_paths': [str(root / 'private')],
                                 'read_only_paths': [str(root / 'control')]}}
        cfg = root / 'control/config.json'; cfg.write_text(json.dumps(config))
        env = dict(os.environ, BRIDGE_WORKSPACE=str(root), BRIDGE_CONFIG=str(cfg),
                   BRIDGE_OPERATIONS_DIR=str(root / 'private/operations'), BRIDGE_MEMORY_DISABLED='1',
                   SSH_AUTH_SOCK='/synthetic/variant-agent.sock', CONTROL_PLANE_API_KEY='synthetic-secret')
        async with stdio_client(StdioServerParameters(command=str(source / 'start.sh'), env=env)) as (read, write):
            async with ClientSession(read, write) as session:
                init = await asyncio.wait_for(session.initialize(), 15)
                assert 'NOBUBBLEWARP' in init.server_info.name
                context = await asyncio.wait_for(session.call_tool('get_session_context', {'cwd': 'project'}), 15)
                assert not context.is_error
                data = context.structured_content or json.loads(context.content[0].text)
                assert data['workspace'] == str(root) and data['execution_mode'] == 'host'
                assert data['host_ipc_isolated'] is False and data['sandboxed'] is False
                code = ('import os,json; print(json.dumps({'
                        '"agent":os.environ.get("SSH_AUTH_SOCK"),'
                        '"secret_forwarded":"CONTROL_PLANE_API_KEY" in os.environ}))')
                result = await asyncio.wait_for(session.call_tool('run_command', {
                    'cwd': 'project', 'command': '/usr/bin/python3 -c ' + shlex.quote(code),
                    'request_id': 'relocated-launcher-once', 'timeout': 5}), 15)
                assert not result.is_error
                data = result.structured_content or json.loads(result.content[0].text)
                assert data['returncode'] == 0 and data['sandboxed'] is False
                environment = json.loads(data['stdout'])
                assert environment == {'agent': '/synthetic/variant-agent.sock', 'secret_forwarded': False}
        print('PASS: relocated start.sh, independent MCP identity/config/state, host command and selective environment')


if __name__ == '__main__':
    asyncio.run(main())
