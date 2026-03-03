"""
Honcho Integration Helper for Agent Zero
Clean implementation following A0 plugin conventions.
"""

import time
from typing import Optional, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from agent import AgentContext

try:
    from honcho import Honcho
    HONCHO_AVAILABLE = True
except ImportError:
    HONCHO_AVAILABLE = False
    Honcho = None

# Caches
_context_cache: Dict[str, tuple] = {}
_client_cache: Dict[str, Any] = {}


def _log(context, msg: str, log_type: str = "info"):
    """Log using A0's logging system via context.log."""
    try:
        if context and hasattr(context, 'log'):
            context.log.log(type=log_type, heading=msg)
        else:
            print(f"[Honcho] {msg}")
    except Exception:
        print(f"[Honcho] {msg}")


def _get_plugin_config(agent) -> Dict[str, Any]:
    """Read plugin settings from A0's config system with fallbacks."""
    config = {}
    defaults = {
        "honcho_workspace_id": "agent-zero",
        "honcho_user_id": "user",
        "honcho_agent_peer_id": "agent-zero",
        "honcho_cache_ttl": 120,
        "honcho_max_context_tokens": 500,
        "honcho_debug": False,
    }
    for key, default in defaults.items():
        config[key] = getattr(agent.config, key, default)
    return config


def get_api_key(context: Optional["AgentContext"] = None) -> Optional[str]:
    """Retrieve HONCHO_API_KEY from A0 secrets manager."""
    try:
        from python.helpers.secrets import get_secrets_manager
        secrets_mgr = get_secrets_manager(context)
        secrets = secrets_mgr.load_secrets()
        key = secrets.get("HONCHO_API_KEY", "").strip() or None
        return key
    except Exception as e:
        _log(context, f"Error loading API key: {e}", "error")
        return None


def _get_secret_value(key: str, default: str, context=None) -> str:
    """Get a value from secrets with fallback."""
    try:
        from python.helpers.secrets import get_secrets_manager
        secrets_mgr = get_secrets_manager(context)
        secrets = secrets_mgr.load_secrets()
        return secrets.get(key, "").strip() or default
    except Exception:
        return default


def is_configured(context=None) -> bool:
    """Check if Honcho SDK is available and API key is set."""
    if not HONCHO_AVAILABLE:
        return False
    return bool(get_api_key(context))


def get_client(context=None) -> Optional[Any]:
    """Get or create a cached Honcho client."""
    if not HONCHO_AVAILABLE:
        return None
    api_key = get_api_key(context)
    if not api_key:
        return None

    # Workspace ID: check secrets first (override), then plugin config
    workspace_id = _get_secret_value("HONCHO_WORKSPACE_ID", "", context)
    if not workspace_id and context and hasattr(context, 'agent0'):
        config = _get_plugin_config(context.agent0)
        workspace_id = config.get("honcho_workspace_id", "agent-zero")
    if not workspace_id:
        workspace_id = "agent-zero"

    if workspace_id in _client_cache:
        return _client_cache[workspace_id]

    try:
        client = Honcho(api_key=api_key, workspace_id=workspace_id)
        _client_cache[workspace_id] = client
        _log(context, f"Connected to workspace: {workspace_id}", "util")
        return client
    except Exception as e:
        _log(context, f"Client error: {e}", "error")
        return None


def get_session_id(context) -> str:
    """Derive Honcho session ID from the A0 chat context."""
    if hasattr(context, 'id') and context.id:
        return f"chat-{context.id}"
    return f"session-{id(context)}"


def get_user_id(context=None) -> str:
    """Get the Honcho user peer ID."""
    user_id = _get_secret_value("HONCHO_USER_ID", "", context)
    if not user_id and context and hasattr(context, 'agent0'):
        config = _get_plugin_config(context.agent0)
        user_id = config.get("honcho_user_id", "user")
    return user_id or "user"


def get_agent_peer_id(context=None) -> str:
    """Get the Honcho agent peer ID."""
    if context and hasattr(context, 'agent0'):
        config = _get_plugin_config(context.agent0)
        return config.get("honcho_agent_peer_id", "agent-zero")
    return "agent-zero"


def ensure_initialized(context) -> bool:
    """Ensure Honcho session is initialized for this context."""
    if hasattr(context, '_honcho') and context._honcho.get('enabled'):
        return True

    if not is_configured(context):
        return False

    client = get_client(context)
    if not client:
        return False

    if not hasattr(context, '_honcho'):
        context._honcho = {}

    session_id = get_session_id(context)
    try:
        session = client.session(session_id)
        user_peer = client.peer(get_user_id(context))
        agent_peer = client.peer(get_agent_peer_id(context))
        try:
            session.add_peers([user_peer, agent_peer])
        except Exception:
            pass  # Peers may already be added

        context._honcho['enabled'] = True
        context._honcho['session_id'] = session_id
        _log(context, f"Session initialized: {session_id}", "util")
        return True
    except Exception as e:
        _log(context, f"Init error: {e}", "error")
        context._honcho['enabled'] = False
        return False


def sync_message(context, role: str, content: str) -> bool:
    """Sync a message to Honcho Cloud."""
    if not ensure_initialized(context):
        return False

    client = get_client(context)
    if not client:
        return False

    try:
        session_id = get_session_id(context)
        session = client.session(session_id)

        if role == "user":
            peer = client.peer(get_user_id(context))
        else:
            peer = client.peer(get_agent_peer_id(context))

        msg = peer.message(content[:10000])
        session.add_messages([msg])
        return True
    except Exception as e:
        _log(context, f"Sync error: {e}", "error")
        return False


def get_user_context(context, max_tokens: int = 500) -> Optional[str]:
    """Fetch user context from Honcho for system prompt injection."""
    if not ensure_initialized(context):
        return None

    # Check cache
    session_id = get_session_id(context)
    cache_ttl = 120
    if context and hasattr(context, 'agent0'):
        config = _get_plugin_config(context.agent0)
        cache_ttl = config.get("honcho_cache_ttl", 120)

    if session_id in _context_cache:
        cached_time, cached_context = _context_cache[session_id]
        if time.time() - cached_time < cache_ttl:
            return cached_context

    client = get_client(context)
    if not client:
        return None

    try:
        session = client.session(session_id)
        ctx = session.context()
        result = None
        if hasattr(ctx, 'summary') and ctx.summary:
            result = ctx.summary
        elif hasattr(ctx, 'peer_representation') and ctx.peer_representation:
            result = ctx.peer_representation
        _context_cache[session_id] = (time.time(), result)
        return result
    except Exception as e:
        _log(context, f"Context error: {e}", "error")
        return None


def clear_context_cache(session_id: Optional[str] = None):
    """Clear cached context."""
    global _context_cache
    if session_id:
        _context_cache.pop(session_id, None)
    else:
        _context_cache = {}
