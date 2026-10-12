> NOBUBBLEWARP variant: this inherited document includes historical sandbox claims. Current host execution and file-tool policy are defined in [README.md](README.md); no command or decoder uses Bubblewrap.

# Command receipts, connection recovery and bounded reads

## Durable operation receipts

`OperationStore.reserve(request_id, scope, operation, parameters)` is reusable by
other mutations. This release wires it only to `run_command`; it does not claim
idempotency for file writes, application launches, desktop/browser input or editor
MCPs. The key is global to this store; reuse for another operation/scope is a conflict.

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
A failure to reserve refuses execution. A failure to persist completion preserves
the actual tool result with a warning and never asks the caller to execute again.

The independent SQLite registry defaults to
`~/.local/state/mcp-integration/bridge-operations/requests.sqlite3`. Directory and
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
8080 metrics every approximately 10 seconds. A poll older than 180 seconds is stale;
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
Other mutation tools do not yet hold this command recovery gate.

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
Ripgrep is required. Filesystem policy and Bubblewrap remain in effect.

## Activation and verification

Refresh the Local Dev Bridge actions in ChatGPT after deployment: `command_request`
is new and the file/search schemas changed. No other MCP needs a catalogue refresh.
Update only installed `supervise.py`, `mcp-services.py` and `bridge_watchdog.py` from
`ops/`, then load source changes by a guarded restart of the existing Bridge tunnel.

```bash
BRIDGE_HOST_TESTS=1 BRIDGE_SYSTEMD_TESTS=1 ./check.sh
.venv/bin/python -B check_reliability_mcp.py
.venv/bin/python -B check_memory_mcp.py
```

Watchdog fault decisions and budgets are tested with injected health/timestamps and
real filesystem locks. MCP acceptance uses real temporary MCP processes and Bubblewrap,
including retry after server restart. No fault is injected into the production tunnel.
A 2.4-MB text fixture returns a 2.4-KB default content page (1,000 times smaller);
that is content size, not a measured token or latency guarantee.
