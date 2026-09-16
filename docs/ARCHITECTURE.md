# Honcho Memory Hub v2 Architecture

## Implemented scope

Version `2.0.0-beta.2` is a hybrid memory integration:

- the Honcho Python SDK/API is the deterministic Agent Zero data plane;
- a local SQLite WAL outbox removes remote writes from the response hot path;
- a bounded worker batches and retries delivery;
- adaptive, query-aware Honcho context is injected before primary-user model turns;
- `honcho_memory` exposes a compact deliberate tool surface;
- the separately configured Honcho MCP URL is the shared interoperability endpoint for external clients.

MCP is not an extra hop for automatic A0 writes.

## Capture and delivery flow

```text
primary user message / final response
  -> normalize and classify
  -> Agent Zero secret masking (fail closed)
  -> neutralize executable secret aliases
  -> SQLite WAL outbox transaction
  -> return to the agent loop

background worker
  -> recover expired leases
  -> lease ordered same-session batches
  -> direct Honcho SDK/API request
  -> delivered | retry with jitter | dead letter
```

The hot-path hook performs no remote network operation. SQLite stores only redacted, bounded text. Empty, system, protocol, reasoning, intermediate assistant, and tool traffic are excluded; exact primary-user and final-response hooks are authoritative.

Delivery is **at least once**. A process can fail after Honcho accepts a batch but before SQLite records success. Stable event IDs prevent duplicate local enqueue and are sent as message metadata, but they are not claimed as a remote transactional idempotency guarantee.

## Queue and worker contracts

The plugin owns a schema-versioned SQLite database, normally `/a0/usr/honcho/outbox.sqlite3`, with mode `0600` and WAL coordination. Events contain destination and identity routing, session, peer, redacted content, attempts, next-attempt time, lease owner/deadline, state, and sanitized error code.

- State: `pending`, `leased`, `delivered`, or `dead`.
- Bounded rows/content prevent unbounded local growth.
- Delivered and dead rows have separate retention windows.
- Leases coordinate multiple processes and preserve session order.
- Batches from separate sessions may execute concurrently.
- 429 honors `Retry-After`; timeouts/5xx retry with exponential backoff and jitter.
- Permanent 4xx responses dead-letter; 401/403 also open a process-local destination circuit.
- Missing scoped runtime configuration defers without consuming an attempt.
- Operator retry resets attempts and process-local circuits; uninstall preserves the outbox.

The effective lease is always longer than the configured SDK timeout.

## Adaptive recall

Before a primary-user model turn, the plugin calls:

```text
session.context(
  peer_target=user,
  peer_perspective=agent,
  search_query=current redacted user intent,
  tokens=budget,
  summary=true,
  relevant + frequent conclusions
)
```

The resulting memory pack can contain recent relevant messages, a session summary, the agent's representation/card of the user, and semantically relevant conclusions. It is bounded and injected inside an explicit untrusted-data prompt boundary.

Cache identity includes endpoint, workspace, observer, target, session, token budget, a query digest, and the resolved scope ID. A per-key single-flight guard avoids duplicate concurrent retrieval. Failed refreshes may return a prior cached value. This alpha uses bounded TTL/manual invalidation rather than a Honcho derivation-complete signal; asynchronous writes therefore do not immediately invalidate recall.

Dialectic `chat` is never automatic. It is an explicit `honcho_memory` action.

## Honcho scopes

Honcho SDK 2.4.0 added scopes: server-side session groups that filter which sessions a context, search, or dialectic view sees. The plugin maps the active Agent Zero project to scope ID `<prefix>-<project-slug>` (default prefix `a0-`; project `honcho-v2` becomes `a0-honcho-v2`) under the `project` strategy, with `custom` and `off` alternatives. Scope IDs are slugified (lowercase, unsafe characters replaced, bounded length) and env overrides follow the standard precedence (`HONCHO_SCOPE_STRATEGY`, `HONCHO_SCOPE_PREFIX`, `HONCHO_SCOPE_CUSTOM`).

- On chat initialization the session is added to the resolved scope on a daemon thread; registration is get-or-create and idempotent, tolerates already-member responses, and is non-fatal on failure. Agent startup never blocks on scope I/O.
- Scopes are **visibility-only**: they do not fork the peer representation. Honcho retroactively backfills existing conclusions into the scope view; `Scope.status()` reports backfill progress.
- Automatic recall and the deliberate tool pass the resolved scope to `Session.context(scope=...)` / `Peer.chat(scope=...)`; the context cache key includes the scope ID.
- All scope use is feature-detected (`hasattr` checks). SDKs older than 2.4.0 skip registration and silently recall the global view; an explicit tool `scope` argument returns a bounded error instead.
- External MCP clients can use the same scope IDs natively; automatic A0 capture still never routes through MCP.

## Deliberate tool

`honcho_memory` exposes only:

- `recall`, `search`, `ask`, `remember`, `forget`, `status`, and `dream`.

Peer search queries the current agent's conclusions about the user. `ask` runs dialectic from the agent observer toward the user target. All blocking SDK calls run off the async event loop, input/output is bounded and redacted, and conclusion deletion requires an exact ID plus `confirm=true` after user confirmation.

## Identity and single-writer model

- Keep one stable workspace when clients should share memory.
- Keep one stable human peer ID across A0 and MCP clients.
- Give each agent/client a distinct stable assistant peer ID.
- Preserve legacy `chat-<context-id>` A0 session IDs.
- Namespace sessions created by external clients.

The A0 plugin records only A0 conversations. Claude Code, Cursor, Codex, or another MCP client records only its own sessions. All clients may read the shared workspace; none should re-record another origin's transcript.

## API versus MCP

| Endpoint | Owner | Purpose |
| --- | --- | --- |
| `HONCHO_API_URL` | Plugin data plane | SDK capture, recall, conclusions, dialectic, dream |
| `HONCHO_MCP_URL` | Honcho MCP sidecar/service | External client interoperability and `/health` visibility |

Hosted/self-hosted mode is derived from the normalized endpoint, not from a separate mode switch. Explicit URLs always win.

## Trust, privacy, and status

Runtime secrets are never included in cache keys except as a full SHA-256 digest used only process-locally. Public status omits digests, the outbox path, queued content, lease owner, and raw exceptions. API handlers retain Agent Zero's authenticated/CSRF-protected defaults.

The sidebar shows effective endpoints/identities, queue counts/timing, worker state, sanitized error codes, last success/latency, and bounded wake/drain/retry/cache-clear operations. API and MCP checks run only when requested.

## Lifecycle

`hooks.py` alone installs dependencies and controls update/uninstall shutdown. `install()` is rerunnable and initializes the schema. `pre_update()` drains briefly and stops workers. `uninstall()` preserves remote Honcho memory, the durable outbox, and shared Python packages. `execute.py` is intentionally absent.
