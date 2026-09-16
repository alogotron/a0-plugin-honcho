"""Inject adaptive, token-bounded Honcho context into primary user chats."""

from __future__ import annotations

import asyncio

from agent import AgentContextType
from helpers.extension import Extension
from usr.plugins.honcho.helpers import honcho_helper


class HonchoContext(Extension):
    async def execute(self, system_prompt: list[str], **kwargs):
        agent = self.agent
        if not agent:
            return
        context = agent.context
        if (
            context.type != AgentContextType.USER
            or getattr(context, "agent0", None) is not agent
        ):
            return
        loop_data = kwargs.get("loop_data")
        search_query = honcho_helper.get_current_search_query(context)
        if not search_query:
            user_message = getattr(loop_data, "user_message", None)
            search_query = honcho_helper.normalize_capture_content(
                getattr(user_message, "content", user_message)
            )
        try:
            effective = honcho_helper.get_effective_config(context)
            user_context = await asyncio.to_thread(
                honcho_helper.get_user_context,
                context,
                effective.max_context_tokens,
                search_query=search_query,
                scope_id=honcho_helper.resolve_scope_id(effective, context),
            )
            if user_context and user_context.strip():
                system_prompt.append(
                    agent.read_prompt(
                        "honcho.context.md",
                        user_context=user_context,
                    )
                )
        except Exception as exc:
            effective = honcho_helper.get_effective_config(context)
            honcho_helper._log(
                context,
                "Context error (non-fatal): "
                + honcho_helper.redact_text(type(exc).__name__, effective),
                "error",
            )
