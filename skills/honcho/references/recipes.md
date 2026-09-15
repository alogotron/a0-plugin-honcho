# Honcho Recipes

Use these inside the Agent Zero framework runtime, where the plugin helper can
resolve settings without duplicating precedence or exposing credentials.

```python
from usr.plugins.honcho.helpers import honcho_helper as h

cfg = h.get_effective_config(context)
client = h.get_client(context, config=cfg)
if client is None:
    raise RuntimeError("Honcho is not configured")
user = client.peer(cfg.user_peer_id)
agent = client.peer(cfg.agent_peer_id)
```

For a standalone operator script, use `/opt/venv-a0/bin/python` and provide
`HONCHO_API_KEY`, `HONCHO_WORKSPACE_ID`, peer IDs, and optional API URL through
the process environment. Resolve the API endpoint as `HONCHO_API_URL`, then
legacy `HONCHO_BASE_URL`, then `https://api.honcho.dev`. Do not use
`HONCHO_MCP_URL` as the SDK base URL and never print credentials.

## Dialectic recall

```python
answer = user.chat("Summarize this user's preferences and constraints.")
```

Use a focused question when the user asks about one fact.

## Exact message search

```python
for message in user.search("deployment decision"):
    print(getattr(message, "content", message))
```

Scope to `client.session("chat-<id>").search(...)` when one conversation is known.

## Representation and card

```python
representation = user.representation()
card = user.card
```

Confirm whether `card` is a property or method against the installed SDK.

## Token-bounded session context

```python
session = client.session("chat-<id>")
context_block = session.context(
    peer_target=cfg.user_peer_id,
    peer_perspective=cfg.agent_peer_id,
    tokens=800,
)
```

The explicit perspective matches Stage 1 automatic retrieval.

## Conclusions

Conclusion SDK surfaces can vary. Inspect the installed SDK and prefer its typed
methods; otherwise use the API routes in `api-reference.md`. Create or delete a
conclusion only when the user explicitly requests a durable fact change.

## Queue and consolidation

```python
status = client.queue_status()
client.schedule_dream()
```

Verify method signatures against the installed SDK before passing filters.

## Status and health

Use the plugin status endpoint for effective local configuration. The explicit
`ping` action performs an authenticated API request and a separate
`GET <HONCHO_MCP_URL>/health` probe. MCP health does not prove API authentication,
and API success does not prove MCP availability.

## Backup before destructive operations

Before deleting sessions, conclusions, or workspaces, export the exact resources
through the configured API client and verify the backup. Workspace deletion is
irreversible, and reconnecting to the deleted workspace may recreate it. Verify
absence through a different workspace client. Never treat plugin uninstall as a
remote-memory deletion operation; uninstall intentionally preserves remote data.
