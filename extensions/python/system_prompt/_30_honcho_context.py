"""
Honcho Context Injection Extension
Injects user context from Honcho into the system prompt.
"""

from agent import AgentContext
from helpers.extension import Extension
from usr.plugins.honcho.helpers import honcho_helper


class HonchoContext(Extension):

    def execute(self, system_prompt: list[str] = [], **kwargs):
        """Add Honcho user context to system prompt."""
        context: AgentContext = self.agent.context

        # Guard: context.agent0 may not be assigned yet during init
        if not hasattr(context, "agent0"):
            return

        try:
            max_tokens = 500
            agent0 = getattr(context, "agent0", None)
            if agent0:
                config = honcho_helper._get_plugin_config(agent0)
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
