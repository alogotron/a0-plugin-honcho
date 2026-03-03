# 🧠 Honcho Conversational Memory Plugin for Agent Zero

Persistent conversational memory via [Honcho Cloud](https://honcho.dev) by [Plastic Labs](https://plasticlabs.ai). Gives Agent Zero the ability to **remember users across chat sessions** — preferences, facts, context, and behavioral patterns.

## What It Does

| Feature | Description |
|---------|-------------|
| **Automatic Message Sync** | Every user and assistant message is pushed to Honcho in real time |
| **Persistent User Context** | User preferences and facts survive across separate chat sessions |
| **System-Prompt Injection** | Summarised context is fetched and injected automatically so the agent "remembers" |
| **Graceful Degradation** | If Honcho is unavailable, the agent continues normally |
| **Settings UI** | Configure workspace, peer IDs, cache TTL directly in A0's settings |
| **Plugin System Conformant** | Built for A0's plugin architecture (`plugin.yaml`, extensions, settings) |

## How It Works

```
┌─────────────┐     messages      ┌──────────────┐     conclusions     ┌──────────────┐
│  Agent Zero  │ ───────────────▶  │ Honcho Cloud │ ──────────────────▶ │  Peer Cards  │
│  (your chat) │                   │  (memory)    │   dreaming cycle    │  (knowledge) │
└──────┬───────┘                   └──────┬───────┘                     └──────────────┘
       │                                  │
       │◀──── context injection ──────────┘
       │      (system prompt)
```

1. **Message Sync** — When you chat, messages are mirrored to Honcho via the `hist_add_before` extension
2. **Context Retrieval** — On each turn, user context is fetched from Honcho and injected into the system prompt
3. **Dreaming** — Honcho's background process consolidates observations into peer cards (persistent user/agent knowledge)

## Installation

### 1. Clone into Agent Zero's user plugins directory

```bash
cd /a0/usr/plugins
git clone https://github.com/alogotron/a0-plugin-honcho.git honcho
```

### 2. Install dependencies

```bash
# Install into A0's runtime Python (important: use the correct venv)
/opt/venv-a0/bin/pip install -r /a0/usr/plugins/honcho/requirements.txt

# Also install for pyenv if applicable
/opt/pyenv/versions/3.12.4/bin/pip install honcho-ai
```

### 3. Get a Honcho API key

1. Go to [app.honcho.dev](https://app.honcho.dev)
2. Create a free account
3. Generate an API key

### 4. Configure in Agent Zero

1. Go to **Settings → Secrets** and add:
   - `HONCHO_API_KEY` — your API key from Honcho
   - `HONCHO_WORKSPACE_ID` — (optional) workspace name, defaults to `agent-zero`

2. Go to **Settings → Plugins** and enable **Honcho Conversational Memory**

3. (Optional) Click **Configure** on the plugin to adjust:
   - Workspace ID
   - User/Agent peer IDs
   - Context cache TTL
   - Max context tokens

### 5. Restart Agent Zero

The plugin will be discovered on restart. You'll see `[Honcho] Integration enabled for session: chat-xxxxx` in the logs.

## Plugin Structure

```
honcho/
├── plugin.yaml                          # Plugin manifest
├── default_config.yaml                  # Settings defaults
├── requirements.txt                     # honcho-ai>=2.0.0
├── LICENSE                              # MIT
├── helpers/
│   └── honcho_helper.py                 # Core integration logic
├── extensions/
│   └── python/
│       ├── agent_init/
│       │   └── _20_honcho_init.py       # Initialize Honcho on agent start
│       ├── hist_add_before/
│       │   └── _20_honcho_sync.py       # Sync messages to Honcho
│       └── system_prompt/
│           └── _30_honcho_context.py    # Inject user context into prompt
├── prompts/
│   └── honcho.context.md                # Context injection template
└── webui/
    └── config.html                      # Settings UI
```

## Configuration

### Secrets (Settings → Secrets)

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `HONCHO_API_KEY` | ✅ Yes | — | Your Honcho API key |
| `HONCHO_WORKSPACE_ID` | No | `agent-zero` | Override workspace (also configurable in plugin settings) |
| `HONCHO_USER_ID` | No | `user` | Override user peer ID |

### Plugin Settings (Settings → Plugins → Honcho → Configure)

| Setting | Default | Description |
|---------|---------|-------------|
| Workspace ID | `agent-zero` | Honcho workspace name |
| User Peer ID | `user` | Human peer identifier |
| Agent Peer ID | `agent-zero` | AI agent peer identifier |
| Cache TTL | `120` seconds | How long to cache context |
| Max Context Tokens | `500` | Max tokens for injected context |
| Debug Logging | `false` | Verbose logging |

## Honcho Concepts

| Concept | Description |
|---------|-------------|
| **Workspace** | Top-level container for all memory data |
| **Peer** | An entity (user or agent) with a card and conclusions |
| **Session** | A conversation thread, maps 1:1 to an A0 chat |
| **Conclusion** | An individual observation Honcho has made |
| **Card** | Compiled summary of key facts about a peer |
| **Dreaming** | Background process that consolidates conclusions into cards |

## Requirements

- Agent Zero (development branch with plugin system)
- Python 3.12+
- `honcho-ai` >= 2.0.0
- Free Honcho account at [honcho.dev](https://honcho.dev)

## Links

- [Honcho Documentation](https://docs.honcho.dev)
- [Honcho Dashboard](https://app.honcho.dev)
- [Agent Zero](https://github.com/agent0ai/agent-zero)
- [Plastic Labs](https://plasticlabs.ai)

## License

MIT
