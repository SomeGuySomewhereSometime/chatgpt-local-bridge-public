# Bridge Companion 0.7 — Workspace, task updates and bounded feedback

Keep talking to ChatGPT in Chrome. This extension connects a local project to
one conversation, shows its state, and sends a message only after your review.
No new MCP plugin, tunnel, TCP listener or model API is needed.

## Setup / update

The native host is registered by `python3 companion/install.py`.
Load `companion/extension` with **Load unpacked** in `chrome://extensions`.
After updating files, press the extension card's circular Reload button, then
reload your ChatGPT tab only when no draft or response is in progress.
The extension ID and existing connections are preserved across this update.

## Using it

1. Open the intended ChatGPT conversation, then open **Bridge · ChatGPT Continuity**.
2. Under **Project connection**, enter an existing project folder. Task name or
   existing task ID is optional. Leave it blank to use the conversation title.
3. Select **Connect this conversation**. This does not send a message.
4. Write in **Message to send** inside the extension, then select **Review message**.
5. Check the destination and message, then select **Send to this conversation**.
   The message appears in your normal ChatGPT conversation.
6. **Message sent** means the extension observed the matching new user message.
   **Delivery uncertain** means check the chat yourself; the attempt is never retried.

Use **Project connection → Disconnect** to remove a connection without opening
another application. Unsent previews are cancelled; delivery receipts are retained.
After a successful delivery, **Write another message** starts a fresh composition.
A pending latest preview can be recovered when reopening the popup. If you close
it during a send, its durable receipt may remain uncertain even if delivery succeeded.

## Identity and checkpoints

The coordinator stores a stable internal `local-…` task ID independently of the
optional task name/reference. Existing associations are migrated without deleting
receipts or renaming user-entered task references. Changing the conversation title
or clearing the optional field does not replace that internal ID. Reconnecting to
a different project/reference cancels old unsent previews.

An existing Bridge task ID remains a reference, not an automatic checkpoint lookup.
Use the existing panel's Continuity page to copy a checkpoint into the extension's
message preview. There is no second project memory or copied chat transcript.
The companion page in Bridge Control is in English; unrelated older panel pages
retain their existing language. Page detection supports English and Portuguese ChatGPT.

## Storage and safety boundaries

Native Messaging permits only this extension's stable ID. The manifest key is
public identification, not a secret. The host runs for each request; no daemon is
needed. SQLite lives in `~/.local/state/bridge-companion` (directory 0700, DB 0600).
It stores associations, titles, optional references, observations and outgoing
message text. It does not store chat responses, cookies or account credentials.
No automatic retention cleanup yet.

Only connected tabs report state, every 5 seconds. Observations older than 20s
are disconnected, not completed. The adapter checks the visible composer and its
form, not hidden editors. Historical responses require a terminal marker, assistant
heading and visible regeneration action to count as ready; readiness is not proof
that the task succeeded. Unknown state, generation, drafts and detected approvals
block sending. Approval layouts not covered by the adapter remain unverified.

The durable sequence is pending → uncertain BEFORE any UI effect → observed_sent.
This prevents retries after a lost reply. A matching visible message is not proof of
server persistence. A failed insertion/send leaves any inserted draft for the user.
Tab ID changes require explicit disconnect/reconnect. Account identity is not read;
changing accounts requires reconnecting. Multiple agents are not part of this version.

## Validation

```
python3 -B -m unittest discover -s companion -p 'test_*.py'
node companion/test_content.cjs
node companion/test_popup.cjs
```

The prior 0.1 send was verified in the real Sueca conversation (one observed_sent
receipt and CONTINUIDADE-OK response). Version 0.2 UI/state flow is covered by fixtures
and native protocol tests; reload the extension before validating its new UI live.


## Goal / Loop (0.3)

1. Open the connected ChatGPT conversation and the extension.
2. In **Goal / Loop**, write the objective and its completion criteria.
3. Set **Maximum automatic continuations** (1–10, default 3).
4. Select **Review goal**, check the destination and scope, then **Start loop and send goal**.
5. The initial goal message is sent immediately. The loop only sends another message
   when the last completed assistant response ends with exactly one matching PENDING
   status. Each step has a fresh random identifier. DONE stops as agent-declared
   completion; NEEDS_USER, absent/ambiguous/old status stops for human review.
6. **Stop loop** in the extension or Bridge Control prevents further continuations.
   It does not cancel messages already in flight or commands already running.

