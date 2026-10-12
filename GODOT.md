> NOBUBBLEWARP variant: this inherited document includes historical sandbox claims. Current host execution and file-tool policy are defined in [README.md](README.md); no command or decoder uses Bubblewrap.

# Godot MCP integration

The installed `godot-editor-mcp` pipx package (2026.9.10) owns every Godot tool.
`ops/godot_entry.py` starts that same server and adds only `/live` and `/ready`.
It does not create a second MCP server or copy application tools into the Bridge.
The editor addon owns automatic WebSocket reconnection. No addon/project files change.

## Services and ports

- `godot-editor-mcp.service`: independent backend, enabled at login; HTTP MCP
  `http://127.0.0.1:9090/mcp`, addon WebSocket `127.0.0.1:9080`.
- `godot-editor-app.service`: optional fixed-project Flatpak launch, only when no
  Godot process exists. Never enabled at login or restarted by recovery.
- `mcp-tunnel-chatgpt-godot.service`: separate tunnel, configured and active; runtime key stored outside Git;
  raw tunnel health port `127.0.0.1:8084`. Stopping it leaves backend/editor running.
- Existing Bridge/Unity/Blender/Browser remain on 8080/8081/8082/8083 respectively.

The fixed project is `/home/bridge-user/Transferências/Keyboard Warriors/warriors`.
A different open project is reported and is never switched automatically.
The existing editor PID 120754 was preserved during activation; only its manually
started MCP with no stdin client was replaced by the managed HTTP process.

## Health semantics

`http://127.0.0.1:9090/live` means the MCP service is alive. `/ready` returns 200
only after a real read-only command through the addon returns the expected project;
disconnection, timeout, command failure or another project returns 503.

The tunnel client's native `/healthz` and `/readyz` remain transport diagnostics;
**native `/readyz` does not prove editor readiness**. `integration_status.godot`
reports `tunnel_transport_ready` separately and computes `tunnel_ready.ok` from
transport readiness AND real editor readiness. `integration_ready` is local readiness.
`chatgpt_available: null` means no direct ChatGPT call has been verified by this probe;
it is never inferred from local health. ChatGPT tool invocation is the final test.

## Lifecycle

Use Bridge `godot_recover(open_application=false)` or:

```sh
python3 -B /home/bridge-user/.local/lib/mcp-integration/godot_control.py recover
python3 -B /home/bridge-user/.local/lib/mcp-integration/godot_control.py recover --open-app
```

Recovery starts missing managed services, allows addon reconnect, and optionally
opens the configured project if there is no editor. It never kills an unmanaged
MCP, closes an app, restarts an editor or switches project. An existing but unresponsive
server is reported for diagnosis rather than forcibly terminated. Service restart
on MCP process failure is bounded by systemd start limits. Locks/port checks refuse
a duplicate managed backend; the app launch is serialized and checks existing Godot.

## Tunnel activation

Create the separate Godot tunnel in OpenAI Platform and associate the target ChatGPT
workspace. Save its runtime key outside the repository at
`/home/bridge-user/.config/mcp-integration/credentials/chatgpt-godot.key`, mode 0600.
Copy `ops/config/tunnel-client/chatgpt-godot.yaml.example` to
`/home/bridge-user/.config/tunnel-client/chatgpt-godot.yaml` and replace its tunnel ID.
Do not put the secret value in YAML, Git, logs or chat.

```sh
tunnel-client doctor --profile chatgpt-godot --explain
systemctl --user enable --now mcp-tunnel-chatgpt-godot.service
```

In ChatGPT create a separate developer-mode Godot connection with that tunnel.
Refresh the existing Bridge catalogue for `godot_recover`. Verify an actual
`godot_inspection_get_project_info` call from ChatGPT before claiming availability.
The installed Godot server advertises 18 default tools and manages its own toolsets;
its wire names are prefixed (for example `godot_health_check`, not `health_check`).
Official connection procedure: https://developers.openai.com/api/docs/guides/secure-mcp-tunnels

## Diagnostics and validation

```sh
mcp-services status godot
curl -fsS http://127.0.0.1:9090/live
curl -fsS http://127.0.0.1:9090/ready
journalctl --user -u godot-editor-mcp.service -n 60 --no-pager
/home/bridge-user/.local/share/pipx/venvs/godot-editor-mcp/bin/python -B /home/bridge-user/chatgpt-local-bridge/check_godot_live.py
```

Initial acceptance: four real MCP reads confirmed Warriors/Godot 4.7.2, no active
scene, and no scene tree. Seven isolated tests cover real readiness vs connection
alone, wrong-project handling, timeouts and conservative app recovery. Base suite:
110 tests passed (16 skips), plus 30 system-Python/GI tests passed.
With `BRIDGE_HOST_TESTS=1 BRIDGE_SYSTEMD_TESTS=1`, 110 tests passed
with 4 expected skips, plus 30 GI tests. Both new Bridge tools passed real stdio
MCP calls. The Bridge tunnel alone was restarted to load `godot_recover`; its
health/readiness returned 200 afterwards. Godot recovery returned `actions: []`
for the existing healthy editor.
Before changes, Unity had no reachable editor, Blender had no addon listener;
Browser MCP was available with 24 tools (extension session not yet verified).
These baseline limitations must not be presented as validated end-to-end integrations.

Final Browser acceptance: a real `browser_tabs(action="list")` MCP call succeeded.
Unity and Blender remain at the unavailable-editor baseline; their services were not restarted.

## Publication and tunnel identity

The user created `REPLACE_WITH_TUNNEL_ID`. The concrete profile
is stored in `ops/config/tunnel-client/chatgpt-godot.yaml` and installed under
`~/.config/tunnel-client/`. The runtime key is installed with mode 0600 and the tunnel is active. The ChatGPT connection remains pending.
The configured profile alone does not prove cloud reachability.
OBS recording/frame extraction and Godot integration are published together on
the existing principal branch `master`; no release/tag or installer changes.

## Tunnel activation verified

The runtime key was validated by file metadata without displaying it.
`tunnel-client doctor --profile chatgpt-godot --explain` passed. The Godot tunnel
was enabled at login and started; transport health/readiness and real editor
readiness returned 200. A completed successful control-plane poll confirmed
communication with OpenAI. Four real local MCP reads passed again, with 18 tools
and the same Warriors editor PID. This does not yet prove a ChatGPT tool call.

## Runtime catalogue and Flatpak binary recovery

The installed package supports `GODOT_MCP_DEFAULT_TOOLSETS`; the service now enables
inspection,runtime,editor,scene_edit,scripts,asset_import at startup. This preserves
the previously enabled categories and exposes play/stop/state before ChatGPT discovery.
Refresh the Godot plugin actions after deployment: a stale hosted catalogue can
omit tools even though local tools/list includes them.

`GODOT_MCP_GODOT_BIN` points to `~/.local/lib/mcp-integration/godot-flatpak`, a fixed
launcher using `flatpak run org.godotengine.Godot` with original arguments preserved.
This fixes native binary discovery for headless checks without installing another Godot.
The source launcher and updated service are included under ops/.

Real `godot_runtime_play_scene(scene_path="res://main.tscn")` returned playing=true,
confirmed by is_playing. Only the test session was stopped after video capture.
The editor remained open. No gameplay script or animation was edited.
