"""Explicit live acceptance: temporary synthetic scene, muted audio, real MCP calls.
Run manually with OBS open and idle. Does not capture the desktop.
"""
import asyncio
import json
import os
from pathlib import Path
import sys
import uuid

os.environ.setdefault('BRIDGE_WORKSPACE', str(Path.home()))
os.environ['BRIDGE_MEMORY_DISABLED'] = '1'
import bridge
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def rpc(name, data=None):
    with bridge.obs_controller.connection() as connection:
        request_id = uuid.uuid4().hex
        connection.ws.send(json.dumps({'op': 6, 'd': {'requestType': name,
                            'requestId': request_id, 'requestData': data or {}}}))
        response = connection.receive(7)
        if response['requestId'] != request_id or not response['requestStatus']['result']:
            raise RuntimeError('OBS setup request failed: ' + name)
        return response.get('responseData', {})


async def check(scene):
    # Do not resolve the venv executable symlink: that would lose its packages.
    async with stdio_client(StdioServerParameters(command=sys.executable,
            args=['-B', str(Path(__file__).with_name('mcp_server.py'))],
            env=dict(os.environ))) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert {'obs_status', 'obs_start_recording', 'obs_stop_recording'} <= names
            async def call(name):
                result = await session.call_tool(name, {})
                if result.is_error:
                    raise RuntimeError(str(result.structured_content))
                return result.structured_content
            status = await call('obs_status')
            assert status['current_scene'] == scene
            started = await call('obs_start_recording')
            assert started['verified']
            try:
                await asyncio.sleep(3)
            finally:
                stopped = await call('obs_stop_recording')
            assert stopped['verified']
            path = Path(stopped['output_path'])
            assert path.is_file() and path.stat().st_size > 1000
            print(json.dumps({'catalogue_count': len(names), 'mcp_status_start_stop': 'PASS',
                              'output_path': str(path), 'bytes': path.stat().st_size}))


def main():
    state = bridge.obs_controller.execute('status')
    assert state['ok'] and not state['recording']['outputActive'], 'OBS must be open and idle'
    original = state['current_scene']
    scene = 'Bridge recording check ' + uuid.uuid4().hex[:8]
    muted = {}
    created = False
    try:
        for name in rpc('GetSpecialInputs').values():
            if name:
                muted[name] = rpc('GetInputMute', {'inputName': name})['inputMuted']
                rpc('SetInputMute', {'inputName': name, 'inputMuted': True})
        rpc('CreateScene', {'sceneName': scene})
        created = True
        rpc('CreateInput', {'sceneName': scene, 'inputName': scene + ' Colour',
                           'inputKind': 'color_source_v3', 'inputSettings': {
                           'color': 0xffa06020, 'width': 1280, 'height': 720}, 'sceneItemEnabled': True})
        rpc('CreateInput', {'sceneName': scene, 'inputName': scene + ' Text',
                           'inputKind': 'text_ft2_source_v2', 'inputSettings': {
                           'text': 'OBS + Bridge\nRecording verification\nNo desktop or microphone captured',
                           'font': {'face': 'DejaVu Sans', 'size': 48, 'flags': 0}}, 'sceneItemEnabled': True})
        rpc('SetCurrentProgramScene', {'sceneName': scene})
        asyncio.run(check(scene))
    finally:
        # Leave the synthetic scene muted if state is uncertain or still recording:
        # restoring the real scene/audio could accidentally capture private content.
        state = bridge.obs_controller.execute('status')
        if state.get('ok') and not state['recording']['outputActive']:
            if created:
                rpc('SetCurrentProgramScene', {'sceneName': original})
                rpc('RemoveScene', {'sceneName': scene})
            for name, value in muted.items():
                rpc('SetInputMute', {'inputName': name, 'inputMuted': value})
        else:
            print('OBS state uncertain/active: synthetic scene and muted audio retained for inspection', file=sys.stderr)


if __name__ == '__main__':
    main()
