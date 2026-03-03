"""
Honcho Initialization Extension
Initializes Honcho client when agent starts.
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


class HonchoInit(Extension):

    async def execute(self, **kwargs):
        """Initialize Honcho integration for this agent context."""
        context: AgentContext = self.agent.context

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

                if not hasattr(context, '_honcho'):
                    context._honcho = {}
                context._honcho['enabled'] = True
                context._honcho['session_id'] = session_id
        except Exception as e:
            honcho_helper._log(context, f"Init error: {e}", "error")