The limit excludes the initial goal message. A limit of 3 permits at most four
messages total (initial + three automatic continuations). An uncertain dispatch
consumes its reserved continuation; it is never retried automatically. The counter
is durable across host restarts. Every new Start is an explicit new run and resets
the counter; Stop never auto-resumes.

The popup may be closed. The ChatGPT tab must remain open and continue reporting
observations. Reload pauses for an explicit verified observation reconnect; navigation, an unexpected user message, a draft, observed
approval, unknown/error state, or interruption stops the loop. A local transport
failure latches a halt until explicit Start. A heartbeat gap over 20 seconds while waiting is shown as a stale page observation;
it does not terminate the running goal. No continuation is dispatched from status
polling. A fresh snapshot must still pass document identity, message count, page
state and final-marker checks before dispatch. An in-flight delivery whose
observation expires is stopped and never replayed. Background suspension is not
treated as completion. Runs have no wall-clock maximum; the selected continuation allowance still applies. No background watchdog or
model API is involved, and nothing runs while Chrome is closed.

The extension inserts a small status protocol into the goal/continuation messages.
It reads only the final status marker from the latest assistant turn. The protocol
is a coordination signal, not trustworthy proof that the objective was achieved.
Tool approvals remain with the user. Unrecognized approval layouts and site DOM
changes can still require manual intervention. Page and document identity, user
message count, current draft and send permission are checked again before clicking.
Stop after that final check may be too late to cancel that single in-flight message.

Goals/run state are stored locally in `goal_loops`. Rebinding/disconnecting stops an
active run; old delivery receipts remain. The latest run per conversation is shown.
This does not add a separate project memory or implement multi-agent coordination.

Additional checks:
```
node companion/test_background.cjs
```
This drives the real framed native host against a temporary HOME through the
background code with simulated tab messages, including disconnection and Stop races.
It does not prove the complete Goal/Loop behavior in the user's live ChatGPT page.
Reload version 0.3 to perform that final browser integration test explicitly.


### Reload recovery (0.3.1)

Old content scripts stop their heartbeat when Chrome invalidates the extension context. Reloading the extension does not update code already injected into existing tabs: safely reload ChatGPT too. Historical Chrome extension errors can remain in its error log even after recovery. Regression: `node companion/test_context_lifecycle.cjs`.


### 0.3.2 multiline delivery

Composer and user-message checks use rendered text so paragraph/line-break boundaries survive verification. Initial loop delivery also updates the popup receipt. Existing uncertain attempts are never retried; preserved drafts must be handled before a fresh explicit Start.

## Bridge Workspace (0.4.0)

The full workspace opens in a normal Chrome tab. Select **Open full workspace** in
the extension popup, or **Open Bridge Workspace** on Bridge Control's ChatGPT in
Chrome page. You can also run `companion/open-workspace.sh` from this checkout.
After updating, reload the extension card and safely reload the ChatGPT tab.
Existing IDs, associations, goal state and receipts are preserved.

The sidebar lists open ChatGPT conversations and this Chrome client's existing
connections. Select the intended conversation, connect its project if necessary,
write a goal and select **Review goal**. The review includes conversation URL,
project and message budget. Only **Start loop and send goal** sends the initial
message. The selected conversation is checked again before dispatch.

The workspace shows the current run separately from the editable next goal,
continuation count, elapsed observation time, live page state, latest delivery
receipt and a durable activity journal. A model-declared completion is not
independent validation. Review the model's tests and evidence in ChatGPT.
**Stop loop** stays available during Start. Closing the workspace does not stop
a loop; the associated ChatGPT tab must remain open and responsive.

Direct messages have their own review and pending receipt recovery. Uncertain
attempts cannot be sent again. Draft fields are saved locally per conversation;
status polling does not overwrite what you are writing. These are composition
drafts, not task checkpoints. Multiple workspace windows can change a connection;
Start checks that the reviewed project/reference is still the current binding.

Closed-tab connections may be disconnected from the workspace. Opening a closed
conversation creates a new tab: refresh, disconnect its old association and
connect the new tab explicitly before sending. Nothing automatically rebinds or
resumes work. Accounts and MCP plugin availability are not detected by this panel.

