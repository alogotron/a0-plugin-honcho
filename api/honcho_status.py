"""Sanitized status, connectivity, and bounded outbox operations for Honcho."""

from __future__ import annotations

import asyncio
import importlib
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request as UrlRequest, urlopen

from helpers.api import ApiHandler, Request, Response


def _helper():
    for path in (
        "usr.plugins.honcho.helpers.honcho_helper",
        "plugins.honcho.helpers.honcho_helper",
        "helpers.honcho_helper",
    ):
        try:
            return importlib.import_module(path)
        except ModuleNotFoundError:
            continue
    raise ModuleNotFoundError("Honcho helper is not importable")


def _context(input: dict):
    context_id = str(input.get("context_id") or input.get("context") or "").strip()
    if not context_id:
        return None
    try:
        from agent import AgentContext

        return AgentContext.get(context_id)
    except Exception:
        return None


def _api_probe(hh, context, effective) -> dict:
    del context
    if not hh.HONCHO_AVAILABLE:
        return {"ok": False, "detail": "Honcho SDK is unavailable"}
    if not effective.api_key and effective.mode == "hosted":
        return {"ok": False, "detail": "HONCHO_API_KEY is not configured"}
    try:
        hh.get_probe_client(effective).get_metadata()
        detail = "Authenticated API request succeeded" if effective.api_key else "API request succeeded"
        return {"ok": True, "detail": detail}
    except Exception as exc:
        return {"ok": False, "detail": hh.redact_text(exc, effective)[:240]}


def _mcp_probe(hh, effective) -> dict:
    endpoint = urlsplit(effective.mcp_url)
    url = urlunsplit((endpoint.scheme, endpoint.netloc, "/health", "", ""))
    try:
        request = UrlRequest(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=5) as response:  # noqa: S310 - configured endpoint
            status = int(getattr(response, "status", 200))
        return {"ok": 200 <= status < 300, "detail": f"GET /health returned HTTP {status}"}
    except Exception as exc:
        return {"ok": False, "detail": hh.redact_text(exc, effective)[:240]}


def _bounded_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def _scope_status(hh, context, effective) -> dict:
    """Sanitized scope view: strategy, resolved ID, and SDK support flag."""
    resolved = None
    if hasattr(hh, "resolve_scope_id"):
        try:
            resolved = hh.resolve_scope_id(effective, context)
        except Exception:
            resolved = None
    supported = False
    if hasattr(hh, "scope_supported"):
        try:
            supported = bool(hh.scope_supported())
        except Exception:
            supported = False
    return {
        "strategy": str(getattr(effective, "scope_strategy", "off") or "off"),
        "resolved": resolved,
        "view": resolved or "global",
        "sdk_support": supported,
    }


def _scope_probe(hh, effective, scope_id: str) -> dict:
    """Optional cheap scope membership/backfill check; never raises or leaks."""
    if not scope_id:
        return {"ok": False, "detail": "No scope is resolved (global view)"}
    try:
        client = hh.get_probe_client(effective)
        scope = client.scope(id=scope_id)
        status = scope.status()
        summary = hh.bounded_result(
            {key: getattr(status, key) for key in vars(status) if not key.startswith("_")}
            if hasattr(status, "__dict__")
            else status,
            effective,
            limit=400,
        )
        return {"ok": True, "detail": f"Scope status: {summary}"}
    except Exception as exc:
        return {"ok": False, "detail": hh.redact_text(exc, effective)[:240]}


class HonchoStatus(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        del request
        action = str(input.get("action") or "status").strip().lower()
        try:
            hh = _helper()
        except Exception as exc:
            return {
                "ok": False,
                "sdk_available": False,
                "error": f"helper import failed: {type(exc).__name__}",
            }

        context = _context(input)
        effective = hh.get_effective_config(context)

        if action in {"clear_cache", "retry_dead_letters", "wake", "drain"}:
            try:
                if action == "clear_cache":
                    if context is None:
                        hh.clear_context_cache()
                    else:
                        hh.clear_context_cache(
                            hh.get_session_id(context), effective=effective
                        )
                    return {
                        "ok": True,
                        "cleared": True,
                        "cache": hh.cache_status(),
                    }
                if action == "retry_dead_letters":
                    limit = _bounded_int(input.get("limit"), 100, 1, 1000)
                    retried = hh.retry_dead_letters(context, limit=limit)
                    return {
                        "ok": True,
                        "retried": retried,
                        "outbox": hh.outbox_status(context),
                    }
                if action == "wake":
                    awakened = hh.wake_worker(context)
                    return {
                        "ok": awakened,
                        "outbox": hh.outbox_status(context),
                    }
                timeout = _bounded_int(input.get("timeout"), 5, 1, 15)
                result = await asyncio.to_thread(
                    hh.drain_worker,
                    context,
                    timeout=timeout,
                )
                return {
                    "ok": bool(result.get("drained")),
                    "drain": result,
                    "outbox": hh.outbox_status(context),
                }
            except Exception as exc:
                return {
                    "ok": False,
                    "error": "operation_failed",
                    "detail": hh.redact_text(type(exc).__name__, effective)[:80],
                }
        if action not in {"status", "ping"}:
            return Response(status=400, response=f"Unknown Honcho action: {action}")

        cache = hh.cache_status()
        outbox_state = (
            hh.outbox_status(context)
            if hasattr(hh, "outbox_status")
            else {"counts": {}, "worker": {"running": False}, "schema_version": 0}
        )
        payload = {
            "ok": True,
            "sdk_available": bool(hh.HONCHO_AVAILABLE),
            "configured": bool(
                hh.HONCHO_AVAILABLE and (effective.api_key or effective.mode == "self-hosted")
            ),
            "api_key_present": bool(effective.api_key),
            "api_key_masked": "***" if effective.api_key else "",
            "api_url": effective.api_url,
            "mcp_url": effective.mcp_url,
            "mode": effective.mode,
            "mcp_mode": effective.mcp_mode,
            "workspace_id": effective.workspace_id,
            "user_peer_id": effective.user_peer_id,
            "agent_peer_id": effective.agent_peer_id,
            "cache_ttl": effective.cache_ttl,
            "max_context_tokens": effective.max_context_tokens,
            "debug": effective.debug,
            "capture_enabled": bool(getattr(effective, "capture_enabled", True)),
            "recall_enabled": bool(getattr(effective, "recall_enabled", True)),
            "session_id": hh.get_session_id(context) if context is not None else None,
            "scope": _scope_status(hh, context, effective),
            "config_scope": {
                "per_project": True,
                "per_agent": False,
                "detail": "Project-scoped settings; runtime secret/environment overrides are process-wide.",
            },
            "cache": cache,
            "outbox": outbox_state,
            "client_cached": cache["clients"] > 0,
            "context_cache_entries": cache["contexts"],
            "checks": None,
        }
        if action == "ping":
            api_check, mcp_check = await asyncio.gather(
                asyncio.to_thread(_api_probe, hh, context, effective),
                asyncio.to_thread(_mcp_probe, hh, effective),
            )
            payload["checks"] = {"api": api_check, "mcp": mcp_check}
            scope_view = payload["scope"]
            if scope_view["resolved"] and scope_view["sdk_support"]:
                payload["checks"]["scope"] = await asyncio.to_thread(
                    _scope_probe, hh, effective, scope_view["resolved"]
                )
            payload["ping"] = {
                "reachable": bool(api_check["ok"] and mcp_check["ok"]),
                "detail": f"API: {api_check['detail']} · MCP: {mcp_check['detail']}",
            }
        return payload
