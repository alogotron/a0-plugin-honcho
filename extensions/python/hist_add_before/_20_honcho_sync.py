"""
Honcho Message Sync Extension
Syncs messages to Honcho when added to conversation history.
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


class HonchoSync(Extension):

    async def execute(self, **kwargs):
        """Sync message to Honcho Cloud."""
        context: AgentContext = self.agent.context

        content_data = kwargs.get('content_data', {})
        ai = kwargs.get('ai', False)

        # Recursive content extraction
        raw_content = content_data
        while isinstance(raw_content, dict):
            extracted = (
                raw_content.get('content')
                or raw_content.get('text')
                or raw_content.get('message')
            )
            if extracted is None:
                raw_content = str(raw_content)
                break
            raw_content = extracted

        content = raw_content if isinstance(raw_content, str) else str(raw_content) if raw_content else ''
        role = 'assistant' if ai else 'user'

        if not content or not content.strip():
            return

        if not honcho_helper.is_configured(context):
            return

        try:
            success = honcho_helper.sync_message(context, role, content)
            if not success:
                honcho_helper._log(context, "sync_message returned False", "error")
        except Exception as e:
            honcho_helper._log(context, f"Sync error: {e}", "error")
