# Command receipts, connection recovery and bounded reads

## Durable operation receipts

`OperationStore.reserve(request_id, scope, operation, parameters)` protects commands
and this variant's structured file/app/process mutations, desktop input, OBS
start/stop and integration preparation. Editor/application MCP calls outside this
bridge require cooperative resource leases and their own verification.
The key is global to this store; reuse for another operation/scope is a conflict.

Supply a fresh `request_id` before launching a command. After a lost reply, reuse
that ID with identical parameters or call `command_request(request_id, cwd)`.
Retries never dispatch again, including after a Bridge restart. The canonical cwd,
operation and parameter fingerprint must match. Concurrent reservations are atomic.
Legacy calls without an ID still work and receive a generated ID, but cannot recover
that ID if the initial response is lost; they lack client-side retry protection.

State is committed before dispatch: `accepted`, then `running`, then `completed`.
`running` means dispatch may have begun, not proof that the executable started.
A dead owner or ambiguous failure leaves `unknown`; it is never reclaimed for replay.
Completion records process termination, not verification of the intended task effect.
A failure to reserve refuses execution. For commands, a failure to persist completion preserves the actual tool result
with a warning. Structured mutations report storage uncertainty and retain the
reservation instead. Neither case permits automatic execution with a new ID.

The independent SQLite registry defaults to
`~/.local/state/chatgpt-local-bridge-NOBUBBLEWARP/operations/requests.sqlite3`. Directory and
file modes are 0700/0600; the directory is covered by the effective denied-path policy.
No commands or stdout/stderr are persisted, only a fingerprint and receipt metadata.
Background output is available while its in-memory session exists; synchronous
replays and post-restart lookups explicitly report `output_available=false`.
Memory notes may be disabled without disabling operational receipts.

Retry keys are not automatically deleted. Capacity is 100,000 receipts; reaching it
refuses new dispatches and requires operator maintenance. Deleting the database
would discard retry protection, so do not do it to recover a lost response.
Tests use `BRIDGE_OPERATIONS_DIR` pointing to a disposable private directory.
The trusted local config can select `operations.directory`.

## Connection watchdog

Only the Bridge tunnel gets the new polling watchdog. It reads the existing local
8090 metrics every approximately 10 seconds. A poll older than 180 seconds is stale;
unavailable metrics produce an unknown connection state, not a claim of success.
There is a 180-second startup grace and three consecutive unhealthy observations
are required. Healthy means a live tunnel process, its MCP child and recent polling.
It does not prove a hosted ChatGPT tool invocation or editor readiness.

Recovery acquires an exclusive cross-process gate. Each shell request holds the
shared gate for its entire execution, including background jobs. Unresolved durable
receipts, a busy gate, unavailable storage or invalid recovery history defer recovery.
The exclusive gate prevents a new command entering between the idle check and restart.
Only the Bridge tunnel is restarted, by its existing systemd restart policy.
Independent application services are not stopped. Unexpected tunnel process death
and an explicit operator stop still have their existing lifecycle semantics.

The persistent recovery budget permits at most two attempts per ten minutes and
at least three minutes between attempts. Only safe polling-failure recoveries use
this budget; ordinary process crashes retain systemd's existing start limits.
Invalid budget history disables automatic recovery across monitor restarts.

`integration_status().bridge.connection` exposes `process_alive`,
`mcp_process_present`, `remote_connected`, `last_success_at`,
`last_success_age_seconds`, `consecutive_failures`, `recovery_state` and blockers.
The watchdog status file is private and updated atomically. Stale monitoring is
reported separately from a fresh direct metric probe.

Unknown commands intentionally block automatic recovery until their effects have
been inspected. The local operator can then acknowledge a specific unknown receipt:

```bash
.venv/bin/python -B operation_store.py resolve REQUEST_ID --scope /absolute/project/path
```

This changes only `unknown` to `resolved`, preserves the retry key and never executes
anything. It is not an automatic timeout handler and cannot resolve an active request.
Structured mutations also hold the shared recovery gate until their effects and receipt update finish.

## Bounded file and search responses

`read_file` defaults to 200 lines, with a 1,000-line/64-KiB page limit. Continue using
`next_byte_offset`, `next_line` and `expected_version=version`. Long lines can span
pages (`partial_line=true`); UTF-8 characters are never split. Empty pages have no
end line. File modification or replacement invalidates an expected version; start a
new read rather than joining incompatible pages. A first arbitrary line seek has an
8-MB/one-second scanning budget; byte-offset continuation avoids rescanning.
`full=true` explicitly retains whole-file reads for files up to 2 MB. Internal project
instruction loading uses full reads so rules are not silently truncated.

`search` now returns `matches` as structured `{path,line,text,excerpt_truncated}`
records, not the previous unstructured string. It defaults to literal matching;
set `literal=false` for regex and `glob` for file filters. Defaults: 50 matches;
maximum: 200 matches, approximately 60 KB of records and 10 seconds of collection.
The collector stops ripgrep when the result budget is reached rather than collecting
unbounded output first. Oversized/non-UTF-8 records or resource deadlines are reported
in `truncated`/`reasons`. Excerpts are limited to 500 characters.

Use `next_offset` for more matches. Each page rescans in sorted path order; it is a
live search, not a snapshot across changing files. Offset is limited to 10,000.
Ripgrep is required. File-tool path policy remains in effect; shell commands execute on the host without Bubblewrap.

## Activation and verification

The variant is not deployed to the hosted connection. After deploying it separately,
refresh its MCP catalogue for operation_request, resource_lease, project_environment,
integration_prepare and the added request/version/lease parameters. Do not restart
or replace the original Bridge as part of this variant's local verification.

```bash
BRIDGE_HOST_TESTS=1 BRIDGE_SYSTEMD_TESTS=1 ./check.sh
.venv/bin/python -B check_reliability_mcp.py
.venv/bin/python -B check_memory_mcp.py
```

Watchdog fault decisions and budgets are tested with injected health/timestamps and
real filesystem locks. MCP acceptance uses real temporary MCP processes and host systemd units,
including retry after server restart. No fault is injected into the production tunnel.
A 2.4-MB text fixture returns a 2.4-KB default content page (1,000 times smaller);
that is content size, not a measured token or latency guarantee.

## Atomic writes, leases and mutation receipts

See README.md for the current workflow. File mutations serialize across bridge
processes and use optional optimistic versions. write_file replaces one file
atomically, preserving its POSIX permissions; expected_version="missing" makes creation
exclusive. Unrelated host writers remain a possible final-check race.

Non-command receipts use workspace root as scope. A replay never reruns its action,
and returns metadata with ok/state/receipt rather than restoring output or images.
Partial GUI failures remain unknown. Known validation failures are failed. State
completed records the operation result, not independent task verification. The
registry migrates existing receipt tables additively, retaining retry keys and
command fingerprints; it does not persist arguments/content in result metadata.

Project environments and leases are stored under the same private operational
root. Leases are durable, expire on wall-clock time and are cooperative. Explicit
leases can span multiple calls; transient mutation locks serialize individual calls.
Commands check project reservations at dispatch; callers must renew while jobs run.
Tools in other MCPs and host processes do not enforce these leases automatically.

Tests never require or modify live desktop/editor state. Service preparations are
verified with injected states; actual hosted/application readiness remains distinct.
