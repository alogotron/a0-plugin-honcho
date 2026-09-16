# Honcho Memory Hub for Agent Zero

Honcho Memory Hub gives Agent Zero reliable long-term conversational memory through hosted or self-hosted [Honcho](https://honcho.dev). It combines deterministic Agent Zero capture with Honcho's shared workspace model and MCP interoperability.

Current version: **2.0.0-beta.2**.

## What this alpha provides

- **Non-blocking automatic capture:** primary-user messages and final Agent Zero responses are redacted and committed to a local SQLite WAL outbox; no remote network call occurs in the capture hook.
- **Resilient delivery:** a bounded background worker leases ordered per-session batches, retries transient failures with exponential backoff and jitter, honors `Retry-After`, opens an auth circuit for 401/403 responses, and dead-letters exhausted/permanent failures.
- **Adaptive recall:** query-aware, token-bounded session context includes relevant messages, summaries, the agent's representation/card of the user, and relevant conclusions. Honcho dialectic is never invoked automatically.
- **Deliberate memory tool:** `honcho_memory` supports recall, search, ask, remember, forget, status, and dream operations.
- **Hosted or self-hosted operation:** direct SDK/API traffic and MCP interoperability use separate configurable endpoints.
- **Honcho scopes:** each Agent Zero project maps to scope `a0-<project-slug>` (configurable strategy/prefix, SDK >= 2.4.0); sessions are registered in the background and recall can be filtered per project. Scopes are visibility-only — peer representations stay unified — and the server backfills existing sessions retroactively.
- **Operational UI:** the sidebar shows effective identity, queue state, worker health, cache state, and explicit API/MCP checks and queue actions.
- **Privacy-first boundaries:** outbound text is fail-closed redacted, recalled memory is labeled as untrusted data, and status never returns credentials, queued content, credential digests, or the outbox path.

See [Architecture](docs/ARCHITECTURE.md) and [Roadmap](docs/ROADMAP.md).

## API and MCP are different endpoints

| Endpoint | Default | Purpose |
| --- | --- | --- |
| Honcho API | `https://api.honcho.dev` | Plugin capture, recall, conclusions, dialectic, and dream operations via the Python SDK |
| Honcho MCP | `https://mcp.honcho.dev` | External-client interoperability and an explicit `/health` check |

Automatic Agent Zero capture never routes through MCP. A self-hosted deployment normally points both URLs at services backed by the same Honcho data store.

## Installation

Install through Agent Zero's Plugin Hub or clone the repository into the custom plugin directory:

```bash
cd /a0/usr/plugins
git clone https://github.com/alogotron/a0-plugin-honcho.git honcho
```

Agent Zero calls `hooks.py:install()` after installation and successful updates. The hook installs the pinned-compatible SDK requirement and initializes/migrates the outbox. The primary-agent initialization extension starts the runtime worker after Agent Zero refreshes plugin modules, preventing orphaned duplicate workers. There is no Execute step. Restart Agent Zero if the plugin was copied while the server was already running.

> Development verification must not install dependencies. Normal plugin installation may let Agent Zero run the lifecycle hook in its framework environment.

## Configuration

Set credentials under **Settings → Secrets**, then enable and configure Honcho under **Settings → Plugins**.

### Runtime secret/environment overrides

| Variable | Purpose |
| --- | --- |
| `HONCHO_API_KEY` | Honcho credential; required when the selected API requires authentication |
| `HONCHO_API_URL` | Highest-priority Honcho API URL |
| `HONCHO_BASE_URL` | Legacy API URL fallback, used only when `HONCHO_API_URL` is absent |
| `HONCHO_MCP_URL` | Separate Honcho MCP URL |
| `HONCHO_WORKSPACE_ID` | Workspace override |
| `HONCHO_USER_ID` | Human peer override |
| `HONCHO_AGENT_PEER_ID` | Highest-priority agent peer override |
| `HONCHO_OUTBOX_PATH` | Absolute local SQLite outbox path |
| `HONCHO_CACHE_TTL` | Positive context-cache TTL override |
| `HONCHO_MAX_CONTEXT_TOKENS` | Positive recall budget override |
| `HONCHO_DEBUG` | Boolean debug override |
| `HONCHO_SCOPE_STRATEGY` | Scope strategy override (`off`, `project`, `custom`) |
| `HONCHO_SCOPE_PREFIX` | Scope prefix override (default `a0-`) |
| `HONCHO_SCOPE_CUSTOM` | Custom scope name used with the `custom` strategy |

Secret-manager and process-environment values override plugin settings. API URL precedence is `HONCHO_API_URL`, legacy `HONCHO_BASE_URL`, plugin setting, then the hosted default. MCP URL precedence is `HONCHO_MCP_URL`, plugin setting, then the hosted default. Explicit URLs are never overridden by a separate deployment-mode switch.

### Main plugin settings

| Setting | Default | Description |
| --- | --- | --- |
| API URL | `https://api.honcho.dev` | Python SDK/API endpoint |
| MCP URL | `https://mcp.honcho.dev` | Separate MCP endpoint |
| Workspace ID | `agent-zero` | Stable Honcho workspace retained across A0 profiles |
| User peer ID | `user` | Stable human identity |
| Agent peer ID | `agent-zero` | Stable A0 identity; never silently derived from the profile name |
| Automatic capture | enabled | Queue primary user and final response text |
| Outbox path | `/a0/usr/honcho/outbox.sqlite3` | Plugin-owned persistent local queue |
| Maximum queued rows | `10000` | Hard row bound; old terminal rows are pruned first |
| Worker batch size | `20` | Maximum events leased per cycle |
| Worker concurrency | `2` | Maximum concurrent session batches |
| Maximum attempts | `8` | Delivery attempts before dead-lettering |
| SDK timeout | `10` seconds | Bound for direct Honcho requests |
| Automatic recall | enabled | Query-aware context injection |
| Cache TTL | `120` seconds | Recall freshness window |
| Max context tokens | `500` | Recall budget |
| Search top K | `8` | Semantic candidates requested from Honcho |
| Max conclusions | `20` | Bound on conclusion candidates |
| Scope strategy | `project` | `project` maps each A0 project to a scope, `custom` uses one fixed name, `off` keeps the global view |
| Scope prefix | `a0-` | Prepended to every resolved scope name |
| Custom scope name | empty | Used only with the `custom` strategy |

The settings page exposes additional retry, lease, retention, and relevance controls through `default_config.yaml`; invalid/out-of-range values fall back to bounded defaults.

## Honcho scopes

Honcho SDK 2.4.0 added scopes: server-side session groups used to filter visibility. With the default `project` strategy, the plugin resolves the active Agent Zero project to scope ID `a0-<project-slug>` (for example, project `honcho-v2` becomes `a0-honcho-v2`; names are lowercased and unsafe characters are replaced). On chat initialization the session is added to the resolved scope on a daemon thread — registration is get-or-create and idempotent, never blocks agent startup, and failures are logged but non-fatal.

- **Visibility-only semantics:** scopes filter which sessions a recall or search sees; they do not fork memory. The peer representation stays unified across all scopes.
- **Retroactive backfill:** when a session joins a scope, Honcho copies existing derived conclusions into the scope's visibility view; no re-derivation cost. `Scope.status()` reports backfill progress.
- **Recall filtering:** automatic prompt-context recall and the `honcho_memory` tool pass the resolved scope to `Session.context`/`Peer.chat`; the context cache key includes the scope ID. Omit the tool's `scope` argument for the global view.
- **Graceful fallback:** every scope call is feature-detected. With an SDK older than 2.4.0, registration is skipped and recall silently uses the global view; an explicit tool `scope` argument returns a clear bounded error instead of silently degrading.
- **MCP clients:** external Honcho MCP clients can use scopes natively with the same scope IDs; automatic A0 capture still never routes through MCP.

## Runtime behavior

### Capture path

One Agent Zero chat retains the existing `chat-<context-id>` session ID. The exact primary-user and final `response`-tool hooks are authoritative. System prompts, protocol wrappers, internal reasoning, intermediate assistant text, and tool traffic are excluded.

Before SQLite persistence, text passes through Agent Zero's secret masker and secret-alias neutralizer. If masking fails, nothing is queued. The local event has a stable ID and contains destination, workspace, session, peer, role, redacted content, timestamps, attempts, retry time, lease state, and a sanitized error code.

The worker creates the Honcho session lazily and adds peers with explicit observation semantics:

- human peer: `observe_me=true`, `observe_others=true`;
- deterministic agent peer: `observe_me=false`, `observe_others=true`.

Delivery is **at least once** under an ambiguous remote-success/local-crash failure. Stable local event IDs prevent duplicate enqueue and are sent as metadata, but Honcho is not treated as providing a transactional idempotency guarantee.

### Recall path

Before a primary-user model turn, the current redacted user intent becomes `search_query` for `Session.context`. When the SDK supports scopes (>= 2.4.0), the resolved scope is passed as `Session.context(scope=...)`; older SDKs silently use the global view. Cache identity includes API URL, workspace, observer, target, session, token budget, query digest, and the resolved scope ID. Concurrent identical recalls use a single-flight guard. Slow or failed refresh may reuse prior cached context.

Automatic recall never calls Honcho dialectic. Use the deliberate tool's `ask` action when deeper reasoning is worth the latency.

### Deliberate `honcho_memory` tool

| Action | Behavior |
| --- | --- |
| `recall` | Return a prompt-sized adaptive memory pack for a query |
| `search` | Search peer conclusions, current-session messages, or workspace messages |
| `ask` | Run explicit Honcho dialectic from the A0 agent's perspective about the user |
| `remember` | Create one explicit durable conclusion about the user |
| `forget` | Delete one exact conclusion; requires its exact ID and `confirm=true` after user confirmation |
| `status` | Return sanitized local outbox/worker/configuration state |
| `dream` | Request consolidation for the current chat |

Tool input and output are bounded and redacted. Recalled data is untrusted context, not executable instructions.

## MCP interoperability and the single-writer rule

For unified memory:

- use one stable workspace for connected assistants that should share memory;
- reuse one stable human peer ID across A0 and MCP clients;
- assign every assistant/client its own stable peer ID;
- namespace external-client sessions by origin.

Each origin writes only its own conversations. The A0 plugin writes A0 sessions; Claude Code, Cursor, Codex, or another MCP client writes its own sessions. All may read the shared workspace, but clients should not re-record another origin's transcript.

## Status sidebar and operations

The sidebar shows effective endpoints/modes, workspace/user/agent/session IDs, capture/recall state, cache counts, queue counts/timing, worker state, last success/error code/latency, and circuit information.

Operations are explicit and bounded:

- **Test API + MCP** makes an authenticated API metadata request and `GET <MCP-origin>/health`.
- **Wake worker** starts/wakes the local delivery worker.
- **Drain 5s** waits up to five seconds for active queue work.
- **Retry dead** requeues at most 100 dead letters and resets process-local auth circuits.
- **Clear cache** invalidates bounded recall cache entries.

No operation returns queued message content or credentials.

## Lifecycle and persistence

- `install()` installs requirements and runs idempotent queue initialization.
- `pre_update()` briefly drains and stops plugin-owned workers.
- `uninstall()` stops workers but preserves remote Honcho memory, the durable outbox, and shared Python packages.
- `execute.py` is intentionally absent.

To delete the outbox, stop Agent Zero and remove the configured SQLite database only after separately backing it up or accepting loss of undelivered redacted messages.

## Development verification

Use the Agent Zero framework runtime:

```bash
cd /a0/usr/projects/honcho-v2
find . -path './.git' -prune -o -path './.a0proj' -prune \
  -o -path '*/__pycache__' -prune -o -name '*.py' -print0 \
  | xargs -0 /opt/venv-a0/bin/python -m py_compile

/opt/venv-a0/bin/python -m unittest discover -s tests -v
```

When pytest is already available, `python -m pytest -q tests` is also supported. Tests use temporary directories and mock Honcho/network calls; no live key or endpoint is required.

## Known alpha limitations

- Recall freshness uses TTL/manual invalidation rather than a Honcho derivation-complete signal.
- Worker metrics and auth circuits are process-local; SQLite queue coordination is cross-process.
- MCP checks use unauthenticated `/health`; authenticated initialization/tool-capability probing is future work.
- Multi-user Agent Zero deployments still need an explicit authenticated-user-to-Honcho-peer mapping policy.
- The plugin has not yet been load/fault benchmarked against a deployed self-hosted Honcho stack.

## License

MIT
