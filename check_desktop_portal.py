"""Manual-consent acceptance test; all input targets belong to our fullscreen GTK fixture."""
import base64
import json
from pathlib import Path
import subprocess
import time

from desktop import DesktopClient

client = DesktopClient()
p = subprocess.Popen(['/usr/bin/python3', '-B', str(Path(__file__).with_name('desktop_test_window.py')), '--fullscreen'],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    time.sleep(1)
    status = client.call('session', command='start')
    print('GNOME_PERMISSION_REQUEST', json.dumps(status), flush=True)
    deadline = time.monotonic() + 95
    while status['state'] == 'pending' and time.monotonic() < deadline:
        time.sleep(1)
        status = client.call('session', command='status')
    assert status['state'] == 'active', status
    print('SESSION_ACTIVE', flush=True)
    session_id = status['session_id']
    r = client.call('observe', application='bridge-desktop-test', max_elements=100, screenshot=True)
    def point(name, fraction=.5):
        element = next(e for e in r['elements'] if e['name'] == name)
        x, y, w, h = element['bounds']
        props = status['streams'][0]['properties']
        ox, oy = props.get('position', [0, 0])
        width, height = props.get('logical_size', props['size'])
        cx, cy = x + w*fraction - ox, y + h/2 - oy
        assert 0 <= cx < width and 0 <= cy < height, 'Fixture not on selected monitor'
        return {'x': cx/width, 'y': cy/height}
    def act(actions):
        global r
        result = client.call('act', snapshot_id=r['snapshot_id'], actions=actions,
                             session_id=session_id, wait_ms=350, screenshot=True)
        assert result['ok'], result['error']
        r = result['observation']
        assert 'image' in r, r
    act([{'kind': 'click', **point('Test name')}, {'kind': 'key', 'keys': ['x']}])
    assert next(e for e in r['elements'] if e['name']=='Test name')['text'] == 'x'
    print('RAW_KEYBOARD_PASS', flush=True)
    act([{'kind': 'key', 'keys': ['CTRL','a']},
         {'kind': 'type_text', 'text': 'Olá, rato e teclado!'}])
    field = next(e for e in r['elements'] if e['name'] == 'Test name')
    assert field['text'] == 'Olá, rato e teclado!', field
    print('FOCUSED_UNICODE_INSERTION_PASS', flush=True)
    act([{'kind': 'click', **point('Apply test')}])
    assert any(e.get('text') == 'Applied: Olá, rato e teclado!' for e in r['elements'])
    print('RAW_CLICK_PASS', flush=True)
    start, end = point('Test slider', .15), point('Test slider', .85)
    before = next(e for e in r['elements'] if e['name']=='Test slider')['value']
    act([{'kind': 'drag', **start, 'to_x': end['x'], 'to_y': end['y'], 'duration_ms': 500}])
    after = next(e for e in r['elements'] if e['name']=='Test slider')['value']
    assert after > before + 30, (before,after)
    print('RAW_DRAG_PASS', before, after, flush=True)
    act([{'kind': 'move', **point('Test row 1')}, {'kind': 'scroll', 'dx': 0, 'dy': 300}])
    scroll = next(e.get('text', '') for e in r['elements'] if e.get('text', '').startswith('Scroll position:'))
    assert int(scroll.split(':')[1]) > 0, scroll
    print('RAW_SCROLL_PASS', scroll, flush=True)
    Path('/tmp/bridge-example.png').write_bytes(base64.b64decode(r['image']['data']))
    client.call('session', command='stop')
    try:
        client.call('act', snapshot_id=r['snapshot_id'], actions=[{'kind':'click',**start}], session_id=session_id)
        raise AssertionError('Input accepted after session closed')
    except RuntimeError as exc:
        assert 'authorized desktop session' in str(exc), str(exc)
    print('SESSION_REVOCATION_PASS\nLIVE_PORTAL_FIXTURE_PASS', flush=True)
finally:
    client.close()
    p.terminate()
    p.wait(timeout=5)