The new native panel_list operation returns connections belonging to the same
Chrome client; content scripts cannot invoke workspace enumeration/disconnection.
Native Messaging, the extension ID, host permission and origin validation are
unchanged. The journal records state transitions and receipt events transactionally
in SQLite. It contains no copied assistant responses. Old receipts are displayed
as historical evidence; no event history is invented for earlier executions.
There is no automatic journal/draft retention cleanup in this release.

Validation includes `node companion/test_dashboard.cjs`, client-scoped journal
tests, and the real framed native host driven with simulated Chrome messages.
The responsive layout and review form were inspected in Chrome with sample data.
Activation of the updated extension and an actual goal sent from the new full
workspace still require live verification after the extension/page reload.

## Workspace 0.5: linked checkpoints, criteria, evidence and explicit resume

Goals started in the **full workspace** create a task in the existing Bridge
continuity database. No new MCP tool, tunnel, plugin or Chrome permission is
required. The native host reads the same `bridge_config.json` (or `BRIDGE_CONFIG`)
as the Bridge. Memory must be enabled. The basic popup loop remains available
without a linked task; use the workspace for these features.

Optional completion criteria are one per line (up to 12). If omitted, the model
is instructed to derive criteria and report checks. The goal message supplies the
exact project root and actual Bridge task ID. It asks the model to inspect session
context/tool availability, link commands using `task_id`, and explicitly save
`task_checkpoint` before its final response. These are model instructions, not
an independent tool-availability check on the other ChatGPT account.

The workspace reads the latest structured checkpoint and revision history.
**Reported checks** are declarations saved in that checkpoint. **Observed operation
receipts** are read-only metadata from Bridge: operation type, state, exit code,
timeout and ID. No command is executed by the panel or native host. No transcript,
command output or credential is copied. Missing receipts remain unavailable.
Dead owners are displayed as unknown without changing the receipt database.
Only the latest 20 linked receipts are displayed; unresolved state is checked for
all retained links (the existing Bridge retains up to 100 links per task).

For a linked goal, DONE is accepted only when the latest checkpoint is completed,
contains a summary and at least one passed reported check with an evidence
reference, has no pending/unverified work, covers each explicit criterion with a
matching check description, and has no unresolved linked operations. Otherwise
the loop stops at **Completion needs review**. This is a consistency gate, not
independent proof. Earlier command failures remain visible even after corrections;
a zero exit code alone does not establish gameplay or visual quality.

When the loop is stopped, **Review resume from checkpoint** previews the historical
context and exact destination. It sends nothing. **Resume loop and send context**
explicitly starts a NEW bounded run with the same Bridge task and a fresh message
ID/nonce; the selected allowance restarts. The former request
is never replayed. Changed task/project/reference/run/revision blocks a stale
review. Uncertain latest delivery additionally requires inspecting ChatGPT and
checking the acknowledgement box. This does not assert that the old message
was sent or repeat its operations. No automatic recovery of failed sends exists.

If no checkpoint was saved (for example the ChatGPT session lacked Bridge tools),
stop the loop, expand **Save a checkpoint manually**, press **Load current checkpoint
into editor**, fill summary and next step plus completed/remaining/unverified work,
and press **Save checkpoint**. It saves a versioned paused checkpoint labelled
user-entered; it does not send a message or mark checks as independently verified.
Concurrent saves use revision protection and cannot silently overwrite progress.
Project checkpoints are distinct from Codex personal memory.

Tests:

```sh
python3 -B -m unittest discover -s companion -p 'test_*.py'
node companion/test_dashboard.cjs
node companion/test_background.cjs
.venv/bin/python -B companion/check_workspace_mcp.py
```

The acceptance script uses the real MCP server/commands/continuity API with
isolated temporary project and databases. ChatGPT messages/page snapshots are
simulated; it does not prove the updated extension send path on the real page.
After installation, reload the existing extension, safely reload ChatGPT when
there is no draft/response in progress, and reload the workspace tab. Existing
associations and earlier receipts are preserved; previous basic goals are not
retroactively assigned a Bridge task.


## 0.6.0 — Current context and completion feedback

Chosen next step: make the existing loop use current task state, rather than add
an instruction queue or multi-agent orchestration. No new API, tunnel or plugin.

For linked Workspace tasks, every valid PENDING continuation reads the latest
checkpoint and retained operation receipts before reserving its outgoing message.
The message includes checkpoint revision, remaining work, unverified items, next
step, file references, reported validations and recent receipt metadata. Context
is historical data, not additional authority. Oversized context stops for review
rather than silently omit pending work. Popup loops without a linked Workspace
task retain their existing behavior.

