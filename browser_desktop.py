"""Browser MCP semantics + existing GNOME portal input, with measured pointer feedback."""
import asyncio
import json
import uuid

from desktop_coordinates import pointer_calibration, calibrated_point, rect, center, numbers

# Only the configured local Browser MCP is contacted. No endpoint comes from page content.
BROWSER_MCP_URL = 'http://127.0.0.1:8931/mcp'

INSTALL = r'''(element) => {
  if (!element || element.ownerDocument !== document || window !== top)
    throw new Error('Select a main-document element; iframe coordinates are not inferred');
  const key = __KEY__, href = location.href, title = document.title;
  const viewport = () => [innerWidth, innerHeight, devicePixelRatio,
    visualViewport?.offsetLeft || 0, visualViewport?.offsetTop || 0, visualViewport?.scale || 1,
    scrollX, scrollY];
  const initial = JSON.stringify(viewport());
  let move = null, click = null, initialBounds = null;
  const geometry = () => {
    if (!element.isConnected || document.visibilityState !== 'visible' ||
        location.href !== href || document.title !== title ||
        JSON.stringify(viewport()) !== initial)
      throw new Error('Browser target, document or viewport changed; recalibrate');
    const r = element.getBoundingClientRect(), s = getComputedStyle(element);
    const bounds = [r.x,r.y,r.width,r.height];
    if (initialBounds && bounds.some((v,i) => Math.abs(v-initialBounds[i]) > .5))
      throw new Error('Browser target, document or viewport changed; recalibrate');
    const hit = document.elementFromPoint(r.x + r.width/2, r.y + r.height/2);
    if (r.width <= 0 || r.height <= 0 || r.x < 0 || r.y < 0 ||
        r.right > innerWidth || r.bottom > innerHeight ||
        s.visibility !== 'visible' || s.display === 'none' || Number(s.opacity) === 0 ||
        element.matches(':disabled,[aria-disabled="true"]') ||
        !(hit === element || element.contains(hit)))
      throw new Error('Target center is hidden, disabled, outside the viewport or covered');
    initialBounds ||= bounds;
    return {title, bounds, viewport:viewport()};
  };
  const onMove = e => { if (e.isTrusted) move = {client:[e.clientX,e.clientY],
    target: e.composedPath().includes(element)}; };
  const onClick = e => { if (e.isTrusted && click === null) click = {client:[e.clientX,e.clientY],
    target: e.composedPath().includes(element)}; };
  const cleanup = () => {
    removeEventListener('pointermove',onMove,true); removeEventListener('click',onClick,true);
    clearTimeout(timer); delete window[key];
  };
  const timer = setTimeout(cleanup, 45000);
  addEventListener('pointermove',onMove,true); addEventListener('click',onClick,true);
  window[key] = {
    arm: () => { move = null; return geometry(); },
    read: () => ({...geometry(), move}),
    prepareClick: () => { click = null; return geometry(); },
    result: () => ({click, connected:element.isConnected, focused:document.activeElement === element,
      text:(element.innerText || '').slice(0,200), checked:element.getAttribute('aria-checked'),
      expanded:element.getAttribute('aria-expanded')}),
    cleanup
  };
  try { return geometry(); } catch(e) { cleanup(); throw e; }
}'''


def probe_points(frame, image):
    """Hover-only probes inside the visible compositor frame, never guessed click targets."""
    x, y, w, h = rect(frame, 'window frame')
    ox, oy = image['logical_position']
    mw, mh = image['logical_size']
    left, top = max(x, ox), max(y, oy)
    right, bottom = min(x+w, ox+mw), min(y+h, oy+mh)
    if right-left < 250 or bottom-top < 250:
        raise ValueError('Restore more of the browser on the selected monitor before calibration')
    return [[left+(right-left)*fx, top+(bottom-top)*fy]
            for fx, fy in ((.35, .55), (.65, .75), (.42, .83))]


