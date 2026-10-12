# Browser MCP and physical desktop fallback

Keep the existing responsibilities: Browser MCP owns page semantics; Local Dev Bridge
owns OS focus, the authorized monitor capture and physical input. No browser proxy,
second agent or automatic retry loop is introduced.

## Interaction policy

1. Find the unique target with Browser MCP and perform the semantic action normally.
2. On failure, inspect the actual state before doing anything else. A timeout can
   happen after the click was delivered. If the expected result is already present,
   stop. If delivery is uncertain and repeating it could have side effects, stop and
   report uncertainty. Never turn a permission/policy denial into a physical retry.
3. Diagnose the failure: wrong tab, minimized window, hidden/disabled target, modal or
   overlay, animation, or lost connection. Restore the correct OS window with Bridge
   focus and re-observe. Do not click through overlays or disabled controls. A corrected
   semantic retry is appropriate only when state inspection establishes no prior effect.
4. If the confirmed target still cannot be operated semantically, physical fallback
   is limited to low-impact reversible actions under the existing task authorization.
   Declare `click_scope="reversible"` for `desktop_browser_act`. Never use physical
   fallback for payments, sending/publishing, deletion, permission changes or other
   consequential operations. Do not bypass this restriction with raw desktop input.
   This is a caller declaration, not automatic classification of page consequences.
   GNOME's actual session consent is still required. Get a fresh Bridge screenshot of
   the selected monitor and execute **one** mapped click. Prefer an observed element
   box when available; otherwise use the original monitor capture, never an estimated
   toolbar height or manually guessed normalized coordinates.
5. Read the result with Browser MCP (e.g. Listener state replacing Start listening).
   `executed=true` is only delivery. The Bridge returns a fresh desktop observation
   and `verified=false` for physical clicks; only the subsequent application readback
   establishes success. No blind repetition or pre-batched follow-up clicks.

## Coordinate inputs

The existing normalized `x,y` API stays unchanged. New `point` inputs are limited to
a single move/click, consume the normal snapshot and require a matching authorized
session plus a monitor capture younger than 30 seconds. They cannot be mixed with
other actions, direct x/y or another target. Examples below use placeholders from
the actual current observation; do not copy sample numbers as real targets.

```python
desktop_act(snapshot_id=observation_id, session_id=authorized_session, actions=[
    {"kind": "click", "point": {"space": "screenshot", "bounds": [x, y, width, height]}}
])
desktop_act(snapshot_id=observation_id, session_id=authorized_session, actions=[
    {"kind": "click", "point": {"space": "element", "element": observed_button_id}}
])
```

`screenshot` accepts either original-PNG `x,y` or a complete box and uses its center.
These are **not** coordinates from a browser-only screenshot, a resized preview, a
crop, CSS or the global desktop. Divide by the PNG width/height inside the Bridge;
then the portal maps that normalized point into its own logical stream dimensions.
Framebuffer size need not equal logical size. Do not multiply by devicePixelRatio.

`element` uses the target and owning window bounds from the same AT-SPI snapshot,
translated through that exact window's GNOME compositor frame. This also handles
toolkits reporting window-relative SCREEN coordinates. The measured frame ratios
must agree within 0.5% (pixel rounding tolerance, not a scale factor). Window identity,
active/minimized state, geometry and element signature are rechecked before dispatch.
The portal's real monitor origin is subtracted; a missing origin is not assumed zero.
A clipped target, incompatible aspect ratio or unavailable geometry is refused.

## Browser bounding box handoff

### Preferred physical fallback: measured pointer events (2026-09-10)

`desktop_browser_act(process_id, target, session_id, tab_index, document_title,
action="click", click_scope="reversible")` now performs the cross-MCP physical handoff directly. Identify the
unique target with Browser MCP and obtain its tab index with `browser_tabs`; get
Chrome's PID with `desktop_observe`. A stable unique CSS/Playwright locator is
preferable to an old snapshot ref. The Bridge connects only to the existing local
Browser MCP at `127.0.0.1:8931/mcp`, explicitly lists/selects the tab, and verifies
the document title. Separate MCP clients do not share their current tab selection.

It binds that document to one compositor window with the supplied PID and exact
native title, restores/focuses it, then captures the authorized monitor. This avoids
Chrome's localized AT-SPI tab-group suffix, which differs from its compositor title.
It installs a temporary pointer listener on the selected page, makes three hover-only
probes inside the visible window, and pairs the actual portal destinations with the
page's trusted `clientX/clientY` events. The first two samples measure CSS-to-logical
scale and origin; the third independently checks the fit. No CDP Browser domain,
toolbar estimate, devicePixelRatio multiplier or AT-SPI document is required.

Finally it moves to the measured DOM target center and checks the actual received
pointer event, hit target and unchanged geometry before **one** physical left click.
`action="move"` does the same calibration/hover without clicking. Probes may trigger
hover effects. Changes detected before dispatch (window, event, overlay or target)
fail without a click; use another deliberate strategy after inspecting the failure. Calibration
is per action, expires after 30 seconds, and is consumed before click dispatch.
Listeners are removed in finally (or after 45 seconds if the connection disappears).
Keep other browser/desktop input clients idle during the sequence.

`pointer_verified=true` is retained for compatibility and means **hover only**, as
does `hover_verified=true`. It never establishes click success. `click_status` is:

- `target_hit`: a trusted click was observed on the target at the expected position.
- `target_missed`: a trusted click was observed outside the target; input occurred.
- `unconfirmed`: no usable click observation (including navigation or lost readback).
- `not_requested`: hover-only operation.

