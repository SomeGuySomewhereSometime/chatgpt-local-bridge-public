# Source publication audit

This repository starts with a new Git history containing only the sanitized source snapshot. The older private repository and its deployment/task history are not included.

The published files exclude live machine/user configuration, task and memory logs, installation inventories, real tunnel bindings, credentials, databases, recordings and generated runtimes. Example user paths and tunnel IDs are placeholders. Local setup is explicit and refuses to overwrite an existing configuration.

Default Gitleaks rules were used to scan the exact source snapshot and its initial commit. One exact-value/path exception permits the Chrome extension’s verified public key; it is not an API/private key and no other secret rule is disabled. Checksums and synthetic test tokens are distinguished from authentication credentials. No secret scanner guarantees that arbitrary prose contains no confidential information, so deployment metadata was also inspected separately.

Validation before publication: 214 companion Python tests, 11 JavaScript suites and disposable Core/Desktop MCP checks passed on the sanitized source. The runtime implementation also had a real free-model stdin test and persistent-worker checks; these remain separate from the first real user ChatGPT run.

Future changes must be audited before push. Keep bridge_config.json, actual tunnel settings, local logs and runtime data ignored. Never commit credentials or private deployment metadata.
