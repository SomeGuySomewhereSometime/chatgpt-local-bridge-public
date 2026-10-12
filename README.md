# ChatGPT Local Bridge NOBUBBLEWARP · 0.14.0

A local MCP bridge and Chrome companion for using a normal ChatGPT conversation to work with files, commands and optional application integrations on your own computer.

Core exposes development tools, persistent command sessions, receipts, project coordination, Code Mode and versioned task checkpoints. Desktop is a separate MCP surface for application/accessibility/browser/OBS operations. The Chrome Workspace associates a project with a conversation, coordinates bounded continuations and task updates, and shows work, delivery and validation as separate states.

Optional external reviewers use currently confirmed free OpenCode models. ChatGPT remains responsible for implementation and assessment. Reviewers receive the exact task context and selected material, with native Plan tools in a disposable project copy when that mode is selected. Full responses and finding investigations are available in the Workspace. Version-bound checks monitor selected files without claiming test coverage or aesthetic quality.

## Execution model

This variant runs commands as the host OS user, **without Bubblewrap**. File-tool paths, receipts, timeouts, leases and native desktop consent remain, but arbitrary shell commands are not contained by those path checks. Native OpenCode Plan commands are also not a filesystem sandbox. Use this only on a computer/workspace where you intend to grant those capabilities.

## Local setup

Requires Linux, Python with venv support and a user systemd session. Desktop integrations have their own prerequisites; the native panel uses GTK/PyGObject.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
python3 ops/configure-local.py
export BRIDGE_WORKSPACE="$HOME"
./start.sh
```

`configure-local.py` creates the private, ignored `bridge_config.json` from `bridge_config.example.json` and refuses to overwrite an existing configuration. Review applications, filesystem policy, memory/receipt storage and desktop settings before use. The example has Desktop disabled. Starting the bridge provides stdio MCP; it does not create a tunnel or a ChatGPT plugin automatically.

Configure your own MCP client or tunnel. Service/configuration samples contain placeholders; replace the example user paths and tunnel ID locally. Keep real tunnel configuration and credentials outside Git. The optional native panel is documented in [interface/README.md](interface/README.md).

## Chrome Workspace

```bash
python3 companion/install.py
```

Load `companion/extension` as an unpacked extension in `chrome://extensions`. Read [companion/README.md](companion/README.md). Connecting a project does not enable MCP plugins or send a chat message. Reloading the extension requires a safe refresh of the ChatGPT page when no draft/response is in progress.

## Optional free reviewers

The 178 MiB platform binary is not included in Git. For the pinned official Linux x64 runtime:

```bash
python3 review-runtime/install.py
```

The installer verifies archive and binary SHA-256 against `review-runtime/release.json`, refuses replacement of an existing runtime and sends no model request. Reviewer configuration is Off by default. See [docs/external-reviewers.md](docs/external-reviewers.md) for modes, free-model eligibility, tools, context budgets, validation and activation.

## Checks

For isolated companion tests (no real user projects/model calls):

```bash
BRIDGE_REVIEWS_DIR="$(mktemp -d)" BRIDGE_COMPANION_DIR="$(mktemp -d)" BRIDGE_VALIDATION_DIR="$(mktemp -d)" \
  .venv/bin/python -B -m unittest discover -s companion -p 'test_*.py'
for test in companion/test_*.cjs; do node "$test"; done
```

Further MCP and host lifecycle checks require their documented disposable environments. Test passes are distinct from successful provider calls, refreshed hosted tool catalogues and real browser/device behavior.

## Source privacy

Local configuration, task/memory logs, installation inventories, databases, recordings, credentials and generated runtimes are excluded from the source snapshot. Paths in examples are generic. The manifest contains a **public** Chrome extension key to preserve its ID; it is not an API/private key. `.gitleaks.toml` permits only that exact public value, with other default secret rules retained.

The runtime installation can keep its own local configuration independently of this sanitized Git source. This repository starts with a new, audited Git history; earlier private deployment/task history is not included. Audit future files and commits before push. See [PUBLICATION.md](PUBLICATION.md).
