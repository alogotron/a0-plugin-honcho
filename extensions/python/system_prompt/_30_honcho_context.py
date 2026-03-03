"""
Honcho Context Injection Extension
Injects user context from Honcho into the system prompt.
"""

import os
import sys

from agent import AgentContext
from python.helpers.extension import Extension

# Resolve plugin root and ensure helpers are importable
_PLUGIN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

from helpers import honcho_helper  # noqa: E402


class HonchoContext(Extension):

    async def execute(self, system_prompt: list[str] = [], **kwargs):
        """Add Honcho user context to system prompt."""
        context: AgentContext = self.agent.context

        try:
            max_tokens = 500
            if hasattr(context, 'agent0'):
                config = honcho_helper._get_plugin_config(context.agent0)
                max_tokens = config.get("honcho_max_context_tokens", 500)

            user_context = honcho_helper.get_user_context(
                context, max_tokens=max_tokens
            )

            if user_context and user_context.strip():
                prompt = self.agent.read_prompt(
                    "honcho.context.md",
                    user_context=user_context,
                )
                system_prompt.append(prompt)
        except Exception as e:
            honcho_helper._log(context, f"Context error (non-fatal): {e}", "error")
