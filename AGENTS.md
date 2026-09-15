# Honcho Memory Hub DOX

## Purpose

- Own the standalone Agent Zero Honcho plugin and its hosted/self-hosted memory integration.
- Keep deterministic capture, adaptive recall, deliberate memory tools, MCP interoperability, configuration, and lifecycle behavior explicit.

## Ownership

- `helpers/honcho_helper.py` owns effective configuration, SDK clients, identities, session semantics, capture policy, redaction, routing, recall, caches, and runtime orchestration.
- `helpers/outbox.py` owns the durable bounded SQLite WAL queue and schema migrations.
- `helpers/worker.py` owns leases, ordered batches, retries, circuit state, dead letters, and process-local worker lifecycle.
- `extensions/python/` owns primary-user/final-response capture, runtime startup, and prompt-context injection.
- `tools/honcho_memory.py` and its prompt own bounded deliberate memory operations.
- `api/honcho_status.py` and the WebUI own sanitized status, connectivity checks, and bounded operator actions.
- `hooks.py` exclusively owns dependency installation and update/uninstall lifecycle behavior.
- `docs/` owns architecture and roadmap; `README.md` owns installation and operator guidance.

## Local Contracts

- Do not write to or depend on `/a0/usr/plugins/honcho` during development or verification.
- Use `/opt/venv-a0/bin/python` for framework syntax/import checks. Do not install dependencies during development verification.
- Effective endpoint precedence is `HONCHO_API_URL`, legacy `HONCHO_BASE_URL`, explicit plugin config, hosted default. `HONCHO_MCP_URL` similarly overrides explicit MCP config.
- Secrets/process environment override explicit plugin config. Never expose credentials, key fragments/digests, queued content, raw sensitive errors, or the outbox path through status/tool output.
- Runtime capture and recall use the Honcho Python SDK/API. MCP remains a separate external interoperability endpoint; automatic capture never routes through MCP.
- Preserve configured workspace and peer IDs. `HONCHO_AGENT_PEER_ID` is the highest-priority agent identity override.
- Automatic capture stores only already-redacted primary-user and final-response text. Redaction failure is fail-closed.
- Outbox delivery is at-least-once under ambiguous remote-success/local-crash failures. Preserve per-session order and stable local event IDs.
- Context cache identity includes API endpoint, workspace, observer, target, session, token budget, and query digest. Automatic recall never invokes dialectic chat.
- The A0 plugin writes only A0-originated sessions; each external MCP client writes only its own namespaced sessions.
- Lifecycle setup/update/uninstall belongs only in `hooks.py`. Uninstall preserves remote memory, shared Python packages, and the durable outbox.

## Work Guidance

- Mock Honcho and network calls in tests; never require live credentials.
- Update architecture, roadmap, README, settings copy, sidebar copy, prompt, and skill wording when runtime behavior changes.
- Keep operations bounded. Destructive conclusion deletion requires an exact ID and explicit confirmation.

## Verification

- Compile changed Python with `/opt/venv-a0/bin/python -m py_compile`.
- Run focused standard-library unit tests without live credentials.
- Parse YAML, inspect `git diff --check` and the exact diff, scan for credentials, remove generated caches, and ensure `execute.py` remains absent.

## Child DOX Index

No child `AGENTS.md` files. This contract applies to the full project tree.
