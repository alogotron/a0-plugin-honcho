<!-- SPDX-License-Identifier: Apache-2.0 -->

# Honcho Plugin for Agent Zero

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![SDK: honcho-ai](https://img.shields.io/badge/SDK-honcho--ai_v2.x-purple.svg)](https://pypi.org/project/honcho-ai/)
[![Agent Zero: v0.8+](https://img.shields.io/badge/Agent_Zero-v0.8%2B-green.svg)](https://github.com/agent0ai/agent-zero)

Connect [Agent Zero](https://github.com/agent0ai/agent-zero) with
[Honcho](https://honcho.dev) — a conversational-memory platform that gives
your agent **persistent user context** across sessions.

> **Disclaimer:** This is a **community-built integration** and is not
> officially maintained or endorsed by [Plastic Labs](https://plasticlabs.ai),
> the creators of Honcho. Use of the Honcho name and service is for
> descriptive purposes only.

---

## ⚠️ Privacy & Data Flow Disclosure

**Before enabling this plugin, please read this section carefully.**

This plugin sends data to a **third-party cloud service** (Honcho, operated by Plastic Labs, Inc.).

### What data is sent

| Data | Destination | Purpose |
|------|------------|--------|
| **All user messages** (full text) | Honcho Cloud API | Stored for conversational memory |
| **All assistant responses** (full text) | Honcho Cloud API | Stored for conversational memory |
| **Session identifiers** | Honcho Cloud API | Links messages to chat sessions |
| **Workspace & User IDs** | Honcho Cloud API | Organizes data in your Honcho account |

### What data is NOT sent

- Agent Zero system prompts
- Local files or tool outputs
- Other plugin data
- API keys for other services

### Where data is stored

Data is stored on **Honcho's cloud infrastructure** managed by Plastic Labs.
Refer to Honcho's privacy and data handling policies:
- [Honcho Documentation](https://docs.honcho.dev)
- [Plastic Labs](https://plasticlabs.ai)

### Data retention

Data persists in Honcho **indefinitely** unless you manually delete it
through the Honcho API or dashboard. This plugin does **not** provide
automatic data deletion or expiration.

### Who can access the data

Anyone with your `HONCHO_API_KEY` can access all data stored in the
associated Honcho workspace. Treat this key as a sensitive credential.

### Consent

By configuring and enabling this plugin (adding `HONCHO_API_KEY` to
Secrets), you **consent** to:
1. Sending all conversation messages to Honcho Cloud
2. Honcho storing and processing that data per their terms
3. Honcho generating summaries and context from your conversations

**If you do not agree, do not add the API key. The plugin remains
inactive without it.**

---

## Features

| Feature | Description |
|---|---|
| **Automatic Message Sync** | Every user and assistant message is pushed to Honcho in real time. |
| **Persistent User Context** | User preferences and facts survive across separate chat sessions. |
| **System-Prompt Injection** | Summarised context is injected automatically so the agent "remembers". |
| **Lazy Initialisation** | No restart required — add the API key and the plugin activates on the next message. |
| **Retry & Resilience** | API calls use exponential back-off; transient errors do not crash the agent. |
| **Secure by Default** | API key loaded only from the secrets manager; never logged. |
| **Graceful Degradation** | If Honcho is unavailable, the agent continues normally without context. |

---

## Architecture

```
┌─────────────────────────┐          ┌───────────────────────┐
│      Agent Zero         │          │     Honcho Cloud       │
│                         │          │                       │
│  ┌───────────────────┐  │   REST   │  ┌─────────────────┐ │
│  │  agent_init       │  │─────────▶│  │  Sessions        │ │
│  │  _20_honcho_init  │  │          │  │  Peers           │ │
│  └─────────┬─────────┘  │          │  │  Messages        │ │
│           │              │          │  │  Context/Summary │ │
│  ┌─────────┴─────────┐  │          │  └─────────────────┘ │
│  │  hist_add_before  │  │─────────▶│                       │
│  │  _20_honcho_sync  │  │  push    │  Messages are stored   │
│  └─────────┬─────────┘  │  msgs    │  and summarised by     │
│           │              │          │  Honcho's backend.     │
│  ┌─────────┴──────────┐ │          │                       │
│  │  system_prompt      │ │─────────▶│  Context is fetched    │
│  │  _30_honcho_context │ │  fetch   │  and cached (120s TTL) │
│  └──────────┬─────────┘ │  ctx     │                       │
│           │              │          │                       │
│  ┌─────────┴─────────┐  │          │                       │
│  │  honcho_helper.py │  │          │                       │
│  │  (shared core)    │  │          │                       │
│  └───────────────────┘  │          │                       │
└─────────────────────────┘          └───────────────────────┘
```

### Data Flow

1. **`_20_honcho_init`** — On agent start, creates a Honcho session
   mapped to the A0 chat ID (`chat-{context.id}`).
2. **`_20_honcho_sync`** — Before each message is persisted in A0's
   history, it is pushed to the Honcho session.
3. **`_30_honcho_context`** — When the system prompt is assembled,
   summarised user context is fetched (with caching) and appended.
4. **`honcho_helper.py`** — Shared library handling SDK calls, retries,
   caching, validation, and secret management.

---

## Prerequisites

| Requirement | Version |
|---|---|
| Agent Zero | v0.8 or later |
| Python | 3.10+ |
| `honcho-ai` SDK | >=2.0.0,<3.0.0 (`pip install honcho-ai`) |
| Honcho API key | [app.honcho.dev/api-keys](https://app.honcho.dev/api-keys) |

---

## Installation

### 1. Install the SDK

```bash
pip install "honcho-ai>=2.0.0,<3.0.0"
```

### 2. Copy the plugin

Place the `honcho/` directory inside your Agent Zero `plugins/` folder:

```
agent-zero/
└── plugins/
    └── honcho/          ← this folder
```

### 3. Configure secrets

Open **Settings → Secrets** in the Agent Zero UI and add:

| Secret | Required | Default | Description |
|---|---|---|---|
| `HONCHO_API_KEY` | **Yes** | — | Your Honcho API key |
| `HONCHO_WORKSPACE_ID` | No | `agent-zero` | Workspace identifier |
| `HONCHO_USER_ID` | No | `user` | User identifier |

> **Security recommendation:** Create a dedicated Honcho workspace
> specifically for Agent Zero rather than sharing one with other
> applications.

### 4. Restart Agent Zero (or just start chatting)

The plugin uses lazy initialisation — it activates automatically on the
next message once the API key is present.

---

## Plugin Structure

```
plugins/honcho/
├── helpers/
│   └── honcho_helper.py                           # Core client & utilities
├── extensions/
│   └── python/
│       ├── agent_init/
│       │   └── _20_honcho_init.py                  # Bootstrap on agent start
│       ├── hist_add_before/
│       │   └── _20_honcho_sync.py                  # Push messages to Honcho
│       └── system_prompt/
│           └── _30_honcho_context.py                # Inject user context
├── plugin.yaml                                     # Marketplace metadata
├── requirements.txt                                # Pinned dependencies
├── README.md
├── NOTICE
└── LICENSE
```

---

## Configuration Reference

| Parameter | Source | Default | Description |
|---|---|---|---|
| `HONCHO_API_KEY` | Secrets | — | API key for Honcho (required) |
| `HONCHO_WORKSPACE_ID` | Secrets | `agent-zero` | Logical workspace grouping |
| `HONCHO_USER_ID` | Secrets | `user` | Identity for the human user |
| `CONTEXT_CACHE_TTL` | Code constant | `120` (seconds) | How long fetched context is cached |
| `MAX_MESSAGE_LENGTH` | Code constant | `10 000` (chars) | Messages are truncated before sending |
| `_RETRY_ATTEMPTS` | Code constant | `3` | Max retries on transient API errors |
| `_RETRY_BASE_DELAY` | Code constant | `0.5` (seconds) | Initial back-off delay (doubles each retry) |

---

## Disabling the Plugin

Remove (or clear) `HONCHO_API_KEY` from **Settings → Secrets**.  The
integration will silently skip all hooks on the next message.

Alternatively, move or delete the `plugins/honcho/` directory.

> **Note:** Disabling the plugin does **not** delete data already stored
> in Honcho. To delete stored data, use the Honcho API or dashboard
> directly.

---

## Security

### Credential handling

- The API key is retrieved **exclusively** from Agent Zero's secrets
  manager and is **never** written to log output or files.
- Secrets are loaded per-request, not cached in environment variables.

### Logging safety

- Message content is **truncated** in all log messages (≤80 chars).
- API keys and tokens are **never** included in logs at any level.
- No file-based debug logs are created by this plugin.

### Input validation

- All message content is validated (non-empty, string type) before
  sending to Honcho.
- Message roles are strictly limited to `user` and `assistant`.
- Content is truncated to `MAX_MESSAGE_LENGTH` (10,000 chars) before
  transmission.

### Network security

- All communication with Honcho uses **HTTPS** (enforced by the SDK).
- Retry logic uses exponential back-off (3 attempts, starting at 0.5s)
  to avoid overwhelming the API during outages.
- On failure, the plugin **degrades gracefully** — the agent continues
  functioning without Honcho context.

### Supply chain

- The only external dependency is [`honcho-ai`](https://pypi.org/project/honcho-ai/),
  published by [Plastic Labs](https://github.com/plastic-labs).
- The SDK version is pinned to `>=2.0.0,<3.0.0` in `requirements.txt`
  to prevent unexpected breaking changes.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| No context injected | API key missing or empty | Add `HONCHO_API_KEY` in Settings → Secrets |
| `Honcho SDK not installed` in logs | `honcho-ai` package missing | Run `pip install "honcho-ai>=2.0.0,<3.0.0"` in the A0 container |
| `sync_message failed` errors | Network / Honcho outage | Check connectivity; retries handle transient issues automatically |
| Stale context | Cache TTL not expired | Wait ~2 min or restart agent; cache TTL is 120 s |
| `Invalid message role` warning | Extension received unexpected role | Ensure only `user`/`assistant` messages reach the sync hook |
| Plugin not activating | Plugin directory misplaced | Verify path is `plugins/honcho/` at the A0 root |

### Logging

All plugin logs use the `honcho` logger.  Increase verbosity with:

```python
import logging
logging.getLogger("honcho").setLevel(logging.DEBUG)
```

Message **content is never logged in full** — only a truncated preview
(≤80 chars) appears at `DEBUG` level.

---

## SDK Compatibility

| SDK | Tested |
|---|---|
| `honcho-ai` 2.0.x | ✅ |
| `honcho-ai` 2.1.x | ✅ (expected) |

---

## Third-Party Services

This plugin integrates with the following external service:

| Service | Provider | Purpose | Data sent |
|---------|----------|---------|-----------|
| [Honcho](https://honcho.dev) | [Plastic Labs, Inc.](https://plasticlabs.ai) | Conversational memory & user context | All chat messages |

By using this plugin you agree to Honcho's terms of service and privacy
policy. This plugin's authors are **not responsible** for how Honcho
stores, processes, or handles your data.

---

## License

[Apache License 2.0](LICENSE)

This project is independently licensed and is **not affiliated with**
Plastic Labs or the Honcho project. "Honcho" is a trademark of Plastic
Labs, Inc., used here for descriptive purposes only.

---

## Links

- [Honcho Documentation](https://docs.honcho.dev)
- [Honcho Python SDK](https://github.com/plastic-labs/honcho-python)
- [Honcho Privacy / Terms](https://honcho.dev) *(check their site for current policies)*
- [Agent Zero](https://github.com/agent0ai/agent-zero)
- [Agent Zero Plugin System](https://github.com/agent0ai/a0-plugins)
