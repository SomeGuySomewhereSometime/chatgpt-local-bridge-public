# External reviewers · Bridge Workspace 0.14.0

ChatGPT remains the implementer and integrator. The Core MCP adds external_review_catalog, external_review_request, external_review_status, external_review_decision and external_review_cancel. Desktop and existing tunnels are unchanged. Opinions never become command receipts or independent test passes.

## Configuration

The English Workspace has an External reviewers section. Plan project copy is the first/default tools option. Model selection uses click-to-toggle checkbox rows, per-purpose selected counts and a shared name/ID search. Filtering keeps selected models; explicit saved tool choices are preserved. Off is the default: existing goal prompts, checkpoint rules and Final review behavior remain unchanged, and requests fail before any provider call. Settings are versioned per conversation and copied to the task on Start. They can be changed during a task; changing policy invalidates prior required reviews. Saving current/future settings is atomic.

Manual only consults when requested. Automatic asks the primary assistant to justify useful consultations. Required checks actual successful jobs plus grounded evaluations for every selected model/stage before accepting a completed checkpoint/DONE. Plan review is requested before implementation; this is not a universal pre-edit gate over other tools/MCPs. Required stages and model choices are editable; no model is hard-coded as mandatory.

Purposes: plan/architecture, code/result, tests/edge cases, visual and targeted question. Strategies: critique, independent alternative, compare, verify a finding. Independent context should omit the primary proposed solution. The catalogue intersects current Zen availability with models.dev's zero input/output/cache prices. Unknown/paid/removed models and Jev are excluded. Refresh never calls a model. Dispatch refreshes the price catalogue again; no paid fallback. Documented image support is distinguished from a locally verified simple image probe.

## OpenCode runtime and tools

Free-tier direct HTTP requests and configurations hiding all native tools returned FreeTierError in real tests. Queries therefore use the official OpenCode CLI v1.19.0 in a separate review-runtime folder. Its release checksum was checked; existing user OpenCode installations/config/auth are not changed. No API key or Codex model call was needed in the successful free-model tests.

Every consultation uses built-in --agent plan, --pure, a private HOME/config/data/cache, no inherited keys/sockets, no MCPs, no session continuation, and the prompt supplied through stdin. --auto, interactive and attach are never used. Plan project-copy mode uses the native agent permissions without a Bridge permission override or post-processing tool allowlist. Native Plan tools may execute commands. Native editing restrictions still apply, including OpenCode’s exceptions for plan files. The other two modes keep restricted permissions and reject approval requests automatically. Tool permission errors and provider failures are never accepted as successful reviews.

Three tool choices:

- Tools off: inline packet only; any tool attempt is rejected and the consultation fails without a review. Native tool definitions remain advertised to satisfy the free-tier protocol, but execution is unavailable.
- Selected snapshots: read, glob and grep only over the selected excerpt/image folder. External directories and all other tools require permission and are auto-rejected.
- Tools on · Plan project copy (default when enabled): native Plan tools and permissions, including command execution for investigation/tests, using a disposable copy of project text files. No second Bridge tool allowlist is applied, and no agent prompt replaces the built-in Plan prompt. The live checkout is never the CLI working directory. Binary/generated/dependency files, .git/.opencode/provider configuration and obvious credential files are excluded. The copy is capped at 1,000 text files/40 MB, with omissions reported. Narrow selected snapshots remain available for larger projects. Project filenames remain usable as relative file:line references.

Context and selected-snapshot modes retain their previous command/edit restrictions. Native Plan is instructed to investigate without implementing source changes, switching agents or changing original projects, host settings or services. It may run diagnostic commands and tests and create temporary output/dependencies in its project copy/private directories. It is not a filesystem sandbox: shell commands can write and can access paths beyond their working directory. Do not describe native Plan as technically read-only or claim that the project copy contains processes. Existing user MCP/plugin connections are not inherited in any mode. Runtime upgrades need new permission tests.

## State, delivery and evaluation

Jobs are committed before launching independent bridge-review-*.service workers. They survive MCP/popup closure. Current snapshots and exact project/task IDs isolate concurrent tasks. Each request starts a new OpenCode session. Reusing the same request ID/parameters retrieves its jobs; changed parameters are rejected. Matching fresh context can reuse results; Repeat explicitly requests another opinion. An uncertain matching attempt must be explicitly excluded before another equivalent request. No automatic replay or silent model substitution.

Independent consultations run in parallel up to the selected concurrency (1–8). Limit 0 imposes no consultation cap; optional caps and per-consultation timeouts are configurable. State is queued/running/completed/failed/cancelled/uncertain. Cancelling/excluding never claims an already sent provider request was undone.

While a linked review is running, the loop waits without spending chat continuations. Results are retrieved through Core tools and attached to subsequent task-scoped Bridge results. They never need a separate reviewer chat. Automatic mode can finish despite a failed optional consultation if the primary reports the limitation; Required keeps its actual missing reviews unmet. Policy Off does not cancel a previously dispatched model request.

ChatGPT records each finding as corrected, rejected, out_of_scope or needs_context, with explanation and evidence for corrections/rejections. Every material finding needs assessment; a response with no findings still needs an evaluation summary. Findings/decisions are reports, not objective truth. Correcting code makes the old review stale; record the correction in the checkpoint and review the current code before Required completion.