A valid DONE with incomplete checkpoint/criteria or invalid receipt evidence may
receive ONE automatic completion-feedback message per run. It consumes the normal
continuation budget, has no wall-clock deadline, and uses the same
uncertain-before-send/no-replay mechanism. A second inadequate DONE stops for
review. NEEDS_USER, Stop, blocked/paused checkpoints, missing task access and active,
unknown or unavailable retained operations never trigger a repair message.
Existing stopped/completed runs do not restart. Explicit reviewed resume starts a
new run with a new budget, as before.

Automated command checks are instructed to cite `[receipt:REQUEST_ID]` in their
checkpoint evidence. References must belong to the same task's retained links and
resolve to a completed run_command with exit zero and no timeout. Missing,
malformed, foreign or failed references prevent completion and get specific
feedback when safe. Evidence without this syntax remains **model-reported**;
command references confirm execution, not test coverage, visual quality, semantic
correctness or freshness after subsequent edits. There is no independent model
reviewer or automatic test-command executor. A successful unrelated command
cannot establish that the criterion was actually tested.

Receipt retention is still controlled by Bridge ContinuityStore (latest 100 links).
All retained links are inspected; latest 20 metadata entries are included in the UI
and continuation. Hitting the retention window is disclosed, and references to
pruned receipts cannot be confirmed. Historical failures remain visible but do not
alone block completion: a later correction can pass, and stopping a test server can
produce a nonzero exit. No failed command is automatically re-executed.

Validation: Python regression suite, all five JavaScript suites, and
`check_workspace_mcp.py` with a disposable actual MCP task and command receipt.
The MCP acceptance now exercises a missing-checkpoint DONE, bounded feedback,
checkpoint correction through MCP, explicit receipt matching, completion and
reviewed resume across server lifetimes. Browser/ChatGPT responses are simulated
in these automated tests. A real ChatGPT run with 0.6 is still to be validated.


## 0.7.0 — Updates for this task

The Workspace now has an English **Updates for this task** card. Start a linked
Workspace goal, type an instruction, and press **Queue update** while ChatGPT is
working. This saves the instruction locally; the action does not send or interrupt
anything. It joins the next allowed continuation. Queueing after completion or
Stop requires an explicit reviewed resume. New tasks never inherit older updates.
Formal completion criteria remain unchanged; start a new reviewed task to change
them. This is not a tool approval or an urgent-stop channel.

Queued updates can be edited/cancelled. Versions and immutable delivery snapshots
are retained. Add IDs are idempotent across lost native replies, and edits require
the version currently reviewed by the user. Updates are bound to the current real
Bridge task, project/reference, client and conversation. Durable drafts are stored
per task in Chrome storage so switching conversations does not copy an instruction
to another task. Limits: 2,000 characters per update, 20 active instructions / 8,000
characters total, 100 history entries per task; no automatic retention cleanup.

Preparing a continuation reserves the included queued versions in the same SQLite
transaction as the message receipt. Their durable state becomes uncertain BEFORE
UI effects. The UI displays Sending only for the live matching dispatch, Sent when
the message was observed, and Delivery uncertain after an unresolved attempt. An
update added after reservation belongs to the next message. Previously sent
instructions accompany subsequent continuations and reviewed resumes as context;
this does not re-execute operations or create another delivery attempt for them.

A matching DONE cannot complete while instructions remain queued. If budget is
available and all safety checks pass, they are sent in a normal continuation. This
consumes the existing message limit and keeps the original deadline; it is not an
extra completion-feedback attempt. If the budget is exhausted, updates_pending
requires reviewed resume. NEEDS_USER, Stop, blocked/paused checkpoints, approval,
unknown operations and uncertain delivery keep their stopping behavior.

After sending a new update, a checkpoint newer than the revision at reservation is
required for completion. This prevents accepting the previous completion report
for new instructions. The model is asked to explain how the instructions were
addressed. A newer checkpoint alone is not independent proof of fulfillment.

Uncertain updates are NEVER automatically retried. Stop first and inspect ChatGPT:
**I found it in ChatGPT** records explicit user confirmation, or **Exclude from
future context** removes the instruction from later coordinator messages without
claiming that the original message did not arrive. Original uncertain receipts and
delivery snapshots remain unchanged. Resume still requires the existing explicit
acknowledgment of the uncertain message. Exclusion cannot erase instructions that
ChatGPT may already have seen; add a later correction when necessary.

