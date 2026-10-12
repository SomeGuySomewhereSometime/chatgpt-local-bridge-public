# Restore the NOBUBBLEWARP copy

This folder is independent of the original bridge, currently stopped at the user’s request. Keep a copy of this folder before making further changes. Recreate its Python environment from `requirements.lock` if necessary, then run `check.sh` and the stdio acceptance scripts from README.md.

The local entry point is `start.sh`. No variant service or external tunnel profile was installed during creation. A later deployment must use the variant's own profile/service and port 8090. Do not run the historical installation commands in `docs/upstream/RESTORE.md` against the original installation.

Separate memory and receipt paths are in `bridge_config.json`. Preserve receipt databases when investigating an uncertain command; deleting them discards retry protection.


## Tunnel deployment — 2026-10-05

The variant now has its own profile in ~/.config/tunnel-client/chatgpt-local-bridge-NOBUBBLEWARP.yaml and unit in ~/.config/systemd/user/mcp-tunnel-chatgpt-local-bridge-NOBUBBLEWARP.service. Its service was started without enabling boot startup. Use the native panel to start it after login. The runtime credential remains a file reference to the existing credential; do not copy secrets into this checkout or diagnostic logs. Remote polling and both health endpoints were verified; plugin creation and hosted tool calls are still pending.

The original profile/unit/source were preserved. Its tunnel remains inactive and disabled. To return to the Bubblewrap bridge, first ensure the variant has no active/unknown operations and stop its connection through the panel, then run `systemctl --user start mcp-tunnel-chatgpt-local-bridge.service` and select the original plugin in ChatGPT. This starts the original for that session without changing its boot setting. No reinstall of the original bridge or shared MCP integrations is needed.