Hashes of selected files/images and Plan project copies detect stale code. Task instruction changes and policy revisions also invalidate reviews. Evaluated plan choices remain historical after implementation. Only summaries appear in normal Workspace polling. View full review loads the complete response, all findings, supplied packet and evaluation on demand in bounded pages; polling preserves the open details. Ask ChatGPT to investigate finding queues a task-scoped instruction with the review/finding IDs; it sends no chat immediately and does not automatically accept the finding. A stopped/completed loop needs an explicit reviewed resume unless a further supported Bridge contact delivers the update. Full copy version manifests stay in local storage rather than bloating tool responses.

## Activation

Reload Bridge · ChatGPT Continuity in chrome://extensions, safely reload the ChatGPT conversation and Workspace, then refresh the Core plugin tools. No new tunnel or plugin. Enable External reviewers, Refresh free models, choose mode/stages/models and Save reviewer settings. With no current task, settings apply to its next goal. With a task, current/future scopes are shown.

## Validation

Unit/VM tests cover Off, versions, atomic settings, current price checks, stale files, request deduplication, uncertainty/exclusion, parallel jobs, cancellation, exact evaluations, Plan copying and UI controls. Real MCP tests cover Core registration, unchanged Desktop, Off rejection, Required checkpoint gates, result attachment and stale-file refusal using disposable provider fixtures. Original 0.13.0 provider tests confirmed MiMo's synthetic code finding, Muse's architecture comparison and blue/red image recognition, plus scoped snapshot read/search and the then-restricted Plan project reading. Native Plan validation is recorded separately for 0.13.1 below. No real ChatGPT message was sent for these validations. Hosted tool refresh and first enabled real-project run remain user activation checks.


## Native Plan validation · 0.13.1

201 Python tests and 11 JavaScript suites passed, plus real disposable Core/Desktop MCP checks. One actual free MiMo review ran a failing diagnostic test through bash and found the deliberate division defect without changing original/copied source. The runtime’s effective native permissions were inspected directly: commands are allowed, normal edit paths denied, and native plan-file exceptions remain. Every copied source file is checked after the CLI exits; altered/deleted/replaced source invalidates the review while newly generated test artifacts are permitted. The source-integrity check does not contain processes or prevent shell access to the host. All other native-tool availability follows the pinned runtime, model and installed dependencies. No real user task or ChatGPT message was run for these checks.


## Automatic context and transport · 0.14.0

Every enabled consultation attaches the exact project/task goal, criteria, retained non-cancelled instructions (including their delivery states) and latest checkpoint with its revision. Missing exact context is stated; another task is never substituted. Independent strategy retains requirements while omitting the previous solution checkpoint and diff to reduce anchoring. Additional caller-selected context/excerpts/images remain available.

A tracked Git diff uses an explicit diff_base or current HEAD in a repository rooted at the project. The stated HEAD baseline is not assumed to be the task's starting state. Selected paths limit the diff; generated/dependency/private credential paths are excluded. Untracked files are not part of Git diff; select them explicitly or inspect the Plan copy. Diff is bounded at 48,000 bytes, with omissions/truncation reported. It is omitted if necessary to preserve the remaining context within the chosen budget. Goal/checkpoint/excerpts are never silently shortened to make a packet fit.

Context budget is configurable in Workspace Limits & defaults: default 512 KiB, range 32–2,048 KiB. It governs the initial UTF-8 JSON packet; it is independent of the unchanged 1,000-file/40 MB Plan project-copy cap, image limits and provider context windows. An oversized packet is refused before model dispatch. The prompt is supplied by a file descriptor on stdin, avoiding Linux's single-argument size limit and pipe backpressure. Stable request IDs retain the original saved packet if files/checkpoints change after a lost response; changed explicit arguments still conflict. No automatic retry was added.

## Validation source versions · 0.14.0

For meaningful command checks, pass run_command(validation_paths=[relative files/directories], task_id=..., request_id=...). The persistent worker captures versions before and after execution, even if the MCP disconnects, then returns validation_reference. Source tracking failure is recorded separately and never changes a successful command into a replay instruction.

For a browser/manual check, call validation_evidence(action='begin', paths=..., request_id=...) before checking, then action='finish' with its validation_id, description and reported status after checking. This tool does not perform the check. Cite [validation:ID] in the checkpoint's existing evidence field. Command-linked checks verify their retained task-scoped receipt; browser/manual outcomes remain assistant-reported. The current task context and Workspace show monitored-source freshness separately from execution and reported outcomes.

A finished validation is immutable. Current status detects changed/added/deleted files in monitored directory scopes, changes during the check, unavailable scopes and later edits. Completed checkpoints and continuation completion feedback refuse an explicit stale/unfinished/non-passed version reference. Old checkpoints and ordinary [receipt:ID] evidence are not retroactively assigned coverage. Existing command calls without validation_paths preserve the original path.

Choose relevant source/assets/configuration, excluding test output. Directory monitoring excludes dependency/generated folders, credentials and symlinks; scope coverage remains explicit rather than inferred. Captures are capped at 40 scopes, 2,000 regular files/100 MB and 20 MB per file, and history at 2,000 records per project; oversized captures produce no partial proof. No file content is stored in validation manifests. A matching hash or successful exit does not prove test adequacy, gameplay correctness or aesthetic quality. Any source that changes and returns to identical bytes between captures can escape detection; these snapshots are not a filesystem monitor.

## Activation · 0.14.0

Reload the existing extension, then refresh the Workspace and safely reload ChatGPT when it has no active response or draft. Refresh the Core plugin tool catalogue to expose validation_evidence and updated run_command/external_review_request parameters. Core gains one tool; Desktop, model selection, native Plan permissions and existing tunnel IDs remain unchanged. Reviewer stages keep their previous semantics; no new strict Automatic category gate was added.
