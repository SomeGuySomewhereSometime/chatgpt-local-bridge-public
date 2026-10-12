# v0.7.0 — Explicit physical-click outcomes and desktop tooling

This release publishes the accumulated local Bridge changes since the previously
published master commit d4d96794aaa4a9bfddfff818aee08fe6054c2738. The repository
remains private and host-specific; this is a source release, not an installer.

## Changes

- Browser MCP to GNOME portal handoff with measured pointer calibration, DOM and
  window checks, and single-dispatch behavior.
- Explicit target_hit, target_missed and unconfirmed click outcomes. Failed or
  uncertain results retain receipts as structured MCP errors; no automatic retry.
- Semantic desktop queries, bounded accessibility scans, process/window identity,
  GNOME focus support and isolated text scratchpads.
- Bounded shell output, asynchronous command sessions, cancellation and improved
  best-effort diagnostic redaction.
- Reproducible check.sh and coordinate, orchestration, dispatch and browser guard
  regressions. Includes the exact installed GNOME 50 window-extension source.

## Migration and prerequisites

Physical desktop_browser_act clicks require click_scope="reversible". Calls
without it fail before any input. This declaration is only for low-impact,
reversible actions; use semantic tools for consequential operations. Hover-only
move does not require the declaration. Refresh the ChatGPT Local Dev Bridge
catalogue to load the new schema and instructions.

pointer_verified and hover_verified describe the preceding hover, not click
success. Inspect click_status, click_target_verified and dispatch_status.
Even target_hit still requires separate verification of the application effect.
Consumers must inspect structured receipts even when MCP isError is true.

The separate Browser MCP service and Chrome extension must already be connected
at 127.0.0.1:8931/mcp. Their installation is not bundled. The GNOME extension is
specific to Shell 50 and may require logout/login on first installation. Python
MCP dependencies use requirements.lock; GI desktop bindings use system Python.
Existing ops restoration assets cover the original integrations and are not a
complete installer for the separately configured Browser MCP.

## Validation

Executed for this release:

- BRIDGE_SANDBOX_TESTS=1 BRIDGE_SYSTEMD_TESTS=1 ./check.sh: 65 tests, 4 expected
  skips, plus 30 system-Python/GI tests passed.
- Isolated headless Chrome guard acceptance: 12 scenarios passed, including
  post-validation movement and preservation of the first missed-click evidence.
- Git whitespace validation and credential-pattern review completed. The only
  credential-URL match was a synthetic diagnostic-redaction test fixture.

User-reported live ChatGPT acceptance, not rerun during publication:

- Browser MCP extension communication and new click_scope catalogue confirmed.
- Missing scope refused, stable target clicked once, moved target refused.
- Final-gap physical race reproduced: target_missed, ok=false, MCP error,
  submitted receipt preserved, one outside click and no retry.
- Navigation destroyed readback: unconfirmed, ok=false, submitted receipt
  preserved and no retry. Post-navigation counters could not be recovered.
- Disposable about:blank tabs remained after Browser MCP close/index failures;
  this separate cleanup limitation was not bypassed with physical input.

## Known limits

DOM validation and OS dispatch are not atomic. The pointer may hit the wrong
place if the page/window changes in the final gap. This release reports the
outcome correctly when evidence exists; it does not eliminate the race.
Scope is a caller declaration, not automatic classification of page risk.
Do not bypass scope restrictions with raw input. Other input clients must stay
idle. Navigation or transport failures can make the outcome indeterminate.
Real zoom/focus synchronization and controlled loss of a dispatch receipt remain
separate acceptance work; a green unit/headless suite does not certify them.

## Previous versions

The previously published master commit is preserved by the new
baseline/published-before-v0.7.0 tag. This is an archival marker, not a newly
validated semantic release. Existing local v0.6.0–v0.6.6 tags are retained and
published unchanged. No existing tag or release is overwritten.
