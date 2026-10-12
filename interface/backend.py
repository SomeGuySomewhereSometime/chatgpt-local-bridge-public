"""Local control-panel backend. Fixed actions; no HTTP listener or shell endpoint."""
from __future__ import annotations
import argparse
import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
# Staging tests use the installed checkout without copying its environment.
if not (ROOT / 'bridge_config.json').exists():
    ROOT = Path('/home/bridge-user/chatgpt-local-bridge-NOBUBBLEWARP')
sys.path.insert(0, str(ROOT))
from operation_store import OperationStore, configured_directory, identity

UNIT = 'mcp-tunnel-chatgpt-local-bridge-NOBUBBLEWARP.service'
PROFILE = Path.home() / '.config/tunnel-client/chatgpt-local-bridge-NOBUBBLEWARP.yaml'
COMPONENTS = {
    'bridge': ('Core NOBUBBLEWARP', UNIT),
    'desktop': ('Desktop NOBUBBLEWARP', 'mcp-tunnel-chatgpt-local-bridge-desktop.service'),
    'browser': ('Browser', 'mcp-tunnel-chatgpt-browser.service'),
    'unity': ('Unity', 'mcp-tunnel-chatgpt-unity.service'),
    'blender': ('Blender', 'mcp-tunnel-chatgpt-blender.service'),
    'godot': ('Godot', 'mcp-tunnel-chatgpt-godot.service'),
    'libresprite': ('LibreSprite', 'mcp-tunnel-chatgpt-libresprite.service'),
}


def settings():
    config_path = Path(os.environ.get('BRIDGE_CONFIG', ROOT / 'bridge_config.json'))
    return json.loads(config_path.read_text())


def run(argv, timeout=15):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                          env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))


def service_states():
    units = [unit for _, unit in COMPONENTS.values()]
    result = run(['systemctl', '--user', 'show', '--property=Id,LoadState,ActiveState,SubState', '--', *units])
    records = {}
    for block in result.stdout.strip().split('\n\n'):
        values = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
        if values.get('Id'):
            records[values['Id']] = values
    return {key: {'name': name, 'unit': unit,
                  'installed': records[unit].get('LoadState') == 'loaded' if unit in records else None,
                  'active_state': records.get(unit, {}).get('ActiveState', 'unknown'),
                  'sub_state': records.get(unit, {}).get('SubState', 'unknown')}
            for key, (name, unit) in COMPONENTS.items()}


def registry_snapshot(directory):
    """Observation must not create, migrate or modify operational state."""
    path = Path(directory) / 'requests.sqlite3'
    empty = {'receipts': [], 'leases': [], 'counts': {}, 'storage_status': 'empty'}
    if not path.exists():
        return empty
    if path.is_symlink():
        return dict(empty, storage_status='unavailable')
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            columns = {row[1] for row in db.execute('PRAGMA table_info(requests)')}
            rows = db.execute('SELECT * FROM requests ORDER BY created_at DESC LIMIT 40').fetchall()
            receipts = []
            counts = dict(db.execute('SELECT state,count(*) FROM requests GROUP BY state').fetchall())
            for row in rows:
                state = row['state']
                if state in {'accepted', 'running'} and identity(row['owner_pid']) != row['owner_birth']:
                    state = 'unknown'
                item = {key: row[key] for key in ('request_id', 'operation', 'scope', 'created_at', 'returncode')}
                item['state'] = state
                if 'result_json' in columns and row['result_json']:
                    # The existing registry contains metadata only. Never include lease tokens.
                    result = json.loads(row['result_json'])
                    item['path'] = result.get('path', result.get('destination', ''))
                receipts.append(item)
            # Derive displayed active/unknown counts from owner identities too.
            active = db.execute("SELECT state,owner_pid,owner_birth FROM requests WHERE state IN ('accepted','running')").fetchall()
            for row in active:
                if identity(row['owner_pid']) != row['owner_birth']:
                    counts[row['state']] -= 1
                    counts['unknown'] = counts.get('unknown', 0) + 1
            restart_blockers = 0
            for row in db.execute("SELECT * FROM requests WHERE state NOT IN ('completed','resolved','failed')"):
                known = identity(row['owner_pid']) == row['owner_birth']
                metadata = json.loads(row['result_json'] or '{}') if 'result_json' in columns else {}
                if not (known and row['state']=='running' and row['operation']=='run_command' and metadata.get('persistent') is True):
                    restart_blockers += 1
            leases = []
            if db.execute("SELECT name FROM sqlite_master WHERE name='leases'").fetchone():
                leases = [{'resource': r['resource'], 'owner_id': r['owner'], 'expires_at': r['expires']}
                          for r in db.execute('SELECT resource,owner,expires FROM leases WHERE expires>? ORDER BY resource',
                                              (time.time(),)).fetchall()]
            return {'receipts': receipts, 'leases': leases, 'counts': counts, 'restart_blockers':restart_blockers, 'storage_status': 'available'}
    except (sqlite3.Error, OSError, ValueError, KeyError):
        return dict(empty, storage_status='unavailable')


