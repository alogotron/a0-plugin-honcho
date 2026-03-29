"""
Honcho Initialization Extension
Initializes Honcho client when agent starts.
"""

from agent import AgentContext
from helpers.extension import Extension
from usr.plugins.honcho.helpers import honcho_helper


class HonchoInit(Extension):

    def execute(self, **kwargs):
        """Initialize Honcho integration for this agent context."""
        context: AgentContext = self.agent.context

        # Guard: context.agent0 may not be assigned yet during init
        if not hasattr(context, "agent0"):
            return

        try:
            if not honcho_helper.is_configured(context):
                return  # Not configured, skip silently

            client = honcho_helper.get_client(context)
            if client:
                session_id = honcho_helper.get_session_id(context)
                honcho_helper._log(
                    context,
                    f"Integration enabled for session: {session_id}",
                    "util",
                )

                if not hasattr(context, "_honcho"):
                    context._honcho = {}
                context._honcho["enabled"] = True
                context._honcho["session_id"] = session_id
        except Exception as e:
            honcho_helper._log(context, f"Init error: {e}", "error")