Validation includes 85 Python tests, six JS suites (including update UI), real
native-host/background framing with queued updates on a reviewed resume, and
actual Bridge MCP acceptance in disposable state. The acceptance confirms a fresh
checkpoint after the update and a single original command execution. A local
synthetic HTTP preview was inspected in Chrome at desktop and 390px, Queue update
worked and neither layout had page overflow; screenshot capture/cropping by the
browser provider was inconsistent. Preview tab/server were closed and viewport
reset. No real ChatGPT messages were sent by these checks; a live 0.7 conversation
run remains pending until extension and safe page reload.

Workspace status includes the latest outgoing message text and older receipt metadata; full older message text remains in SQLite. This bounds native-response size as task context and instruction history grow.


## 0.7.1 — Delivery verification and temporary notices

A live queued-update test changed the Solo table from blue to yellow and saved checkpoint revision 3. The second message was delivered, but ChatGPT's rich composer serialized a bare URL in checkpoint JSON as `[URL](URL)`. Exact message comparison rejected this lossless transformation and the dispatch expired. Delivery confirmation now normalizes only redundant HTTP(S) Markdown links whose label and target are identical; it still requires one newly identified user message and the complete matching body. Changed labels/targets, changed text, missing identities and draft/Stop/context checks remain blockers. Confirmation polling also has a ten-second wall-clock bound when browser timers are throttled. No retry or automatic revival of stopped runs was added.

Successful action notices disappear after eight seconds and no longer overlap scrolled controls. Error notices remain visible. The criteria placeholder displays real line breaks. Existing uncertain receipts and queued-update history remain unchanged; inspect ChatGPT and use the explicit found/exclude controls before reviewed resume. Updating installed source does not reload Chrome; reload the extension, then safely reload ChatGPT and Workspace. The captured real message was checked read-only against the corrected comparator; the complete corrected browser send flow still needs a fresh live run.


## 0.8.0 — Automatic task updates through Bridge results

The Workspace Delivery selector applies to each new queued instruction:

- **Automatic · Bridge contact or next response** offers an instruction inside a supported Core tool result while the linked loop waits for ChatGPT. If no tool result reserves it, the existing bounded chat continuation carries it after the response.
- **Next response · chat continuation** retains chat delivery only.

Existing instructions retain their original Next response behavior. Queueing itself sends no message. Both routes reserve the same durable instruction inside one SQLite IMMEDIATE transaction. Once either route reserves it, the other cannot send it. Uncertain delivery blocks fallback and automatic re-offering; Stop, deadline, task/project isolation and reviewed resume remain in control.

Supported contacts are current explicit task_context (also the continuity part of get_session_context), task_checkpoint, task-associated run_command, and command_request/operation_request/command_session reads whose retained receipt uniquely links to the task. Reading arbitrary files or using another MCP does not deliver updates. Historical task_context/Markdown exports do not reserve them. A running command is not cancelled; this feature does not identify semantic subtask boundaries or interrupt ChatGPT.

Bridge delivery adds no user message and consumes no automatic continuation. A returned tool result is not confirmed receipt. Each instruction supplies an exact BRIDGE_UPDATE marker. The model includes that marker and its handling report in the completed entries of a newly committed task_checkpoint. Only a matching committed checkpoint acknowledges receipt; the Workspace then displays Acknowledged via Bridge with its revision. The handling report is model-reported, not independent proof of application. If the result was lost or ignored, the instruction remains unconfirmed until explicit inspection/resolution. No automatic retry or revival was added. If a task was previously completed, reopen it with an in_progress checkpoint before further mutations.

Code Mode forwards update metadata in its outer result even when JavaScript prints selected fields. It pauses before the next tool call in that batch so the model can read the instruction and plan remaining work. Already completed calls retain their receipts and must not be replayed. Tool argument schemas, extension ID, permissions and the existing tunnel/plugin configuration are unchanged.

Validation: 101 Python tests, six JavaScript suites, six actual Core MCP acceptance scenarios and the existing Code Mode/profile/reconnection acceptance, all using disposable state. These exercise both-route arbitration, checkpoint acknowledgement, late acknowledgement after Stop, legacy migration, isolated task links, Code Mode pause and unconfirmed delivery. The synthetic local Workspace preview was inspected in Chrome at desktop and a mobile viewport, including selector/queue labels and no console errors; it is not a real extension or ChatGPT test. Source installation does not reload Chrome. Reload the extension and safely reload ChatGPT/Workspace before a fresh live test. The previous 0.7.1 real-send confirmation fix and the new tool route can be validated together in that test.