def persistent_snapshot(directory):
    from persistent_jobs import PersistentSessions
    try: return PersistentSessions(directory).list()
    except (OSError, ValueError): return []


def snapshot():
    config = settings()
    directory = configured_directory(config)
    states = service_states()
    connection = {'remote_connected': None, 'last_success_age_seconds': None}
    if states['bridge']['active_state'] == 'active':
        from ops.bridge_watchdog import poll_health
        connection = poll_health()
    return {'ok': True, 'root': str(ROOT), 'execution_mode': config.get('execution', {}).get('mode', 'host'),
            'workspace': os.environ.get('BRIDGE_WORKSPACE', str(Path.home())),
            'launcher_available': (ROOT / 'start.sh').is_file(), 'profile_configured': PROFILE.is_file(),
            'services': states, 'connection': connection, 'jobs': persistent_snapshot(directory),
            'operations_directory': str(directory), 'updated_at': time.time(),
            **registry_snapshot(directory)}


def continuity_snapshot(cwd, task_id=''):
    from continuity import readonly_context, markdown
    import bridge
    project = bridge.safe_path(cwd)
    if not project.is_dir():
        raise ValueError('Escolhe uma pasta de projeto existente.')
    settings = bridge.BRIDGE_CONFIG.get('memory', {})
    if not bridge.memory.enabled:
        return {'ok': True, 'status': 'disabled', 'tasks': []}
    try:
        result = readonly_context(Path(settings['path']).expanduser().absolute(), str(project), task_id)
        if task_id:
            # Reading receipts here must not create/migrate the operational database.
            records = registry_snapshot(configured_directory(bridge.BRIDGE_CONFIG))
            indexed = {r['request_id']: r for r in records['receipts']}
            for item in result.get('operations', []):
                r = indexed.get(item['request_id'])
                item['state'] = r['state'] if r and r['scope'] == item['cwd'] else 'not_checked'
            result['markdown'] = markdown(result)
        return dict(result, ok=True)
    except (OSError, sqlite3.Error, ValueError):
        return {'ok': False, 'error': 'Não foi possível ler a continuidade. Nenhuma operação foi repetida.'}


def control_service(action):
    if action not in {'start', 'stop', 'restart'}:
        raise ValueError('Ação de serviço inválida.')
    state = service_states()['bridge']
    if state['installed'] is None:
        raise ValueError('Não foi possível consultar o serviço. Verifica a sessão systemd antes de agir.')
    if not state['installed']:
        raise ValueError('O serviço NOBUBBLEWARP ainda não está instalado. A interface não cria nem substitui túneis.')
    if action == 'start' and not PROFILE.is_file():
        raise ValueError('Falta configurar o perfil de túnel próprio da NOBUBBLEWARP.')
    store = OperationStore(configured_directory(settings()))
    gate = None
    try:
        if action in {'stop', 'restart'}:
            gate = store.gate(exclusive=True)
            if store.transport_blockers():
                raise ValueError('Há operações ativas ou com resultado desconhecido. Verifica os recibos antes de parar/reiniciar.')
        result = run(['systemctl', '--user', action, UNIT], timeout=45)
        if result.returncode:
            raise ValueError('Não foi possível alterar o serviço NOBUBBLEWARP. Consulta o diário desse serviço.')
        return {'ok': True, 'action': action, 'service': service_states()['bridge']}
    except BlockingIOError:
        raise ValueError('A bridge tem trabalho em curso. O serviço foi preservado.') from None
    finally:
        if gate is not None:
            os.close(gate)


async def diagnose():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    async with stdio_client(StdioServerParameters(command=str(ROOT / 'start.sh'), env=dict(os.environ))) as (read, write):
        async with ClientSession(read, write) as session:
            initial = await asyncio.wait_for(session.initialize(), 15)
            tools = await asyncio.wait_for(session.list_tools(), 15)
            names = {tool.name for tool in tools.tools}
            expected = {'run_command', 'resource_lease', 'operation_request', 'project_environment', 'integration_prepare'}
            return {'ok': expected <= names and 'NOBUBBLEWARP' in initial.server_info.name,
                    'server_name': initial.server_info.name, 'tools': len(names),
                    'hosted_verified': False}


