# Honcho v3 API — Endpoint Inventory

All routes nest under a workspace; the live API base is served as `/v2/workspaces/...` (v3 docs, v2 base path). Endpoint **names and methods** are verified from the API reference; some **paths/schemas** follow Honcho REST conventions — confirm against https://honcho.dev/docs/v3/api-reference when exact fields matter. Prefer the SDK over raw HTTP (the docs explicitly recommend it for auth, retries, types).

## Workspaces

| Method | Path | Purpose |
|---|---|---|
| POST | `/workspaces` | Get or create workspace (idempotent upsert by name) |
| POST | `/workspaces/list` | List all workspaces |
| PUT | `/workspaces/{ws}` | Update workspace metadata/config |
| DELETE | `/workspaces/{ws}` | Delete workspace (irreversible; sessions must be gone first) |
| POST | `/workspaces/{ws}/search` | Semantic/keyword search across the workspace |
| GET | `/workspaces/{ws}/queue` | Deriver/ingestion backlog status |
| POST | `/workspaces/{ws}/dream` | Schedule a dream (trigger consolidation) |

## Peers

| Method | Path | Purpose |
|---|---|---|
| POST | `/peers/list` | List peers in workspace |
| POST | `/peers` | Get or create peer (upsert) |
| PUT | `/peers/{peer}` | Update peer metadata/config |
| POST | `/peers/{peer}/sessions` | Sessions involving this peer |
| POST | `/peers/{peer}/chat` | **Dialectic** — natural-language query over the peer's representation |
| POST | `/peers/{peer}/representation` | Structured model of what is known about/by the peer |
| GET/PUT | `/peers/{peer}/card` | Get/set compact identity profile |
| GET | `/peers/{peer}/context` | Assembled context block for injection |
| POST | `/peers/{peer}/search` | Search that peer's messages/knowledge |

## Sessions

| Method | Path | Purpose |
|---|---|---|
| POST | `/sessions` | Get or create session |
| POST | `/sessions/list` | List sessions |
| PUT | `/sessions/{s}` | Update session |
| DELETE | `/sessions/{s}` | Delete session |
| POST | `/sessions/{s}/clone` | Fork conversation state |
| GET/PUT | `/sessions/{s}/peers` | Get / replace peer roster |
| POST/DELETE | `/sessions/{s}/peers` | Add / remove peers |
| GET/PUT | `/sessions/{s}/peers/{peer}/config` | Per-session peer flags (observe/observed) |
| GET | `/sessions/{s}/context` | Token-bounded window (messages + summaries) |
| GET | `/sessions/{s}/summaries` | Short + long rolling summaries |
| POST | `/sessions/{s}/search` | Search within the session |

## Messages

| Method | Path | Purpose |
|---|---|---|
| POST | `/sessions/{s}/messages` | Batch insert messages (triggers deriver) |
| POST | `/sessions/{s}/messages/list` | List messages (paginated) |
| GET | `/sessions/{s}/messages/{m}` | Get one message |
| PUT | `/sessions/{s}/messages/{m}` | Update message metadata |
| POST | `/sessions/{s}/messages/upload` | Ingest a file/document as messages |

## Conclusions (v3 — explicit fact store)

| Method | Path | Purpose |
|---|---|---|
| POST | `/conclusions` | Create derived facts/insights |
| POST | `/conclusions/list` | List conclusions |
| POST | `/conclusions/query` | Semantic retrieval of facts |
| DELETE | `/conclusions/{id}` | Delete a conclusion |

## Webhooks

| Method | Path | Purpose |
|---|---|---|
| GET | `/webhooks` | List webhook endpoints |
| POST | `/webhooks` | Get or create webhook endpoint |
| DELETE | `/webhooks/{id}` | Delete webhook endpoint |
| GET | `/webhooks/test` | Fire a test event |

## Miscellaneous

| Method | Path | Purpose |
|---|---|---|
| POST | `/keys` | Issue a scoped API key |

## Concept glossary

- **Workspace**: top-level tenant/namespace; isolation + search boundary.
- **Peer**: any entity (user, agent, actor); symmetric model, each accrues a representation.
- **Session**: one conversation/thread with an ordered message log and a peer roster (multi-peer capable).
- **Message**: one utterance attributed to a peer; insertion queues the async deriver.
- **Conclusion**: a first-class derived fact unit — queryable/listable/deletable.
- **Representation**: structured model of a peer's knowledge/traits; reached via dialectic chat or the representation endpoint.
- **Peer card**: compact identity/attributes summary for fast injection.
- **Session context / summaries**: token-budgeted window combining recent messages with short+long summaries.
- **Dreaming/consolidation**: background process (schedule dream) that turns raw messages into durable representations/conclusions; queue status reports backlog.

## SDK notes (honcho-ai Python / @honcho-ai/sdk TS)

- Object handles: `Honcho` -> `peer()` / `session()` with chained methods.
- `peer.chat(query)` over the dialectic endpoint; `peer.representation()`, `peer.card`.
- `session.add_messages([...])`, `session.messages`, `session.summaries`, `session.context(tokens=...)`, `session.delete()`.
- `client.workspaces()`, `client.peers()`, `client.sessions()`, `client.delete_workspace(name)`.
- List endpoints return iterators; models are typed (Pydantic in Python).
## Agent Zero endpoint contract

The plugin's Python SDK client uses the effective Honcho API URL
(`HONCHO_API_URL`, legacy `HONCHO_BASE_URL`, plugin setting, hosted default).
`HONCHO_MCP_URL` identifies a separate MCP service and must not be used as the
SDK base URL. Stage 1 probes only the MCP service's unauthenticated `GET /health`;
it does not register MCP tools.