async def browser_pointer_action(client, process_id, target, session_id, tab_index, document_title, action='click',
                                 browser_session=None, click_scope='unspecified'):
    """One finite calibration + one hover/click. Never retries a click or navigates."""
    if action not in ('move', 'click'):
        raise ValueError('action must be move or click')
    if click_scope not in ('unspecified', 'reversible'):
        raise ValueError('click_scope must be unspecified or reversible')
    if action == 'click' and click_scope != 'reversible':
        raise ValueError('Physical clicks require click_scope=reversible for a low-impact reversible action. '
                         'Use Browser MCP semantic actions for consequential operations; do not bypass with raw input.')
    if not isinstance(target, str) or not target.strip() or len(target) > 2000:
        raise ValueError('Provide the unique target/ref identified by Browser MCP')
    if not session_id:
        raise ValueError('An active desktop_session authorization is required')
    if isinstance(tab_index, bool) or not isinstance(tab_index, int) or tab_index < 0:
        raise ValueError('Provide the tab index from Browser MCP browser_tabs')
    if browser_session is None:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        async with streamable_http_client(BROWSER_MCP_URL) as (read, write, *_):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await browser_pointer_action(client, process_id, target, session_id,
                                                    tab_index, document_title, action, browser_session=session, click_scope=click_scope)

    async def evaluate(function, **extra):
        result = await asyncio.wait_for(browser_session.call_tool(
            'browser_evaluate', {'function': function, **extra}), 8)
        if result.is_error:
            # Raw errors may contain extension URLs/tokens or private page snippets.
            text = '\n'.join(c.text for c in result.content if c.type == 'text')
            known = ('Browser target, document or viewport changed; recalibrate',
                     'Target center is hidden, disabled, outside the viewport or covered',
                     'Select a main-document element; iframe coordinates are not inferred')
            reason = next((reason for reason in known if reason in text),
                          'Browser MCP could not confirm the target/page state')
            stage = 'install' if extra.get('target') else ('arm' if '.arm()' in function else
                    'read' if '.read()' in function else 'prepare_click' if '.prepareClick()' in function else 'readback')
            raise ValueError(f'{reason} ({stage}); no automatic retry')
        text = '\n'.join(c.text for c in result.content if c.type == 'text')
        try:
            return json.loads(text.split('### Result\n', 1)[1].split('\n###', 1)[0])
        except (ValueError, IndexError) as exc:
            raise ValueError('Unrecognized Browser MCP geometry response') from exc

    key = '__localDevBridgePointer_' + uuid.uuid4().hex
    member = 'window[' + json.dumps(key) + ']'
    binding = None
    installed = False
    # Serialize this physical sequence with the existing desktop operations. This
    # is the same DesktopClient RLock; no second input owner/backend is introduced.
    with client.lock:
        try:
            client.call('session', command='status')
            # Upstream selectTab indexes Context._tabs without initializing it.
            # Listing first populates the per-client view of the shared browser.
            listed = await browser_session.call_tool('browser_tabs', {'action': 'list'})
            if listed.is_error:
                raise ValueError('Browser MCP could not enumerate tabs')
            selected = await browser_session.call_tool('browser_tabs', {'action': 'select', 'index': tab_index})
            if selected.is_error:
                raise ValueError('Browser MCP could not select the specified tab')
            actual_title = await evaluate('() => document.title')
            if actual_title != document_title:
                raise ValueError('Browser tab title differs from the observed document; inspect browser_tabs again')
            initial = await evaluate(INSTALL.replace('__KEY__', json.dumps(key)), target=target)
            installed = True
            binding = client.call('browser_pointer', command='prepare', session_id=session_id,
                                  process_id=process_id, document_title=initial['title'])

            def physical(command, position):
                return client.call('browser_pointer', command=command, session_id=session_id,
                                   calibration_id=binding['calibration_id'], position=position)

            async def move_and_read(position):
                await evaluate('() => ' + member + '.arm()')
                physical('move', position)
                data = await evaluate('() => ' + member + '.read()')
                if not data.get('move'):
                    raise ValueError('The browser received no pointer event for the physical move; no click sent')
                return data

            samples = []
            for position in probe_points(binding['window']['frame_bounds'], binding['image']):
                data = await move_and_read(position)
                samples.append({'logical': position, 'client': data['move']['client']})
            calibration = pointer_calibration(samples)
            data = await evaluate('() => ' + member + '.read()')
            css = center(data['bounds'])
            destination = calibrated_point(css, calibration)
            hover = await move_and_read(destination)
            new_center = center(hover['bounds'])
            if (any(abs(css[i]-new_center[i]) > .5 for i in (0, 1)) or
                    any(abs(hover['move']['client'][i]-css[i]) > 2 for i in (0, 1)) or
                    not hover['move']['target']):
                raise ValueError('Physical hover did not reach the confirmed DOM target; no click sent')
            result = {'ok': True, 'action': action, 'hover_verified': True,
                      'pointer_verified': True,  # Compatibility: hover only, never click success.
                      'calibration': calibration, 'target_client': list(css),
                      'backend': 'portal input', 'verification_required': action == 'click',
                      'automatic_retry': False}
            if action == 'move':
                result.update(executed=True, verified=True, click_sent=False,
                              click_status='not_requested', click_target_verified=None)
                return result

            # Clear earlier events and recheck geometry. This narrows, but cannot
            # close, the DOM-to-OS race; never claim that dispatch is atomic.
            await evaluate('() => ' + member + '.prepareClick()')
            try:
                receipt = physical('click', destination)
                result.update(receipt)
                result.update(click_sent=True, dispatch_status='submitted')
            except Exception:
                # A lost worker receipt does not prove that no input was delivered.
                # Preserve uncertainty as structured output, without exposing raw errors.
                result.update(executed=None, click_sent=None, dispatch_status='unknown')
            result['verified'] = False  # Application effect needs a separate observation.
            try:
                result['readback'] = await evaluate('() => ' + member + '.result()')
            except Exception:
                result['readback'] = {'available': False}
            readback = result['readback']
            event = readback.get('click') if isinstance(readback, dict) else None
            hit = None
            if isinstance(event, dict):
                if event.get('target') is False:
                    hit = False
                elif event.get('target') is True:
                    try:
                        point = numbers(event.get('client'), 2, 'click client')
                        if all(abs(point[i] - css[i]) <= 2 for i in (0, 1)):
                            hit = True
                    except ValueError:
                        pass
            result['click_target_verified'] = hit
            result['click_status'] = 'target_hit' if hit is True else 'target_missed' if hit is False else 'unconfirmed'
            result['ok'] = hit is True and result['dispatch_status'] == 'submitted'
            if hit is False:
                result['error'] = 'Physical click observed outside the target; input already occurred. Do not retry.'
            elif not result['ok']:
                result['error'] = 'Physical click outcome is uncertain; input may have occurred. Do not retry.'
            result['next_step'] = 'Inspect the application state with Browser MCP before any further action.'
            return result
        finally:
            if installed:
                try:
                    await evaluate('() => { ' + member + '?.cleanup(); return true; }')
                except Exception:
                    pass  # The bounded page listener also expires after 45 seconds.
            if binding:
                try:
                    client.call('browser_pointer', command='finish', session_id=session_id,
                                calibration_id=binding['calibration_id'])
                except (RuntimeError, ValueError):
                    pass  # A click already consumed it, or the portal was revoked.
