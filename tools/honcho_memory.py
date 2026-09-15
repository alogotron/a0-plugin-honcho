"""Compact, policy-filtered native tool for deliberate Honcho memory operations."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from helpers.tool import Response, Tool
from usr.plugins.honcho.helpers import honcho_helper as hh

_ACTIONS = {"recall", "search", "ask", "remember", "forget", "status", "dream"}
_SCOPES = {"peer", "session", "workspace"}
_REASONING_LEVELS = {"minimal", "low", "medium", "high", "max"}
_CONCLUSION_ID = re.compile(r"^[A-Za-z0-9._:-]{1,256}$")


class HonchoMemory(Tool):
    async def execute(
        self,
        action: str = "",
        query: str = "",
        text: str = "",
        scope: str = "peer",
        limit: int = 5,
        conclusion_id: str = "",
        confirm: bool = False,
        reasoning_level: str = "minimal",
        **kwargs: Any,
    ) -> Response:
        del kwargs
        selected = str(action or self.args.get("action") or "status").strip().lower()
        context = self.agent.context
        effective = hh.get_effective_config(context)

        if selected not in _ACTIONS:
            return self._safe_response(
                context,
                effective,
                "Unknown action. Supported: " + ", ".join(sorted(_ACTIONS)) + ".",
            )

        try:
            if selected == "status":
                state = await asyncio.to_thread(hh.outbox_status, context)
                payload = {
                    "configured": hh.is_configured(context),
                    "mode": effective.mode,
                    "workspace_id": effective.workspace_id,
                    "user_peer_id": effective.user_peer_id,
                    "agent_peer_id": effective.agent_peer_id,
                    "outbox": state,
                }
                return self._safe_response(
                    context,
                    effective,
                    json.dumps(payload, ensure_ascii=False, default=str),
                )

            if selected == "recall":
                safe_query = self._safe_input(
                    context,
                    effective,
                    self._required(query, "query"),
                )
                result = await asyncio.to_thread(
                    hh.get_user_context,
                    context,
                    effective.max_context_tokens,
                    search_query=safe_query,
                )
                return self._safe_response(
                    context,
                    effective,
                    result or "No relevant Honcho memory was found.",
                )

            initialized = await asyncio.to_thread(hh.ensure_initialized, context)
            client = hh.get_client(context, config=effective) if initialized else None
            if client is None:
                return self._safe_response(
                    context,
                    effective,
                    "Honcho is not configured, reachable, or available through its SDK.",
                )

            user = client.peer(effective.user_peer_id)
            observer = client.peer(effective.agent_peer_id)
            conclusions = observer.conclusions_of(user)
            session_id = hh.get_session_id(context)

            if selected == "search":
                safe_query = self._safe_input(
                    context,
                    effective,
                    self._required(query, "query"),
                )
                bounded_limit = self._bounded_int(limit, default=5, minimum=1, maximum=20)
                selected_scope = str(scope or "peer").strip().lower()
                if selected_scope not in _SCOPES:
                    raise ValueError("scope must be peer, session, or workspace")
                if selected_scope == "peer":
                    items = await asyncio.to_thread(
                        conclusions.query,
                        safe_query,
                        bounded_limit,
                        effective.recall_max_distance,
                    )
                elif selected_scope == "session":
                    items = await asyncio.to_thread(
                        client.session(session_id).search,
                        safe_query,
                        limit=bounded_limit,
                    )
                else:
                    items = await asyncio.to_thread(
                        client.search,
                        safe_query,
                        limit=bounded_limit,
                    )
                return self._safe_response(
                    context,
                    effective,
                    self._format_items(items, bounded_limit),
                )

            if selected == "ask":
                safe_query = self._safe_input(
                    context,
                    effective,
                    self._required(query, "query"),
                )
                level = str(reasoning_level or "minimal").strip().lower()
                if level not in _REASONING_LEVELS:
                    raise ValueError(
                        "reasoning_level must be minimal, low, medium, high, or max"
                    )
                answer = await asyncio.to_thread(
                    observer.chat,
                    safe_query,
                    target=user,
                    session=session_id,
                    reasoning_level=level,
                )
                return self._safe_response(
                    context,
                    effective,
                    answer or "Honcho returned no answer.",
                )

            if selected == "remember":
                memory = self._safe_input(
                    context,
                    effective,
                    self._required(text or query, "text"),
                )[:4000]
                created = await asyncio.to_thread(
                    conclusions.create,
                    [{"content": memory, "session_id": session_id}],
                )
                hh.clear_context_cache(session_id, effective=effective)
                return self._safe_response(
                    context,
                    effective,
                    "Remembered: " + self._format_items(created, 3),
                )

            if selected == "forget":
                identifier = self._required(conclusion_id, "conclusion_id")
                if not _CONCLUSION_ID.fullmatch(identifier):
                    raise ValueError("conclusion_id has an invalid format")
                if confirm is not True:
                    return self._safe_response(
                        context,
                        effective,
                        "Refusing destructive forget: pass the exact conclusion_id "
                        "and confirm=true after user confirmation.",
                    )
                await asyncio.to_thread(conclusions.delete, identifier)
                hh.clear_context_cache(session_id, effective=effective)
                return self._safe_response(
                    context,
                    effective,
                    f"Deleted conclusion {identifier}.",
                )

            await asyncio.to_thread(
                client.schedule_dream,
                observer=observer,
                session=session_id,
                observed=user,
            )
            return self._safe_response(
                context,
                effective,
                "Honcho consolidation was scheduled for this chat.",
            )
        except (TypeError, ValueError) as exc:
            return self._safe_response(
                context,
                effective,
                f"Invalid request: {hh.redact_text(exc, effective)}",
            )
        except Exception as exc:
            return self._safe_response(
                context,
                effective,
                f"Honcho {selected} failed: {hh.redact_text(type(exc).__name__, effective)}",
            )

    @staticmethod
    def _required(value: Any, name: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{name} is required")
        return text

    @staticmethod
    def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            return max(minimum, min(maximum, int(value)))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_input(context: Any, effective: Any, value: Any) -> str:
        masked = hh.mask_outbound(context, value, effective)
        if masked is None:
            raise ValueError("content could not be safely redacted")
        if not masked.strip():
            raise ValueError("content is empty after redaction")
        return masked

    @staticmethod
    def _format_items(items: Iterable[Any], limit: int) -> str:
        lines: list[str] = []
        for item in items:
            if isinstance(item, Mapping):
                identifier = str(item.get("id") or "").strip()
                peer_id = str(item.get("peer_id") or "").strip()
                content = str(item.get("content") or "").strip()
            else:
                identifier = str(getattr(item, "id", "") or "").strip()
                peer_id = str(getattr(item, "peer_id", "") or "").strip()
                content = str(getattr(item, "content", "") or "").strip()
            if not content:
                continue
            prefix = " · ".join(part for part in (identifier, peer_id) if part)
            line = f"{prefix}: {content}" if prefix else content
            lines.append(line[:1400])
            if len(lines) >= limit:
                break
        return "\n".join(lines) or "No results."

    @staticmethod
    def _safe_response(context: Any, effective: Any, message: Any) -> Response:
        bounded = hh.bounded_result(message, effective, limit=6000)
        masked = hh.mask_outbound(context, bounded, effective)
        if masked is None:
            masked = "Honcho output could not be safely redacted."
        return Response(message=masked[:6000], break_loop=False)
