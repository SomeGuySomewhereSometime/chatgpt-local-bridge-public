"""Exercise project environments, atomic writes, receipts and leases through real MCP."""
import asyncio
import json
import os
from pathlib import Path
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def call(session, name, parameters, *, error=False):
    result = await asyncio.wait_for(session.call_tool(name, parameters), 110)
    assert result.is_error == error, (name, result.content)
    if error:
        return result
    return result.structured_content or json.loads(result.content[0].text)


async def main():
    source = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix='bridge-improvements-mcp-') as temporary:
        root = Path(temporary)
        for name in ('project', 'other', 'private', 'control'):
            (root / name).mkdir()
        (root / 'project/subdir').mkdir()
        config = root / 'control/config.json'
        config.write_text(json.dumps({'filesystem': {'denied_paths': [str(root / 'private')],
            'read_only_paths': [str(root / 'control')]}}))
        env = dict(os.environ, BRIDGE_WORKSPACE=str(root), BRIDGE_CONFIG=str(config),
                   BRIDGE_OPERATIONS_DIR=str(root / 'private/operations'), BRIDGE_MEMORY_DISABLED='1')
        parameters = StdioServerParameters(command=str(source / 'start.sh'), env=env)
        write_args = {'path': 'project/file.txt', 'content': 'first',
                      'expected_version': 'missing', 'request_id': 'write-once'}
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as first:
                await first.initialize()
                catalogue = {tool.name for tool in (await first.list_tools()).tools}
                assert {'operation_request', 'project_environment', 'resource_lease', 'integration_prepare'} <= catalogue
                created = await call(first, 'write_file', write_args)
                readback = await call(first, 'read_file', {'path': 'project/file.txt'})
                assert readback['version'] == created['version']
                await call(first, 'write_file', dict(write_args, content='conflict'), error=True)
                await call(first, 'write_file', {'path': 'project/file.txt', 'content': 'second',
                    'expected_version': readback['version'], 'request_id': 'second-write'})
                await call(first, 'write_file', {'path': 'project/file.txt', 'content': 'stale',
                    'expected_version': readback['version'], 'request_id': 'stale-write'}, error=True)
                patch_file = await call(first, 'write_file', {'path': 'project/patch.txt', 'content': 'before\n',
                    'expected_version': 'missing', 'request_id': 'patch-file'})
                patch = 'diff --git a/patch.txt b/patch.txt\n--- a/patch.txt\n+++ b/patch.txt\n@@ -1 +1 @@\n-before\n+after\n'
                patch_args = {'cwd': 'project', 'patch': patch, 'expected_versions': {'patch.txt': patch_file['version']},
                              'request_id': 'patch-once'}
                applied = await call(first, 'apply_patch', patch_args)
                assert applied['ok'] and (root / 'project/patch.txt').read_text() == 'after\n'
                assert (await call(first, 'apply_patch', patch_args))['replayed']
                await call(first, 'apply_patch', dict(patch_args, request_id='stale-patch'), error=True)
                # A managed environment is an explicit, persistent opt-in; no packages installed.
                prepared = await call(first, 'project_environment', {'cwd': 'project', 'action': 'prepare',
                    'python': True, 'request_id': 'prepare-python'})
                assert prepared['managed_python_ready']
                command = ('python -c \'import json,os,sys; print(json.dumps({"prefix":sys.prefix,'
                           '"project":os.environ["BRIDGE_PROJECT_ROOT"],"cache":os.environ["PIP_CACHE_DIR"]}))\'')
                executed = await call(first, 'run_command', {'cwd': 'project/subdir', 'project_root': 'project',
                    'command': command, 'request_id': 'managed-python', 'timeout': 10})
                assert executed['returncode'] == 0
                data = json.loads(executed['stdout'])
                assert data['prefix'] == prepared['python_environment']
                assert data['project'] == str(root / 'project')
                assert data['cache'].startswith(prepared['cache_directory'])
                background = await call(first, 'run_command', {'cwd': 'project/subdir', 'project_root': 'project',
                    'command': command, 'request_id': 'managed-python-background', 'timeout': 10, 'background': True})
                output = await call(first, 'command_session', {'session_id': background['session_id'], 'wait_ms': 10000})
                assert not output['running'] and output['returncode'] == 0
                assert json.loads(output['stdout'])['prefix'] == prepared['python_environment']
                different = await call(first, 'run_command', {'cwd': 'other', 'command': 'printf "$PIP_CACHE_DIR"',
                    'request_id': 'other-cache', 'timeout': 5})
                assert different['stdout'] != data['cache']
                lease = await call(first, 'resource_lease', {'action': 'acquire', 'resource': 'path:project',
                    'owner_id': 'chat-A'})
                async with stdio_client(parameters) as (other_read, other_write):
                    async with ClientSession(other_read, other_write) as second:
                        await second.initialize()
                        await call(second, 'write_file', {'path': 'project/blocked.txt', 'content': 'blocked',
                            'request_id': 'other-chat-blocked'}, error=True)
                        await call(second, 'run_command', {'cwd': 'project', 'command': 'touch forbidden',
                            'request_id': 'other-shell-blocked'}, error=True)
                        assert not (root / 'project/forbidden').exists()
                        assert not (root / 'project/blocked.txt').exists()
                        await call(second, 'write_file', {'path': 'project/owned.txt', 'content': 'owned',
                            'request_id': 'owned', 'owner_id': 'chat-A', 'lease_token': lease['lease_token']})
                await call(first, 'resource_lease', {'action': 'release', 'resource': 'path:project',
                    'owner_id': 'chat-A', 'lease_token': lease['lease_token']})
        # Restart is the real lost-response path: retry must not overwrite the second edit.
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as restarted:
                await restarted.initialize()
                replay = await call(restarted, 'write_file', write_args)
                assert replay['replayed']
                assert (root / 'project/file.txt').read_text() == 'second'
                receipt = await call(restarted, 'operation_request', {'request_id': 'write-once'})
                assert receipt['state'] == 'completed' and receipt['operation'] == 'write_file'
                status = await call(restarted, 'project_environment', {'cwd': 'project'})
                assert status['managed_python_ready']
        print('PASS: real MCP atomic/versioned writes, durable replay, two-chat leases and persistent Python/project caches')


if __name__ == '__main__':
    asyncio.run(main())