## 0.8.1 — Recover an unacknowledged Bridge instruction

The live 0.8.0 test reserved the AMARELO correction, but the model reported seeing only awaiting_acknowledgement; resultado.txt remained AZUL. The exact loss point in the original hosted response could not be established. The diagnosed protocol defect was that subsequent reads hid the text/marker permanently after the first reservation.

Current task_context now returns the immutable original instruction text and the same acknowledgement marker for an outstanding Bridge delivery. This is retrieval of existing task data, not a new delivery reservation, chat message or command retry. It does not confirm receipt by itself. Chat deliveries are not exposed through this recovery route. Historical reads remain unchanged. Retrieval after Stop preserves the stopped state and does not claim newer queued instructions. get_session_context also exposes workspace_updates at the top level as well as continuity, reducing the chance that a caller misses the nested metadata. Pending-result instructions explicitly direct the model to task_context if the text is missing.

Tests simulate discarding the first tool payload and recovering it through a second real MCP call, then acknowledging and completing with one reservation and no continuation. 104 Python tests and actual MCP acceptance pass. This does not prove how the hosted model will handle the next live test. The existing failed test and its uncertain delivery are retained without automatic replay or forced acknowledgement.


## 2026-10-11 — Companion 0.8.2: quality and delivery clarity

Added proportional quality instructions to goals/continuations/resumes, separate reported work status from automatic continuation, and rich-message source reconstruction for exact delivery comparison. Live historical DOM showed a Markdown link consuming a JSON opening bracket in a resumed checkpoint. Preserve exact matching, identity/count checks, uncertain receipts and no automatic retries. Historical task states are unchanged. Validation results are recorded below after checks.

Validation: 104 companion Python tests and all six JavaScript suites passed, including rich-link JSON delimiter regression and altered-target rejection, stopped-loop/completed-work independence, native framing, draft preservation and no replay. Read-only comparison against the actual failed ChatGPT message matched all 7,843 normalized characters with the new reconstruction. Desktop preview inspected in Chrome using synthetic data. No new live message was sent; existing receipts and tasks were not rewritten. Extension reload and idle ChatGPT page reload required to activate 0.8.2; no tunnel/plugin changes. Backup: /tmp/bridge-example.


## Optional diagnostics (0.9.0)

In Bridge Workspace, use **Debug mode for this run** before Start or during the current run. The checkbox changes only local logging. It does not modify prompts, invoke model APIs, add continuations, interrupt tools or retry an uncertain message. With no current run the setting applies to the next run. Starting from Workspace uses the displayed choice; callers that do not explicitly opt in default to essential logging.

**Export diagnostics** downloads JSON for a selected recent run. It contains run/task/request identifiers, timestamps, fixed error categories, page/loop states, native processing or content delivery timings, versions, and numeric/boolean delivery checks. It excludes goal and conversation text, project paths, file contents, raw errors, command output and credentials. The existing activity/receipt/checkpoint stores are unchanged; they are not dumped into the export.

Essential state changes, delivery outcomes and authenticated failures are logged regardless of debug. Debug adds supported observe/tick/validation traces. Native rows are limited to 150 per run, 1,500 globally and 100 run headers; the selector shows up to 20 recent runs for the selected conversation. Expired runs are excluded from exports after seven days; old rows are pruned during subsequent logging. These are row/field limits, not a guarantee of a specific SQLite file size. Diagnostic writes use a zero busy timeout, and failures are ignored without changing operation results.

Chrome retains up to 200 metadata-only communication/delivery failures across conversations. The export filters to the selected conversation and separates matching run events from uncorrelated failures. After a service-worker restart some failures may lack a run identifier. If the native host is offline and the local conversation association still matches, export returns an explicitly partial browser-only report. A logging/storage failure or process termination can leave gaps. This is best-effort diagnosis, not evidence of task quality.

Exported files stay in the browser download folder until removed by the user. Nothing is uploaded. Reload the installed **Bridge · ChatGPT Continuity** extension in `chrome://extensions`, then reload the ChatGPT tab only when idle with no unsent draft, and refresh Workspace. No MCP catalogue, tunnel, native-host registration or Bridge service restart is required.

