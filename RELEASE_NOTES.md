> NOBUBBLEWARP variant: this inherited document includes historical sandbox claims. Current host execution and file-tool policy are defined in [README.md](README.md); no command or decoder uses Bubblewrap.

# v0.8.0 — Persistent project memory and revised agent instructions

Adds durable project context to the installed Local Development Bridge. Notes and
checkpoints can be retrieved in a later conversation; command metadata survives
server restarts. This remains a private, host-specific source release, not an
installer. Browser MCP and Unity/Blender editor integrations require their existing
separate setup; no alternate Bridge distribution or installer is changed.

## Changes

- SQLite/FTS5 project notes and checkpoints, with `memory_save`, `memory_search`,
  `memory_get` and `memory_delete` MCP tools.
- Bounded memory retrieval in `get_session_context`, using the explicit project
  directory. Saved entries include source, timestamps and versions.
- Idempotent writes and optimistic update/delete checks, protected local storage,
  retention limits and an online backup command.
- Metadata-only history for synchronous and background commands. Exited-owner
  records become unknown; persistence errors do not trigger command replay.
- Balanced revision of global agent instructions: browser/desktop routing,
  selective memory use, asynchronous recovery and proportional verification.
  Existing authorization and safety boundaries are retained.
- Global instructions are returned once in session context; distinct project rules
  remain ordered and separate. The installed global file and recovery copy match.

## Activation and compatibility

The host configuration enables memory inside the existing protected
`/home/bridge-user/.local/state/mcp-integration/bridge-memory` directory. The actual
SQLite database, backups and saved project notes are not included in this release.
Python's SQLite build must support FTS5. There are no new Python dependencies.

See [MEMORY.md](MEMORY.md) for configuration, tools, limits, online backup and
rollback. On another installation, adapt the database path to a local directory
inside that installation's denied filesystem subtree. Set `memory.enabled` to false
to disable memory without deleting it. Retain existing configuration customizations.

Refresh/reconnect the ChatGPT Bridge catalogue to load the new tools and revised
initialization instructions. For deployment of this source, synchronize the global
instructions using `ops/config/global-agent-instructions.md`, reviewing any local
customizations first, then restart the Bridge when no owned commands are active.
All existing MCP function signatures are retained; session context now omits the
redundant global entry from `project_rules` because it is in `global_instructions`.

## Validation

Release checks: 84 tests with four expected skips, plus 30 system-Python/GI
tests passed. The reproducible checks are:

```bash
BRIDGE_HOST_TESTS=1 BRIDGE_SYSTEMD_TESTS=1 ./check.sh
.venv/bin/python -B check_memory_mcp.py
```

The MCP acceptance uses temporary storage and two server lifetimes, checking
checkpoint recovery, duplicate suppression, real synchronous/background commands,
project isolation, deletion and database protection through file tools/Bubblewrap.
Instruction checks cover global deduplication, ancestor order, symlinks, missing
files and current path policy.

The user separately reported successful memory retrieval in a new ChatGPT
conversation after following the manual test. This is user-reported acceptance,
not an automated measurement of model behavior or token savings. Physical-browser
acceptance from v0.7.0 was not rerun for this memory/instruction release.

## Known limits

Project identity is the exact canonical directory; subdirectories and moved
projects are not automatically grouped. Stored context is historical and needs
revalidation. Credential filtering is best-effort; do not save secrets. Deleting
entries does not erase independent backups. Agent recall is not guaranteed by
storage, and the wording revision is not a live behavioral benchmark.

Browser/desktop restrictions and the final-gap physical-click race documented in
[BROWSER_FALLBACK.md](BROWSER_FALLBACK.md) still apply.

## Earlier releases

v0.8.0 is added as the newest release. Existing releases, tags and release assets
are preserved unchanged. The previous release's source notes are also retained in
[docs/releases/v0.7.0.md](docs/releases/v0.7.0.md).
