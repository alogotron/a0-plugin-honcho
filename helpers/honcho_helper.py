"""Honcho configuration, durable ingestion, adaptive recall, and safe operations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import TYPE_CHECKING, Any, Mapping, Optional
from urllib.parse import urlparse, urlsplit, urlunsplit

from .outbox import Outbox, OutboxEvent
from .worker import WorkerSettings, get_singleton, start_singleton, stop_all

if TYPE_CHECKING:
    from agent import AgentContext

try:
    from honcho import Honcho
    from honcho.session import SessionPeerConfig

    HONCHO_AVAILABLE = True
except ImportError:
    HONCHO_AVAILABLE = False
    Honcho = None  # type: ignore[assignment,misc]
    SessionPeerConfig = None  # type: ignore[assignment,misc]

CLOUD_API_URL = "https://api.honcho.dev"
HOSTED_MCP_URL = "https://mcp.honcho.dev"
DEFAULT_OUTBOX_PATH = "/a0/usr/honcho/outbox.sqlite3"

_DEFAULTS: dict[str, Any] = {
    "honcho_api_url": CLOUD_API_URL,
    "honcho_mcp_url": HOSTED_MCP_URL,
    "honcho_workspace_id": "agent-zero",
    "honcho_global_workspace_id": "",
    "honcho_user_id": "user",
    "honcho_agent_peer_id": "agent-zero",
    "honcho_cache_ttl": 120,
    "honcho_max_context_tokens": 500,
    "honcho_debug": False,
    "honcho_capture_enabled": True,
    "honcho_capture_user": True,
    "honcho_capture_assistant": True,
    "honcho_outbox_path": DEFAULT_OUTBOX_PATH,
    "honcho_outbox_max_rows": 10000,
    "honcho_outbox_max_content_chars": 10000,
    "honcho_delivered_retention_seconds": 86400,
    "honcho_dead_retention_seconds": 2592000,
    "honcho_worker_poll_seconds": 1,
    "honcho_worker_batch_size": 20,
    "honcho_worker_concurrency": 2,
    "honcho_worker_lease_seconds": 30,
    "honcho_worker_max_attempts": 8,
    "honcho_retry_base_seconds": 2,
    "honcho_retry_max_seconds": 300,
    "honcho_retry_jitter_ratio": 0.2,
    "honcho_auth_circuit_seconds": 900,
    "honcho_sync_timeout_seconds": 10,
    "honcho_recall_enabled": True,
    "honcho_recall_search_top_k": 8,
    "honcho_recall_max_distance": 0.8,
    "honcho_recall_max_conclusions": 20,
    "honcho_context_cache_max_entries": 256,
}

_client_cache: dict[tuple[str, str, str], Any] = {}
_delivery_client_cache: dict[tuple[str, str, str, float], Any] = {}
_context_cache: dict[tuple[str, str, str, str, str, int, str], tuple[float, Optional[str]]] = {}
_context_inflight: dict[tuple[str, str, str, str, str, int, str], threading.Event] = {}
_context_lock = threading.RLock()
_outbox_cache: dict[tuple[Any, ...], Outbox] = {}
_runtime_configs: dict[str, "EffectiveConfig"] = {}
_ready_sessions: set[tuple[str, str, str]] = set()
_runtime_lock = threading.RLock()


@dataclass(frozen=True)
class EffectiveConfig:
    api_key: str
    api_url: str
    mcp_url: str
    workspace_id: str
    user_peer_id: str
    agent_peer_id: str
    cache_ttl: int
    max_context_tokens: int
    debug: bool
    capture_enabled: bool = True
    capture_user: bool = True
    capture_assistant: bool = True
    outbox_path: str = DEFAULT_OUTBOX_PATH
    outbox_max_rows: int = 10000
    outbox_max_content_chars: int = 10000
    delivered_retention_seconds: int = 86400
    dead_retention_seconds: int = 2592000
    worker_poll_seconds: float = 1.0
    worker_batch_size: int = 20
    worker_concurrency: int = 2
    worker_lease_seconds: float = 30.0
    worker_max_attempts: int = 8
    retry_base_seconds: float = 2.0
    retry_max_seconds: float = 300.0
    retry_jitter_ratio: float = 0.2
    auth_circuit_seconds: float = 900.0
    sync_timeout_seconds: float = 10.0
    recall_enabled: bool = True
    recall_search_top_k: int = 8
    recall_max_distance: float = 0.8
    recall_max_conclusions: int = 20
    context_cache_max_entries: int = 256

    @property
    def mode(self) -> str:
        return endpoint_mode(self.api_url, CLOUD_API_URL)

    @property
    def mcp_mode(self) -> str:
        return endpoint_mode(self.mcp_url, HOSTED_MCP_URL)

    @property
    def key_digest(self) -> str:
        return hashlib.sha256(self.api_key.encode("utf-8")).hexdigest()

    @property
    def client_cache_key(self) -> tuple[str, str, str]:
        return (self.api_url, self.workspace_id, self.key_digest)

    @property
    def routing_id(self) -> str:
        """Non-secret identifier for queued-event routing across scoped configs."""
        identity = "\0".join(
            (
                self.api_url,
                self.workspace_id,
                self.user_peer_id,
                self.agent_peer_id,
            )
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]

    @property
    def worker_settings(self) -> WorkerSettings:
        return WorkerSettings(
            poll_seconds=self.worker_poll_seconds,
            batch_size=self.worker_batch_size,
            concurrency=self.worker_concurrency,
            lease_seconds=max(
                self.worker_lease_seconds,
                self.sync_timeout_seconds + 5.0,
            ),
            max_attempts=self.worker_max_attempts,
            retry_base_seconds=self.retry_base_seconds,
            retry_max_seconds=self.retry_max_seconds,
            jitter_ratio=self.retry_jitter_ratio,
            auth_circuit_seconds=self.auth_circuit_seconds,
        ).normalized()

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("api_key", None)
        data.pop("outbox_path", None)
        data.update({"mode": self.mode, "mcp_mode": self.mcp_mode})
        return data


def _normalize_url(value: Any, default: str = "") -> str:
    supplied = str(value or "").strip()
    text = supplied or str(default or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        host = parsed.hostname or ""
        if not host:
            return ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port is not None else ""
    except (TypeError, ValueError):
        return ""
    return urlunsplit(
        (parsed.scheme.lower(), f"{host.lower()}{port}", parsed.path.rstrip("/"), "", "")
    )


def _normalize_outbox_path(value: Any) -> str:
    """Accept absolute local paths only; avoid CWD-dependent queue locations."""
    text = str(value or DEFAULT_OUTBOX_PATH).strip()
    try:
        path = Path(text).expanduser()
    except (TypeError, ValueError, OSError):
        return DEFAULT_OUTBOX_PATH
    return str(path) if path.is_absolute() else DEFAULT_OUTBOX_PATH


def _positive_int(value: Any, default: int, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return default
    return min(parsed, maximum) if maximum is not None else parsed


def _positive_float(
    value: Any, default: float, minimum: float = 0.01, maximum: float | None = None
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return default
    return min(parsed, maximum) if maximum is not None else parsed


def _ratio(value: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _log(context: Optional["AgentContext"], msg: str, log_type: str = "info") -> None:
    try:
        if context and hasattr(context, "log"):
            context.log.log(type=log_type, heading=f"[Honcho] {msg}")
        else:
            print(f"[Honcho] {msg}")
    except Exception:
        print(f"[Honcho] {msg}")


def _agent_for_context(context: Optional["AgentContext"]) -> Any:
    return getattr(context, "agent0", None) if context else None


def _get_plugin_config(agent: Any = None) -> dict[str, Any]:
    try:
        from helpers.plugins import get_plugin_config

        config = dict(get_plugin_config("honcho", agent=agent) or {})
    except Exception:
        config = {}
    return {**_DEFAULTS, **config}


def _load_secret_values(context: Optional["AgentContext"]) -> Mapping[str, Any]:
    try:
        from helpers.secrets import get_secrets_manager

        return get_secrets_manager(context).load_secrets() or {}
    except Exception:
        return {}


def _runtime_override(names: tuple[str, ...], secrets: Mapping[str, Any]) -> Optional[str]:
    for name in names:
        secret_value = str(secrets.get(name, "") or "").strip()
        if secret_value:
            return secret_value
        env_value = str(os.environ.get(name, "") or "").strip()
        if env_value:
            return env_value
    return None


def get_effective_config(
    context: Optional["AgentContext"] = None, *, agent: Any = None
) -> EffectiveConfig:
    resolved_agent = agent if agent is not None else _agent_for_context(context)
    explicit = _get_plugin_config(resolved_agent)
    secrets = _load_secret_values(context)
    override = lambda name: _runtime_override((name,), secrets)
    explicit_workspace = (
        str(explicit.get("honcho_global_workspace_id") or "").strip()
        or str(explicit.get("honcho_workspace_id") or "").strip()
        or _DEFAULTS["honcho_workspace_id"]
    )
    api_url = _runtime_override(("HONCHO_API_URL", "HONCHO_BASE_URL"), secrets)
    mcp_url = override("HONCHO_MCP_URL")
    outbox_path = _normalize_outbox_path(
        override("HONCHO_OUTBOX_PATH")
        or explicit.get("honcho_outbox_path")
        or DEFAULT_OUTBOX_PATH
    )
    return EffectiveConfig(
        api_key=override("HONCHO_API_KEY") or "",
        api_url=_normalize_url(api_url or explicit.get("honcho_api_url"), CLOUD_API_URL),
        mcp_url=_normalize_url(mcp_url or explicit.get("honcho_mcp_url"), HOSTED_MCP_URL),
        workspace_id=(override("HONCHO_WORKSPACE_ID") or explicit_workspace).strip()
        or _DEFAULTS["honcho_workspace_id"],
        user_peer_id=(
            override("HONCHO_USER_ID")
            or str(explicit.get("honcho_user_id") or "").strip()
            or _DEFAULTS["honcho_user_id"]
        ),
        agent_peer_id=(
            override("HONCHO_AGENT_PEER_ID")
            or str(explicit.get("honcho_agent_peer_id") or "").strip()
            or _DEFAULTS["honcho_agent_peer_id"]
        ),
        cache_ttl=_positive_int(
            override("HONCHO_CACHE_TTL") or explicit.get("honcho_cache_ttl"), 120
        ),
        max_context_tokens=_positive_int(
            override("HONCHO_MAX_CONTEXT_TOKENS")
            or explicit.get("honcho_max_context_tokens"),
            500,
            maximum=4000,
        ),
        debug=_as_bool(
            override("HONCHO_DEBUG")
            if override("HONCHO_DEBUG") is not None
            else explicit.get("honcho_debug")
        ),
        capture_enabled=_as_bool(explicit.get("honcho_capture_enabled"), True),
        capture_user=_as_bool(explicit.get("honcho_capture_user"), True),
        capture_assistant=_as_bool(explicit.get("honcho_capture_assistant"), True),
        outbox_path=outbox_path,
        outbox_max_rows=_positive_int(explicit.get("honcho_outbox_max_rows"), 10000, maximum=1000000),
        outbox_max_content_chars=_positive_int(explicit.get("honcho_outbox_max_content_chars"), 10000, maximum=100000),
        delivered_retention_seconds=_positive_int(explicit.get("honcho_delivered_retention_seconds"), 86400, minimum=0),
        dead_retention_seconds=_positive_int(explicit.get("honcho_dead_retention_seconds"), 2592000, minimum=0),
        worker_poll_seconds=_positive_float(explicit.get("honcho_worker_poll_seconds"), 1, minimum=0.05, maximum=60),
        worker_batch_size=_positive_int(explicit.get("honcho_worker_batch_size"), 20, maximum=100),
        worker_concurrency=_positive_int(explicit.get("honcho_worker_concurrency"), 2, maximum=16),
        worker_lease_seconds=_positive_float(explicit.get("honcho_worker_lease_seconds"), 30, minimum=1, maximum=3600),
        worker_max_attempts=_positive_int(explicit.get("honcho_worker_max_attempts"), 8, maximum=100),
        retry_base_seconds=_positive_float(explicit.get("honcho_retry_base_seconds"), 2, minimum=0.05, maximum=3600),
        retry_max_seconds=_positive_float(explicit.get("honcho_retry_max_seconds"), 300, minimum=0.05, maximum=86400),
        retry_jitter_ratio=_ratio(explicit.get("honcho_retry_jitter_ratio"), 0.2),
        auth_circuit_seconds=_positive_float(explicit.get("honcho_auth_circuit_seconds"), 900, minimum=1, maximum=86400),
        sync_timeout_seconds=_positive_float(explicit.get("honcho_sync_timeout_seconds"), 10, minimum=1, maximum=120),
        recall_enabled=_as_bool(explicit.get("honcho_recall_enabled"), True),
        recall_search_top_k=_positive_int(explicit.get("honcho_recall_search_top_k"), 8, maximum=100),
        recall_max_distance=_ratio(explicit.get("honcho_recall_max_distance"), 0.8),
        recall_max_conclusions=_positive_int(explicit.get("honcho_recall_max_conclusions"), 20, maximum=100),
        context_cache_max_entries=_positive_int(
            explicit.get("honcho_context_cache_max_entries"),
            256,
            maximum=4096,
        ),
    )


def destination_ready(effective: EffectiveConfig) -> bool:
    """Return whether capture/recall may target this exact destination."""
    if not (
        effective.api_url
        and effective.workspace_id
        and effective.user_peer_id
        and effective.agent_peer_id
    ):
        return False
    return bool(effective.api_key) or effective.mode == "self-hosted"


def get_api_key(context: Optional["AgentContext"] = None) -> Optional[str]:
    return get_effective_config(context).api_key or None


def _get_secret_value(key: str, default: str, context: Optional["AgentContext"] = None) -> str:
    return _runtime_override((key,), _load_secret_values(context)) or default


def is_configured(context: Optional["AgentContext"] = None) -> bool:
    return HONCHO_AVAILABLE and destination_ready(get_effective_config(context))


def _construct_client(effective: EffectiveConfig, **options: Any) -> Any:
    if not destination_ready(effective):
        raise RuntimeError("Honcho destination is incomplete or unauthenticated")
    return Honcho(
        api_key=effective.api_key or None,
        base_url=effective.api_url,
        workspace_id=effective.workspace_id,
        **options,
    )


def get_client(
    context: Optional["AgentContext"] = None, *, config: Optional[EffectiveConfig] = None
) -> Optional[Any]:
    if not HONCHO_AVAILABLE:
        return None
    effective = config or get_effective_config(context)
    if not destination_ready(effective):
        return None
    key = effective.client_cache_key
    with _runtime_lock:
        cached = _client_cache.get(key)
    if cached is not None:
        return cached
    try:
        candidate = _construct_client(
            effective,
            timeout=effective.sync_timeout_seconds,
            max_retries=1,
        )
        with _runtime_lock:
            client = _client_cache.setdefault(key, candidate)
            while len(_client_cache) > 32:
                _client_cache.pop(next(iter(_client_cache)))
        _register_runtime_config(effective)
        return client
    except Exception as exc:
        _log(context, f"Client error: {redact_text(exc, effective)}", "error")
        return None


def get_probe_client(effective: EffectiveConfig) -> Any:
    if not HONCHO_AVAILABLE:
        raise RuntimeError("Honcho SDK is unavailable")
    if not destination_ready(effective):
        raise RuntimeError("Honcho destination is incomplete or unauthenticated")
    return _construct_client(effective, timeout=5, max_retries=0)


def get_session_id(context: "AgentContext") -> str:
    return f"chat-{context.id}" if getattr(context, "id", None) else f"session-{id(context)}"


def get_user_id(context: Optional["AgentContext"] = None) -> str:
    return get_effective_config(context).user_peer_id


def get_agent_peer_id(context: Optional["AgentContext"] = None) -> str:
    return get_effective_config(context).agent_peer_id


def _already_present(exc: Exception) -> bool:
    status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    text = " ".join(
        str(value)
        for value in (exc, getattr(exc, "body", ""), getattr(exc, "code", ""))
        if value
    ).lower()
    return status == 409 and any(word in text for word in ("already", "exists", "present", "member", "duplicate")) and any(
        word in text for word in ("peer", "session", "resource")
    )


def _observation_roster(client: Any, effective: EffectiveConfig) -> list[Any]:
    user_peer = client.peer(effective.user_peer_id)
    if effective.user_peer_id == effective.agent_peer_id:
        return [(user_peer, SessionPeerConfig(observe_others=True, observe_me=True))]
    return [
        (user_peer, SessionPeerConfig(observe_others=True, observe_me=True)),
        (client.peer(effective.agent_peer_id), SessionPeerConfig(observe_others=True, observe_me=False)),
    ]


def ensure_initialized(context: "AgentContext") -> bool:
    effective = get_effective_config(context)
    if not HONCHO_AVAILABLE or not destination_ready(effective):
        return False
    _register_runtime_config(effective)
    initialize_runtime(context, start_worker=True)
    session_id = get_session_id(context)
    identity = (
        effective.routing_id,
        effective.key_digest,
        session_id,
    )
    state = getattr(context, "_honcho", None)
    if state and state.get("enabled") and state.get("identity") == identity:
        return True
    client = get_client(context, config=effective)
    if client is None:
        return False
    if not hasattr(context, "_honcho"):
        context._honcho = {}
    try:
        _ensure_remote_session(client, effective, session_id)
        context._honcho.update(
            {"enabled": True, "session_id": session_id, "identity": identity}
        )
        return True
    except Exception as exc:
        context._honcho["enabled"] = False
        _log(context, f"Init error: {redact_text(exc, effective)}", "error")
        return False


def _neutralize_secret_aliases(text: str) -> str:
    return re.sub(r"§§secret\([^\r\n)]{1,200}\)", "[REDACTED]", text)


def _mask_outbound(
    context: "AgentContext", content: str, effective: EffectiveConfig
) -> Optional[str]:
    try:
        from helpers.secrets import get_secrets_manager

        masked = get_secrets_manager(context).mask_values(
            content,
            min_length=1,
            placeholder="[REDACTED]",
        )
        return _neutralize_secret_aliases(masked)
    except Exception as exc:
        _log(
            context,
            "Secret masking failed; content was not sent or persisted: "
            + redact_text(type(exc).__name__, effective),
            "error",
        )
        return None


def set_current_search_query(context: "AgentContext", content: Any) -> bool:
    """Keep a bounded, already-redacted current-turn query in process memory."""
    effective = get_effective_config(context)
    state = getattr(context, "_honcho", None)
    if not isinstance(state, dict):
        state = {}
        context._honcho = state
    masked = mask_outbound(context, content, effective)
    if masked is None:
        state["search_query"] = ""
        return False
    state["search_query"] = masked[:2000]
    return True


def get_current_search_query(context: "AgentContext") -> str:
    state = getattr(context, "_honcho", None)
    return str(state.get("search_query") or "") if isinstance(state, dict) else ""


def normalize_capture_content(value: Any) -> str:
    current = value
    while isinstance(current, dict):
        if any(key in current for key in ("tool_calls", "tool_result", "function_call")):
            return ""
        found = None
        for key in ("user_message", "content", "text", "message"):
            if key in current and current[key] not in (None, ""):
                found = current[key]
                break
        if found is None:
            return ""
        current = found
    if isinstance(current, list):
        parts = []
        for item in current:
            if isinstance(item, dict) and str(item.get("type") or "").lower() in {"text", "input_text", "output_text"}:
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        current = "\n".join(parts)
    text = str(current or "").replace(chr(0), "")
    return re.sub(r"[ \t]+", " ", text).strip()


def should_capture(role: str, content: Any, metadata: Optional[Mapping[str, Any]] = None) -> bool:
    role = str(role or "").strip().lower()
    if role not in {"user", "assistant"}:
        return False
    meta = {str(k).lower(): v for k, v in (metadata or {}).items()}
    trusted_source = str(meta.get("source") or "").lower() in {
        "user_message",
        "response_tool",
    }
    category = " ".join(
        str(meta.get(key) or "").lower()
        for key in ("type", "kind", "source", "role")
    )
    # The exact response-tool hook is authoritative final assistant output;
    # do not reject it merely because its trusted source name contains "tool".
    if not trusted_source and any(
        word in category
        for word in ("tool", "internal", "reasoning", "system", "protocol")
    ):
        return False
    if role == "assistant" and any(
        meta.get(key) is False for key in ("final", "is_final")
    ):
        return False
    text = normalize_capture_content(content)
    if not text:
        return False
    if trusted_source:
        return True
    lowered = text.lstrip().lower()
    if lowered.startswith(("[protocol]", "[extras]", "<tool", "system:", "developer:")):
        return False
    if '"tool_name"' in lowered and '"tool_args"' in lowered:
        return False
    return True


def _capture_event_id(
    context: "AgentContext",
    role: str,
    content: str,
    metadata: Mapping[str, Any],
) -> str:
    supplied = str(metadata.get("id") or metadata.get("message_id") or "").strip()
    if supplied:
        position = supplied
    else:
        history_obj = getattr(getattr(context, "agent0", None), "history", None)
        position = str(getattr(history_obj, "counter", 0) + 1)
    basis = f"{get_session_id(context)}\0{role}\0{position}\0{content}"
    return "a0-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()


def enqueue_message(
    context: "AgentContext",
    role: str,
    content: Any,
    *,
    metadata: Optional[Mapping[str, Any]] = None,
) -> bool:
    effective = get_effective_config(context)
    role = str(role or "").strip().lower()
    if not effective.capture_enabled or not destination_ready(effective):
        return False
    if role == "user" and not effective.capture_user:
        return False
    if role == "assistant" and not effective.capture_assistant:
        return False
    if not should_capture(role, content, metadata):
        return False
    normalized = normalize_capture_content(content)
    masked = _mask_outbound(context, normalized, effective)
    if masked is None or not masked.strip():
        return False
    _register_runtime_config(effective)
    outbox = get_outbox(effective)
    event_id = _capture_event_id(context, role, masked, metadata or {})
    peer_id = effective.user_peer_id if role == "user" else effective.agent_peer_id
    queued = outbox.enqueue(
        endpoint=effective.api_url,
        workspace_id=effective.workspace_id,
        session_id=get_session_id(context),
        peer_id=peer_id,
        content=masked,
        role=role,
        user_peer_id=effective.user_peer_id,
        agent_peer_id=effective.agent_peer_id,
        config_id=effective.routing_id,
        event_id=event_id,
    )
    if queued:
        worker = initialize_runtime(context, start_worker=True)
        if worker:
            worker.wake()
        return True
    _log(context, "Outbox capacity reached; redacted message was not queued", "error")
    return False


def sync_message(context: "AgentContext", role: str, content: str) -> bool:
    """Compatibility alias: Stage 2 synchronization means durable enqueue."""
    return enqueue_message(context, role, content)


def get_outbox(effective: Optional[EffectiveConfig] = None) -> Outbox:
    cfg = effective or get_effective_config()
    key = (
        str(Path(cfg.outbox_path).expanduser()),
        cfg.outbox_max_rows,
        cfg.outbox_max_content_chars,
        cfg.delivered_retention_seconds,
        cfg.dead_retention_seconds,
    )
    with _runtime_lock:
        outbox = _outbox_cache.get(key)
        if outbox is None:
            outbox = Outbox(
                cfg.outbox_path,
                max_rows=cfg.outbox_max_rows,
                max_content_chars=cfg.outbox_max_content_chars,
                delivered_retention_seconds=cfg.delivered_retention_seconds,
                dead_retention_seconds=cfg.dead_retention_seconds,
            )
            _outbox_cache[key] = outbox
        return outbox


class RuntimeConfigUnavailable(RuntimeError):
    failure_code = "runtime_config_unavailable"
    defer_without_attempt = True
    retry_after = 5.0


def _register_runtime_config(effective: EffectiveConfig) -> None:
    if not destination_ready(effective):
        return
    with _runtime_lock:
        _runtime_configs[effective.routing_id] = effective
        while len(_runtime_configs) > 64:
            _runtime_configs.pop(next(iter(_runtime_configs)))


def _event_matches_config(event: OutboxEvent, effective: EffectiveConfig) -> bool:
    if (event.endpoint, event.workspace_id) != (
        effective.api_url,
        effective.workspace_id,
    ):
        return False
    if event.config_id and event.config_id != effective.routing_id:
        return False
    if event.user_peer_id and event.user_peer_id != effective.user_peer_id:
        return False
    if event.agent_peer_id and event.agent_peer_id != effective.agent_peer_id:
        return False
    return event.peer_id in {effective.user_peer_id, effective.agent_peer_id}


def _config_for_event(event: OutboxEvent) -> EffectiveConfig:
    with _runtime_lock:
        configured = _runtime_configs.get(event.config_id) if event.config_id else None
        candidates = list(_runtime_configs.values()) if configured is None else []
    if configured and _event_matches_config(event, configured) and destination_ready(configured):
        return configured
    if not configured:
        for candidate in reversed(candidates):
            if _event_matches_config(event, candidate) and destination_ready(candidate):
                return candidate
    current = get_effective_config()
    if _event_matches_config(event, current) and destination_ready(current):
        _register_runtime_config(current)
        return current
    raise RuntimeConfigUnavailable("matching scoped Honcho configuration is unavailable")


def _delivery_client(effective: EffectiveConfig) -> Any:
    if not HONCHO_AVAILABLE:
        raise RuntimeConfigUnavailable("Honcho SDK is unavailable")
    key = (*effective.client_cache_key, effective.sync_timeout_seconds)
    with _runtime_lock:
        existing = _delivery_client_cache.get(key)
    if existing is not None:
        return existing
    client = _construct_client(
        effective,
        timeout=effective.sync_timeout_seconds,
        max_retries=0,
    )
    with _runtime_lock:
        existing = _delivery_client_cache.setdefault(key, client)
        while len(_delivery_client_cache) > 32:
            _delivery_client_cache.pop(next(iter(_delivery_client_cache)))
    return existing


def _ensure_remote_session(client: Any, effective: EffectiveConfig, session_id: str) -> Any:
    ready_key = (effective.routing_id, effective.key_digest, session_id)
    session = client.session(session_id)
    with _runtime_lock:
        ready = ready_key in _ready_sessions
    if ready:
        return session
    try:
        session.add_peers(_observation_roster(client, effective))
    except Exception as exc:
        if not _already_present(exc):
            raise
    with _runtime_lock:
        _ready_sessions.add(ready_key)
        while len(_ready_sessions) > 4096:
            _ready_sessions.pop()
    return session


def deliver_outbox_batch(events: list[OutboxEvent]) -> None:
    """Deliver one ordered, same-session lease in a single Honcho API request."""
    if not events:
        return
    first = events[0]
    if any(event.delivery_group != first.delivery_group for event in events):
        error = ValueError("mixed_delivery_group")
        error.status_code = 400  # type: ignore[attr-defined]
        raise error
    effective = _config_for_event(first)
    if any(not _event_matches_config(event, effective) for event in events):
        error = ValueError("event_identity_mismatch")
        error.status_code = 400  # type: ignore[attr-defined]
        raise error
    client = _delivery_client(effective)
    session = _ensure_remote_session(client, effective, first.session_id)
    messages = []
    for event in events:
        peer = client.peer(event.peer_id)
        messages.append(
            peer.message(
                event.content,
                metadata={
                    "source": "agent-zero",
                    "a0_event_id": event.event_id,
                    "role": event.role or (
                        "user" if event.peer_id == effective.user_peer_id else "assistant"
                    ),
                },
            )
        )
    session.add_messages(messages)
    # Honcho derives representations asynchronously. Invalidating immediately
    # can cache a pre-derivation response; bounded TTL/manual clear controls
    # freshness until a queue-aware refresh signal is available.


def initialize_runtime(
    context: Optional["AgentContext"] = None, *, start_worker: bool = True
):
    effective = get_effective_config(context)
    _register_runtime_config(effective)
    outbox = get_outbox(effective)
    outbox.initialize()
    if (
        not start_worker
        or not effective.capture_enabled
        or not destination_ready(effective)
    ):
        return None
    return start_singleton(
        outbox,
        deliver_outbox_batch,
        settings=effective.worker_settings,
    )


def stop_runtime(*, drain_seconds: float = 0.0) -> None:
    stop_all(drain_seconds=min(30.0, max(0.0, float(drain_seconds))))


def outbox_status(context: Optional["AgentContext"] = None) -> dict[str, Any]:
    effective = get_effective_config(context)
    outbox = get_outbox(effective)
    counts = outbox.counts()
    worker = get_singleton(effective.outbox_path)
    worker_state = worker.status() if worker else {
        "running": False,
        "started_at": None,
        "stopped_at": None,
        "last_success_at": None,
        "last_error_at": None,
        "last_error_code": None,
        "last_latency_ms": None,
        "delivered": 0,
        "retried": 0,
        "dead_lettered": 0,
        "in_flight": 0,
        "circuits": [],
        "settings": asdict(effective.worker_settings),
    }
    worker_state.pop("owner", None)
    return {
        "counts": counts,
        "timing": outbox.timing(),
        "worker": worker_state,
        "schema_version": outbox.schema_version(),
    }


def retry_dead_letters(
    context: Optional["AgentContext"] = None,
    *,
    limit: int = 100,
) -> int:
    effective = get_effective_config(context)
    count = get_outbox(effective).retry_dead_letters(
        limit=max(1, min(1000, int(limit)))
    )
    worker = initialize_runtime(context, start_worker=True)
    if worker:
        worker.reset_circuits()
        worker.wake()
    return count


def wake_worker(context: Optional["AgentContext"] = None) -> bool:
    worker = initialize_runtime(context, start_worker=True)
    if not worker:
        return False
    worker.wake()
    return True


def drain_worker(context: Optional["AgentContext"] = None, *, timeout: float = 5) -> dict[str, Any]:
    worker = initialize_runtime(context, start_worker=True)
    if not worker:
        return {"drained": False, "counts": get_outbox(get_effective_config(context)).counts()}
    return worker.drain(timeout=min(15.0, max(0.1, float(timeout))))


def _context_text(value: Any, *, max_chars: int = 12000) -> Optional[str]:
    parts: list[str] = []
    messages = list(getattr(value, "messages", None) or [])[-3:]
    message_lines = []
    for message in messages:
        content = str(getattr(message, "content", "") or "").strip()
        if content:
            message_lines.append(
                f"- {getattr(message, 'peer_id', 'peer')}: {content[:1000]}"
            )
    if message_lines:
        parts.append("Recent relevant session messages:\n" + "\n".join(message_lines))
    summary = getattr(value, "summary", None)
    summary_content = getattr(summary, "content", None)
    if summary_content:
        parts.append(f"Current session summary:\n{summary_content}")
    representation = getattr(value, "peer_representation", None)
    if representation:
        parts.append(f"Stable user representation and relevant facts:\n{representation}")
    card = getattr(value, "peer_card", None)
    if card:
        card_text = "\n".join(str(item) for item in card) if isinstance(card, (list, tuple)) else str(card)
        parts.append(f"Stable user card:\n{card_text}")
    text = "\n\n".join(parts).strip()
    return text[:max_chars] or None


def context_cache_key(
    effective: EffectiveConfig, session_id: str, max_tokens: int, search_query: str = ""
) -> tuple[str, str, str, str, str, int, str]:
    query_digest = hashlib.sha256(str(search_query or "").strip().encode("utf-8")).hexdigest()[:20]
    return (
        effective.api_url,
        effective.workspace_id,
        effective.agent_peer_id,
        effective.user_peer_id,
        session_id,
        max_tokens,
        query_digest,
    )


def get_user_context(
    context: "AgentContext",
    max_tokens: Optional[int] = None,
    *,
    search_query: str = "",
) -> Optional[str]:
    effective = get_effective_config(context)
    if not effective.recall_enabled or not ensure_initialized(context):
        return None
    budget = _positive_int(
        max_tokens if max_tokens is not None else effective.max_context_tokens,
        effective.max_context_tokens,
        maximum=4000,
    )
    raw_query = normalize_capture_content(search_query)[:2000]
    query = _mask_outbound(context, raw_query, effective) if raw_query else ""
    if raw_query and query is None:
        return None
    session_id = get_session_id(context)
    key = context_cache_key(effective, session_id, budget, query or "")
    now = time.monotonic()
    with _context_lock:
        cached = _context_cache.get(key)
        if cached and now - cached[0] < effective.cache_ttl:
            return cached[1]
        in_flight = _context_inflight.get(key)
        if in_flight is None:
            in_flight = threading.Event()
            _context_inflight[key] = in_flight
            leader = True
        else:
            leader = False
    if not leader:
        in_flight.wait(timeout=min(5.0, effective.sync_timeout_seconds))
        with _context_lock:
            refreshed = _context_cache.get(key)
            return refreshed[1] if refreshed else (cached[1] if cached else None)
    try:
        client = get_client(context, config=effective)
        if client is None:
            return cached[1] if cached else None
        result = client.session(session_id).context(
            summary=True,
            tokens=budget,
            peer_target=effective.user_peer_id,
            peer_perspective=effective.agent_peer_id,
            search_query=query or None,
            limit_to_session=False,
            search_top_k=effective.recall_search_top_k,
            search_max_distance=effective.recall_max_distance,
            include_most_frequent=True,
            max_conclusions=effective.recall_max_conclusions,
        )
        text = _context_text(result, max_chars=max(1000, budget * 6))
        if text:
            text = _mask_outbound(context, text, effective)
        with _context_lock:
            _context_cache[key] = (time.monotonic(), text)
            while len(_context_cache) > effective.context_cache_max_entries:
                _context_cache.pop(next(iter(_context_cache)))
        return text
    except Exception as exc:
        _log(context, f"Context error: {redact_text(exc, effective)}", "error")
        return cached[1] if cached else None
    finally:
        with _context_lock:
            event = _context_inflight.pop(key, None)
            if event:
                event.set()


def clear_context_cache(
    session_id: Optional[str] = None, *, effective: Optional[EffectiveConfig] = None
) -> None:
    with _context_lock:
        if session_id is None:
            _context_cache.clear()
            return
        identity = None
        if effective is not None:
            identity = (
                effective.api_url,
                effective.workspace_id,
                effective.agent_peer_id,
                effective.user_peer_id,
            )
        for key in [
            key for key in _context_cache if key[4] == session_id and (identity is None or key[:4] == identity)
        ]:
            _context_cache.pop(key, None)


def cache_status() -> dict[str, int]:
    with _context_lock, _runtime_lock:
        return {
            "clients": len(_client_cache),
            "delivery_clients": len(_delivery_client_cache),
            "contexts": len(_context_cache),
            "context_inflight": len(_context_inflight),
            "runtime_configs": len(_runtime_configs),
            "ready_sessions": len(_ready_sessions),
        }


def cleanup(context: Optional["AgentContext"] = None) -> None:
    if context is not None:
        clear_context_cache(get_session_id(context))
        return
    clear_context_cache()
    with _runtime_lock:
        _client_cache.clear()
        _delivery_client_cache.clear()
        _runtime_configs.clear()
        _ready_sessions.clear()
        _outbox_cache.clear()
    stop_runtime(drain_seconds=0)


def redact_text(value: Any, effective: Optional[EffectiveConfig] = None) -> str:
    text = str(value)
    keys = [effective.api_key] if effective and effective.api_key else []
    keys.extend(
        str(item or "").strip()
        for item in _load_secret_values(None).values()
        if str(item or "").strip()
    )
    for key in sorted(set(keys), key=len, reverse=True):
        text = text.replace(key, "***")
    text = re.sub(
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+",
        r"\1***",
        text,
    )
    return _neutralize_secret_aliases(text)[:4000]


def mask_outbound(
    context: "AgentContext", value: Any, effective: Optional[EffectiveConfig] = None
) -> Optional[str]:
    """Normalize and fail-closed redact text before sending it to Honcho."""
    cfg = effective or get_effective_config(context)
    normalized = normalize_capture_content(value)
    if not normalized:
        return ""
    masked = _mask_outbound(context, normalized, cfg)
    return masked if masked is not None else None


def bounded_result(
    value: Any,
    effective: Optional[EffectiveConfig] = None,
    *,
    limit: int = 6000,
) -> str:
    if isinstance(value, (dict, list, tuple)):
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            text = str(value)
    else:
        text = str(value)
    return redact_text(text, effective)[: max(1, min(20000, int(limit)))]


def endpoint_mode(url: str, hosted_default: str) -> str:
    normalized = _normalize_url(url)
    if not normalized:
        return "invalid"
    if normalized == _normalize_url(hosted_default):
        return "hosted"
    host = (urlparse(normalized).hostname or "").lower()
    return "hosted" if host.endswith(".honcho.dev") else "self-hosted"