Validation: Python diagnostic tests and the existing companion suite; JavaScript regression suites including debug toggle/export and real framed native-host subprocess integration under a temporary HOME. Chrome controls and JSON download were checked in an isolated synthetic preview. Debug has not yet been exercised on a new real ChatGPT task after extension reload.


## Observation recovery and no duration cap (0.10.0)

Runs no longer expire after 30 minutes. The selected allowance of 1–10 automatic
continuations remains enforced, including reserved uncertain attempts. The 20-second
permission expiry for an in-flight dispatch remains; it prevents an unconfirmed
message from being replayed. Existing stopped runs are not revived by this update.

**Stop automatic messages** prevents future chat continuations. It does not cancel
the current ChatGPT response, terminate Bridge commands, or revoke already authorized
work. Prompts and recovered Bridge update instructions explicitly distinguish this
from a user request to interrupt the actual work or a real tool refusal.

Moving a tab between windows retains its tab identity and does not require a rebind.
For a reopened/reloaded page: refresh the full workspace, select that same conversation,
then click **Reconnect observation**. This can be done while ChatGPT is responding.
The action sends no chat message, changes no draft, resets no allowance and creates
no new task. Project, checkpoint revisions, receipt history, pending updates, nonce
and run identity are preserved. One unique reopened conversation is found by its
conversation ID; ambiguous copies require an explicit selection. The previous tab
must be closed or have navigated away before a different tab can take ownership.

An active waiting run retains its remaining automatic continuations only if the
fresh page identifies the latest user message and its entire normalized text matches
the retained observed_sent receipt. The fresh document/message-count baseline is
then adopted under the coordinator transaction. Reloaded observers pause until this
explicit verification. Missing/mismatched proof reconnects observation with automatic
messages off, ready for reviewed resume after inspecting the conversation. Drafts,
approvals, interruption and errors also disable continuation. Stopped, expired,
locally halted or uncertain dispatches are never revived by reconnect. Reconnect
itself never dispatches; a subsequent fresh observer tick must still satisfy all
normal final-marker, checkpoint, approval and allowance checks before sending.

The latest message proof is transported locally only for explicit inspection/reconnect;
it is not added to heartbeat payloads or diagnostic journals. Existing privacy
allowlists, permission expiry and no-replay rules remain. Extension permissions,
native host installation, MCP plugins and tunnels are unchanged.

Validation: automated lifecycle/native-host tests cover moving windows, closing and
reopening a tab, duplicate ownership, a response in progress, checkpoint/queue
preservation, expired dispatch permission, explicit Stop and continued budget enforcement.
Browser UI preview uses synthetic data; no real ChatGPT message is sent by these tests.

## Optional final review (0.11.0)

The full Workspace offers **Final review before completion**. The choice is included in the Start/Resume preview and persisted with the run. Fresh conversations default to on; previous runs keep their previous/off setting. The compact popup retains its existing behavior. Existing runs are not opted in by migration.

With review enabled, the first DONE that satisfies the existing completion checks (and has no queued updates or uncertain operations) reserves one separate review message. This uses one of the existing 1–10 automatic continuations; it adds no model API or separate agent. Exhausted allowance leaves an explicit “final review has not run” result, never an implied success. Review compares the delivered result with the goal and later instructions, reuses valid evidence, checks gaps, and fixes justified in-scope defects. Ordinary implementation checks must not be deferred. The prompt tells the model to reopen a completed task with an in_progress checkpoint before further operations when needed.

The coordinator records the starting checkpoint revision and accepts completion only with a newer completed checkpoint containing a `[BRIDGE_REVIEW:<run_id>]` completed entry with an explanation. Existing criterion, update-acknowledgement, receipt and uncertainty checks still apply. PENDING can continue verification/corrections within the same allowance and phase; it does not start another initial review. A missing review report uses the existing single bounded completion-feedback mechanism. Subsequent user updates remain in review context and require a newer checkpoint. Stop, NEEDS_USER, changed conversation, reconnect and uncertain-delivery rules remain in force. Explicit Resume starts a new run, with the displayed review choice and a fresh review identity.

The work panel shows pending/requested/reported review and its completion revision. Checkpoint history contains the model's findings and checks; activity records review dispatch and completion. Diagnostic metadata includes review enablement, phase and checkpoint revisions without adding prompt or file contents. This confirms the protocol and records the model's report: it does not independently prove review quality, test coverage, evidence freshness, or aesthetics.