def dispatch(payload):
    action = payload.get('action', 'snapshot')
    if action == 'companion_open':
        launcher = ROOT / 'companion/open-workspace.sh'
        subprocess.Popen([str(launcher)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return {'ok': True}
    if action == 'companion':
        from companion.coordinator import Coordinator
        return {'ok': True, **Coordinator().dispatch({'action':'list'})}
    if action == 'companion_stop':
        from companion.coordinator import Coordinator, conversation
        from companion import goal_loop
        coordinator = Coordinator()
        with coordinator.db:
            coordinator.db.execute('BEGIN IMMEDIATE')
            cid = conversation(payload.get('url', ''))
            goal_loop.stop(coordinator.db, cid, 'Stopped from Bridge Control. In-flight messages and tools may finish.')
            return {'ok': True, 'loop': goal_loop.status(coordinator.db, cid)}
    if action == 'companion_unbind':
        from companion.coordinator import Coordinator
        return {'ok': True, **Coordinator().dispatch({'action':'unbind','url':payload.get('url','')})}
    if action == 'snapshot':
        return snapshot()
    if action == 'continuity':
        return continuity_snapshot(payload.get('cwd', '.'), payload.get('task_id', ''))
    if action == 'continuity_export':
        import bridge
        content = payload.get('content', '')
        if not isinstance(content, str) or len(content) > 60000:
            raise ValueError('Exportação inválida.')
        # New files only: never overwrite an existing project document silently.
        return bridge.write_file(bridge.WriteRequest(path=payload.get('path', ''), content=content,
            expected_version='missing', request_id=payload.get('request_id', ''),
            owner_id=payload.get('owner_id', ''), lease_token=payload.get('lease_token', '')))
    if action == 'service':
        return control_service(payload.get('command', ''))
    if action == 'diagnose':
        return asyncio.run(diagnose())
    if action == 'probe':
        component = payload.get('component', '')
        if component not in COMPONENTS or component == 'bridge':
            raise ValueError('Escolhe uma integração de aplicação.')
        result = run([sys.executable, '-B', str(ROOT / 'ops/mcp-services.py'), 'status', component], timeout=45)
        if result.returncode:
            raise ValueError('Não foi possível verificar a integração. Consulta os serviços locais.')
        status = json.loads(result.stdout)[component]
        return {'ok': True, 'component': component,
                'integration_ready': status.get('integration_ready', status.get('application') is True or status.get('mcp') is True),
                'service': status.get('service', 'unknown'),
                'application': status.get('application'), 'mcp': status.get('mcp'),
                'tunnel_ready': status.get('tunnel_ready', {}).get('ok'),
                'chatgpt_verified': False}
    if action not in {'project', 'lease', 'prepare'}:
        raise ValueError('Ação não suportada pela interface.')
    # Use the same path policy, reservations and receipts as MCP clients.
    import bridge
    common = {key: payload.get(key, '') for key in ('request_id', 'owner_id', 'lease_token')}
    if action == 'project':
        result = bridge.project_environment(bridge.ProjectEnvironmentRequest(
            cwd=payload.get('cwd', '.'), action=payload.get('command', 'status'),
            python=payload.get('python', False), **common))
        return dict(result, ok=result.get('ok', True))
    if action == 'lease':
        return bridge.resource_lease(bridge.LeaseRequest(action=payload.get('command', 'list'),
            resource=payload.get('resource', ''), owner_id=common['owner_id'],
            lease_token=common['lease_token'], ttl_seconds=300))
    if action == 'prepare':
        import mcp_server
        component = payload.get('component', '')
        if component not in COMPONENTS or component == 'bridge':
            raise ValueError('Escolhe uma integração de aplicação.')
        return mcp_server.integration_prepare(component, open_application=False, **common)
    raise ValueError('Ação não suportada pela interface.')


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        raw = sys.stdin.buffer.read(16385)
        if len(raw) > 16384:
            raise ValueError('Pedido demasiado grande.')
        payload = json.loads(raw or b'{}')
        if not isinstance(payload, dict):
            raise ValueError('Pedido inválido.')
        result = dispatch(payload)
    except Exception as exc:
        # Do not echo subprocess output, credentials or an entire request.
        from diagnostics import redact
        detail = getattr(exc, 'detail', str(exc))
        result = {'ok': False, 'error': redact(str(detail))[:800], 'automatic_retry': False}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
