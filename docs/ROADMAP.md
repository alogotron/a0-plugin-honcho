# Honcho Memory Hub Roadmap

## Stage 1 — compatibility foundation

Implemented in `2.0.0-alpha.1`:

- centralized hosted/self-hosted endpoint configuration;
- API/MCP separation and identity preservation;
- observation semantics, cache isolation, lifecycle hooks, status panel, and mocked tests.

## Stage 2 — reliable integrated memory

Implemented in `2.0.0-beta.1`:

- construction-safe extension guards that skip synthetic initialization traffic;

- durable bounded SQLite WAL outbox storing only redacted content;
- exact primary-user and final-response capture hooks with no remote hot-path write;
- process-safe leases, ordered batches, retries/jitter, `Retry-After`, auth circuits, dead letters, recovery, and bounded retention;
- sanitized queue/worker status plus wake, drain, dead-letter retry, and cache-clear operations;
- adaptive query-aware session context with token limits, cache isolation, and single-flight retrieval;
- compact native `honcho_memory` tool for recall, search, dialectic, conclusions, status, and dreaming;
- advanced configuration/settings UI and an operational sidebar;
- explicit single-writer and MCP interoperability contracts.

## Stage 2.1 — Honcho scopes

Implemented in `2.0.0-beta.2` (requires honcho-ai >= 2.4.0; feature-detected, graceful global fallback on older SDKs):

- A0 project to Honcho scope mapping with a configurable `a0-` prefix (`project` strategy maps project `honcho-v2` to scope `a0-honcho-v2`; `custom` and `off` strategies available);
- non-blocking, idempotent, failure-tolerant session-to-scope registration on chat initialization;
- scope-filtered recall and deliberate tool operations with the scope ID in the context cache key;
- sanitized scope status (strategy, resolved ID, SDK support flag, backfill status) in the API and sidebar.

## Stage 3 — production hardening candidates

These remain future work and require separate approval:

1. Honcho derivation/queue-aware recall refresh instead of TTL/manual refresh.
2. Cross-process metrics aggregation and OpenTelemetry-compatible export.
3. Consent presets, per-chat pause, export, and audited remote-retention workflows.
4. Authenticated MCP initialize/tool-capability probe and safe client-config generators.
5. Optional native Agent Zero MCP registration without duplicating the compact tool surface.
6. First-class instance/user identity mapping for multi-user Agent Zero deployments.
7. Load/fault benchmarks against a deployed self-hosted Honcho stack.
8. Cloud-to-self-host export/import assistance and documented rollback tooling.

Permanent cloud/self-host dual-writing is not planned because it creates divergent stores.
