"""Bounded Code Mode controller. Child calls retain normal validation and receipts."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from mcp.types import ToolAnnotations
from operation_store import OperationStore

MAX_CALLS = 24
MAX_OUTPUT = 32000
MAX_RESPONSE = 2 * 1024 * 1024
# Long-lived session/goal finalization must remain explicit.
EXCLUDED = {'code_mode', 'task_checkpoint', 'task_begin', 'memory_delete'}
MUTATIONS = set('external_review_request write_file apply_patch run_command project_environment integration_prepare godot_recover libresprite_recover command_session mkdir move delete append_ai_change memory_save resource_lease'.split())


def register(server, bridge):
    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True,
                                            idempotent_hint=False, open_world_hint=True))
    async def code_mode(code: str, request_id: str) -> dict:
        """Run bounded JavaScript over Core tools: const r = tools.read_file({path:'a'}); text(r).

        tools calls are synchronous/ordered; await is also accepted. No filesystem, network,
        imports, timers or Node APIs in JS. text(value) emits results; no implicit output.
        ALL_TOOLS lists callable names/descriptions/schemas. 24 calls, 32 KiB text, 60 s between-call scheduling budget,
        3 s CPU, 32 MiB JS memory. Dispatched tools finish under their own deadlines. A fresh interpreter per call; no background promises.
        Supply a stable request_id. Repeating it NEVER reexecutes the script; consult returned
        child receipts. Mutating calls get deterministic child IDs; do not override them.
        A failed/interrupted script does not undo or automatically retry its child operations.
        Task begin/checkpoint and memory deletion must be called directly. Parallel JS syntax
        does not parallelize tools. Ending JS does not cancel jobs already launched.
        """
        if not isinstance(code, str) or not code.strip() or len(code.encode()) > 64000:
            raise ValueError('code must be nonempty and at most 64000 UTF-8 bytes')
        OperationStore.validate_id(request_id)
        if len(request_id) > 96:
            raise ValueError('Code Mode request_id must be at most 96 characters')
        store = bridge.operations
        scope = str(bridge.WORKSPACE)
        gate = store.gate()
        proc = None
        fresh = False
        calls, outputs = [], []
        output_size = 0
        emitted_error = None
        try:
            previous, fresh = store.reserve(request_id, scope, 'code_mode', {'code': code})
            if not fresh:
                return dict(previous, replayed=True, automatic_retry=False)
            store.starting(request_id)
            catalog = []
            for tool in await server.list_tools():
                if tool.name not in EXCLUDED:
                    schema = tool.model_dump(by_alias=True)
                    catalog.append({'name': tool.name, 'description': (tool.description or '')[:180],
                                    'inputSchema': schema['inputSchema']})
            names = {t['name'] for t in catalog}
            proc = await asyncio.create_subprocess_exec(sys.executable, '-I', '-B',
                str(Path(__file__).with_name('code_mode_worker.py')),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=150000,
                env={'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8'})
            proc.stdin.write((json.dumps({'code':code,'tools':catalog}, ensure_ascii=False)+'\n').encode())
            await proc.stdin.drain()
            workspace_updates = None
            deadline = time.monotonic() + 60
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Code Mode wall time exhausted')
                raw = await asyncio.wait_for(proc.stdout.readline(), remaining)
                if not raw:
                    raise RuntimeError('JS worker stopped (CPU/memory limit or interpreter failure)')
                msg = json.loads(raw)
                if msg['kind'] == 'done':
                    emitted_error = msg.get('error')
                    break
                response = {'ok': True, 'result': None}
                try:
                    if msg['kind'] == 'text':
                        value = msg['payload']
                        encoded = json.dumps(value, ensure_ascii=False)
                        if output_size + len(encoded.encode()) > MAX_OUTPUT:
                            raise ValueError('Output budget exhausted')
                        outputs.append(value); output_size += len(encoded.encode())
                    elif msg['kind'] == 'tool':
                        if workspace_updates:
                            raise ValueError('Batch paused at a tool boundary for a Workspace instruction. Read workspace_updates in the Code Mode result, acknowledge it in task_checkpoint, and plan remaining work. Do not replay completed calls.')
                        payload = msg['payload']; name = payload['name']; args = payload['arguments']
                        if name not in names or not isinstance(args, dict):
                            raise ValueError('Tool unavailable in this profile or invalid arguments')
                        if len(calls) >= MAX_CALLS:
                            raise ValueError('Tool call budget exhausted')
                        child_id = f'{request_id}.{len(calls)+1}'
                        if name in MUTATIONS and name != 'resource_lease':
                            if args.get('request_id') not in (None, '', child_id):
                                raise ValueError('Child request IDs are assigned by Code Mode')
                            args['request_id'] = child_id
                        entry = {'tool':name, 'request_id':child_id if name in MUTATIONS else None,
                                 'state':'dispatched'}
                        calls.append(entry)
                        # Persist dispatch metadata before the side effect, not arguments/content.
                        store.progress(request_id, {'calls':calls, 'automatic_retry':False})
                        # Do not cancel a dispatched mutation on JS timeout. Finish its receipt first.
                        result = await server.call_tool(name, args)
                        body = result.model_dump(mode='json', by_alias=True)
                        entry['state'] = 'error' if body.get('isError') else 'returned'
                        response['result'] = body.get('structuredContent')
                        if response['result'] is None:
                            content = body.get('content', [])
                            try:
                                response['result'] = json.loads(content[0]['text']) if len(content)==1 and content[0]['type']=='text' else body
                            except (ValueError,KeyError): response['result'] = body
                        value=response['result']
                        if isinstance(value,dict):
                            update=value.get('workspace_updates') or (value.get('continuity') or {}).get('workspace_updates')
                            if update:
                                # Forward critical instructions even if JS prints only
                                # selected fields, and refuse later calls in this batch.
                                workspace_updates=update
                        if body.get('isError'):
                            store.progress(request_id, {'calls':calls, 'automatic_retry':False})
                            raise ValueError('Tool reported an error: '+json.dumps(response['result'])[:1400])
                        if len(json.dumps(response).encode()) > MAX_RESPONSE:
                            raise ValueError('Tool result too large; use paginated direct tools')
                        store.progress(request_id, {'calls':calls, 'automatic_retry':False})
                    else:
                        raise ValueError('Unknown worker message')
                except Exception as exc:
                    response = {'ok':False, 'error':str(exc)[:1800]}
                proc.stdin.write((json.dumps(response)+'\n').encode())
                await proc.stdin.drain()
            result = {'ok':not emitted_error, 'outputs':outputs, 'calls':calls,
                      'error':emitted_error, 'request_id':request_id, 'automatic_retry':False}
            if workspace_updates:
                result['workspace_updates']=workspace_updates;result['paused_for_updates']=True
            # Never persist file content/output. Retry returns receipt metadata only.
            store.finish_mutation(request_id, {'ok':not emitted_error,'calls':calls,
                'error':('Batch paused for Workspace update' if workspace_updates else 'Script failed') if emitted_error else None,'paused_for_updates':bool(workspace_updates),'automatic_retry':False}, failed=bool(emitted_error))
            return result
        except BaseException as exc:
            if fresh:
                store.finish_mutation(request_id, {'ok':False,'calls':calls,'automatic_retry':False,
                    'error':type(exc).__name__}, unknown=True)
            if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                raise
            return {'ok':False,'outputs':outputs,'calls':calls,'error':str(exc),
                    'request_id':request_id,'automatic_retry':False}
        finally:
            if proc:
                if proc.returncode is None:
                    proc.kill()
                await proc.wait()
            os.close(gate)
