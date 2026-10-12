> NOBUBBLEWARP variant: this inherited document includes historical sandbox claims. Current host execution and file-tool policy are defined in [README.md](README.md); no command or decoder uses Bubblewrap.

# LibreSprite independent MCP

The installed `libresprite-mcp==0.1.3` owns `run_script`, `docs://reference`,
`docs://examples` and its prompt. No LibreSprite domain tools were added to Bridge.
`libresprite_recover` is lifecycle only. The original `mcp.js` and all documents
remain untouched. The user's Disconnect/Connect action re-established polling.

## Runtime and reproducibility

- Application: `/home/bridge-user/Applications/LibreSprite-1.1/LibreSprite-x86_64.AppImage`.
- Environment: `/home/bridge-user/Applications/libresprite-mcp-0.1.3/`.
- Script: `/home/bridge-user/.config/libresprite/scripts/mcp.js`.
- **MCP must remain 1.30.0 / below 2**. The entrypoint refuses a different version;
  `ops/libresprite-requirements.lock` pins `libresprite-mcp==0.1.3` and `mcp==1.30.0`.
- To restore packages into that environment, use its Python with
  `-m pip install -r /home/bridge-user/chatgpt-local-bridge/ops/libresprite-requirements.lock`.
  Do not install/upgrade unpinned MCP in this environment.

## Services and ports

| Service | Role | Address |
| --- | --- | --- |
| `libresprite-mcp.service` | Existing package, HTTP transport adapter | `127.0.0.1:9091/mcp` |
| Same process, original relay | mcp.js polling | `localhost:64823` |
| `mcp-tunnel-chatgpt-libresprite.service` | Separate OpenAI tunnel | health `127.0.0.1:8085` |
| `libresprite-app.service` | Optional conservative AppImage launch | No network port |

MCP and tunnel start at login. The application is never enabled at login and never
restarted by recovery. Locks and port checks refuse a duplicate managed server.
The existing AppImage instance is reused without selecting or changing a document.

Tunnel: `REPLACE_WITH_TUNNEL_ID`.
Runtime key: `~/.config/mcp-integration/credentials/chatgpt-libresprite.key`, mode 0600,
not in Git. Profile: `~/.config/tunnel-client/chatgpt-libresprite.yaml`.

## Readiness and pending work

`9091/live` confirms the MCP process and relay thread. `9091/ready` needs a recent
real, uniquely identified `console.log(app.version)` response through the original
relay and mcp.js. An open port alone never passes it. Status distinguishes MCP,
relay, application PIDs, `script_connected`, pending/busy state and tunnel transport.
The result expires after five seconds. Health waits at most four seconds, queues
only one outstanding diagnostic and never duplicates that pending request.

The adapter serializes the original proxy's `run_script` calls, including health,
and moves the original blocking tool off the HTTP event loop. At 25 seconds a tool
call reports an unknown outcome; its original relay request remains pending. It
is not replayed, replaced or cancelled. Recovery does not restart a live server
with pending work. This preserves the upstream script behavior and document state.
A temporarily busy tool can make readiness conservative/negative.

The tunnel client's native `/healthz` and `/readyz` describe transport, not the app.
`integration_status.libresprite.tunnel_ready.ok` combines real integration readiness
with raw `tunnel_transport_ready`. ChatGPT availability remains unverified until a
call from the separately created LibreSprite connection succeeds.

## Diagnosis and recovery

```sh
mcp-services status libresprite
curl -fsS http://127.0.0.1:9091/live
curl -fsS http://127.0.0.1:9091/ready
curl -fsS http://localhost:64823/ping
journalctl --user -u libresprite-mcp.service -n 60 --no-pager
python3 -B /home/bridge-user/.local/lib/mcp-integration/libresprite_control.py recover
```

Use `recover --open-app` or Bridge `libresprite_recover(open_application=true)`
only when opening the app is wanted. If the script is disconnected, open mcp.js
in LibreSprite and click Connect. Recovery never reloads scripts, closes apps,
changes documents or automatically repeats an uncertain user script.

One real read-only MCP acceptance (run when no script is pending):

```sh
/home/bridge-user/Applications/libresprite-mcp-0.1.3/bin/python -B /home/bridge-user/chatgpt-local-bridge/check_libresprite_live.py
```

Confirmed output: `MCP_TEST_VERSION=1.1-dev`. The original one-tool catalogue and
both documentation resources were also verified. Six focused tests cover pending
probe deduplication, response matching/freshness and conservative recovery.

## ChatGPT final test

Create a separate LibreSprite MCP plugin using the tunnel ID above, and refresh
the Local Dev Bridge catalogue for `libresprite_recover`. Ask ChatGPT to inspect
`integration_status`, then use LibreSprite's own `run_script` with
`console.log(app.version)`. No document edits are needed. Local readiness alone
is not proof that the ChatGPT connection was created successfully.

## Final local validation

Host-enabled check.sh passed 116 tests (4 expected skips) and 30 GI tests.
Both new Bridge operations passed real stdio MCP calls. LibreSprite tunnel polling
completed successfully against OpenAI; combined readiness is positive. Only the
Bridge tunnel was restarted to load the new lifecycle tool. App PIDs 133841/133847
were preserved. Godot and Browser passed real MCP read operations again. Unity and
Blender remain at the pre-existing unavailable-editor baseline; neither was restarted.
`git diff --check` passed. The ChatGPT plugin invocation remains pending user test.