`click_target_verified` is true/false/null respectively. `click_sent=true` means
OS dispatch returned; null means the dispatch receipt was lost or failed and
input may have occurred. Missing receipts never mean safe to retry. Misses and
uncertain delivery return `ok=false` **and MCP `isError=true`**, retaining all
receipts and readback in structured content. A hit with a lost dispatch receipt
also remains `ok=false`. Click `verified=false` and `verification_required=true`
remain until the caller separately verifies the intended application effect.
No automatic replay occurs on any of these paths. The caller must inspect state.

Immediately before dispatch, the page guard clears prior click evidence and
rechecks geometry. The first subsequent trusted click is retained, so a later
click cannot hide an earlier miss. This still does not make DOM validation and
OS dispatch atomic; other input clients must remain idle.

Iframe targets and ambiguous native
window titles are currently unsupported. If a probe lands in browser chrome rather
than web content, the absent page event stops the operation; maximize/reposition and
recalibrate instead of guessing offsets.

Live acceptance through BOTH MCP transports passed with Chrome exposing **zero**
AT-SPI document nodes: hover caused zero clicks; two separate physical click calls
incremented the fixture counter exactly once each. A second run at actual Chrome
110% zoom measured scale approximately 1.10 and both clicks passed. Zoom was restored
and the owned test tab/session closed. This was historical acceptance of the earlier
contract. The separate `../PlaywrightBridge/check_physical_pointer.py` caller must
now pass `click_scope="reversible"` before reuse; it is not part of this installed
suite. GNOME may request monitor/input consent.

### Optional AT-SPI viewport mapping

`../PlaywrightBridge/browser_pointer_target.js` is a read-only function for
`browser_evaluate(function=<file function>, target=<semantic element ref>)`. It returns
the main-document CSS box, viewport dimensions, title, timestamp, visual-viewport
state and center hit-test result. It neither clicks nor scrolls. Preserve its values.

In a fresh Bridge observation, find the matching **top-level document web** element
with `desktop_query`, scoped to the correct Chrome window. Add its Bridge `element`
ID to the returned object and pass it as `desktop_act`'s `point`. The Bridge uses that
observed document rectangle as the viewport anchor, maps the CSS box into it, maps
the owning window into compositor logical space, then normalizes into the authorized
monitor. The receipt includes the source space and normalized coordinates used.

No viewport anchor means no direct conversion. The outer browser window is not a
replacement: toolbars, decorations, dock offsets, zoom and monitor scaling differ.
Nested frames and pinch zoom/visual-viewport offsets are refused in this initial
implementation. Scroll is already reflected in the main-document client rectangle;
do not add scrollX/scrollY. Re-measure after scrolling, navigation, zoom or moving the
window. A full-page document extent that does not match the viewport aspect ratio
is rejected. Missing accessibility coverage is a reason to use the monitor image,
not to invent a document rectangle.

## Validation and current limits

- Unit tests cover HiDPI screenshots, fractional scale, negative monitor origins,
  window offsets, clipping, absent geometry, freshness, consent, single-use snapshots,
  ambiguous portal failures and a simulated Browser state change after one physical click.
- The GNOME headless test checks measured frame geometry and inactive/minimized focus
  in an isolated compositor, not the user's session.
- The real Browser MCP fixture verifies DOM geometry extraction, exact text readback
  and the existing semantic click. No X Space was joined during implementation.
- The original AT-SPI-anchored route remains separate from the newly live-validated
  pointer-event route above. No X Space was joined during these tests.
- Element/browser mapping requires the updated GNOME window extension (frame_bounds).
  Previously loaded extension code can require logout/login before it is available;
  screenshot-pixel conversion does not require that extension change.
- The cross-MCP handoff is sequential, not atomic; timestamps and revalidation reduce
  stale targeting but cannot prove that web content will not move between calls.

For the installed Bridge, run `./check.sh`. The historical BridgeRelaxed-only
`test_gnome_windows` module is not part of the installed suite. For the separate
GNOME compositor fixture, run
`check_browser.py` with the MCP Python environment from PlaywrightBridge.

References: [portal monitor metadata](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html),
[portal pointer coordinates](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html),
[Playwright bounding boxes](https://playwright.dev/docs/api/class-locator#locator-bounding-box).


## Installed guard regression checks (2026-09-10)

The page guard now binds the initial target rectangle (0.5 CSS-pixel tolerance),
scroll position and document visibility as well as the existing document/viewport
identity. A changed state rejects further calibration or click preparation.

`check_browser_geometry.py` executes the actual guard in isolated headless Chrome.
`test_browser_desktop.py` checks transport orchestration with fakes;
`test_browser_pointer_worker.py` checks a fake portal, including one-use bindings,
lost dispatch receipts, expired calibration, focus/window/monitor changes and
unmeasured destinations. `test_desktop_coordinates.py` covers measured scale,
negative monitor origins, invalid numbers and independent-probe disagreement.
These are distinct from a Browser MCP -> GNOME portal physical acceptance run.

Remaining boundary: DOM validation and OS input are separate operations. A page
can still change after the last read and before OS dispatch. Do not describe this
as an atomic or race-free click guarantee. The worker consumes its binding before
dispatch and the orchestration never retries a click. After an ambiguous result,
inspect the application state before issuing a new operation. A new API call is
not deduplicated against previous calls. Real tab switching, zoom, timed final-gap
mutation, portal delivery and repeated AT-SPI application teardown still need
separate live acceptance; headless viewport resizing is not a physical zoom test.