Validation: 13 new Python review scenarios; full coordinator suite; JavaScript UI toggle/draft/preview/resume tests; real framed Native Messaging subprocess integration with simulated Chrome; actual Chrome rendering of a synthetic local workspace. A real ChatGPT final-review run remains to be validated after reloading the extension and Workspace.

## Complete delivery confirmation and compact continuation (0.12.0)

ChatGPT may expose only the first 10,240 characters of a collapsed long user bubble. The content script expands **only the identified new message** using its Show more/Mostrar mais control, once per attempt, and verifies the complete normalized text. It retains cumulative identities and exact full text/link checks. Prefixes, altered suffixes, changed identities and missing bodies do not establish delivery. No send is repeated. Diagnostic metadata records expansion and observed length, without storing message contents or identities there.

**Verify delivery · no resend** in the full Workspace reconciles the latest uncertain loop receipt. The background obtains fresh proof from the connected content script itself; UI-provided proof is ignored. It rechecks tab URL, ownership, run/request IDs, full text and the recorded candidate message identity. Legacy loop receipts can be reconciled by their unique retained nonce and exact full content. Matching a prefix is never sufficient. The check can read a response still in progress without modifying the composer. It marks the receipt observed_sent and confirms chat-delivered updates reserved in it. A stopped delivery can be recorded as completed only with fresh terminal DONE, valid checkpoint/review acknowledgement, no queued/uncertain updates or unresolved operations, and no explicit/local halt. All other cases keep automatic messages off. Verification sends no continuation and never changes a user Stop into permission to proceed. Further work uses an explicit reviewed Resume.

Work status displays a completed review report separately from a pending delivery confirmation. Delivery verification is recorded in the activity journal. A checkpoint report alone does not prove delivery or quality.

Continuation/resume context now selects a bounded checkpoint summary, next step, **full pending/unverified/recovery and failed checks**, invalid receipt evidence, receipt counts and three recent receipt summaries. It omits verbose successful history, file lists and passed-check details from the chat message, labels those omissions, and requires task_context before acting. Full records and all user updates stay retained. Completion decisions still inspect the complete records. Critical context above 6,000 characters refuses dispatch instead of silently dropping it. On the captured GameBox review context, the message shrank from 24,519 to 11,259 characters (54%); long-message expansion still matters.

Validation includes Python recovery/compact-context tests, simulated Chrome plus real framed native host, and actual Chrome DOM fixtures reproducing the collapsed message and verifying full text, corrupted suffix rejection, and no additional send on recovery. Live ChatGPT activation needs extension reload and a safe ChatGPT page reload; existing uncertain receipts remain untouched until explicit Verify delivery. The original Bubblewrap installation and game files are preserved.


## Optional external reviewers (companion 0.13.2)

English Workspace settings provide Off/Manual/Automatic/Required, editable free OpenCode models and context/snapshot/Plan project-copy tools. Core has five external_review_* tools; no new tunnel or plugin. Off preserves existing goal prompts and completion behavior. Reviewers use the dedicated official CLI with built-in Plan, independent jobs and explicit finding evaluations. Plan project-copy mode uses native tool permissions, including commands; Context and selected-snapshot modes remain restricted. Reviews are instructed to analyze without implementing changes. Native Plan command access is not a read-only sandbox. Required gates actual records, never prose alone. See ../docs/external-reviewers.md for operation, tested boundaries, limits and activation.

Model choices use labeled checkbox rows instead of Ctrl/Cmd multi-select. Search filters the visible choices without changing selections; each purpose shows its selected count. Tools on · Plan project copy is the first/default option, while explicit saved choices are retained. Save reviewer settings applies edits; selecting models does not itself call them.


## External reviewers and versioned checks · 0.14.0

Enabled reviewers receive the exact task context automatically. Limits & defaults includes a configurable Context budget (512 KiB default). View full review loads complete paginated details without another provider call; Ask ChatGPT to investigate queues an instruction in the existing task. A stopped loop requires reviewed resume to send a further chat message. Reviewer Off behavior and tool permissions are preserved.

For version-bound checks, the Core adds validation_evidence and optional run_command(validation_paths=...). Cite returned [validation:ID] in the existing checkpoint evidence field. The panel distinguishes current monitored source from reported outcomes and confirmed command receipts. Scope selection and test/visual coverage remain the assistant's responsibility. See docs/external-reviewers.md for limits and activation; the Core tool catalogue must be refreshed alongside extension reload.
