"""Capture successful primary-user messages into the local Honcho outbox."""

from agent import AgentContextType, UserMessage
from helpers.extension import Extension
from usr.plugins.honcho.helpers import honcho_helper


class HonchoCaptureUser(Extension):
    def execute(self, data: dict | None = None, **kwargs):
        del kwargs
        agent = self.agent
        if not agent:
            return
        context = agent.context
        # Agent Zero adds a synthetic initial user message while Agent(0) is
        # still being constructed. At that point ``context.agent0`` is absent;
        # skip it and capture only messages from the assigned primary agent.
        if (
            context.type != AgentContextType.USER
            or getattr(context, "agent0", None) is not agent
        ):
            return
        if not isinstance(data, dict) or data.get("exception") is not None:
            return

        message = None
        for value in data.get("args", ())[1:]:
            if isinstance(value, UserMessage):
                message = value
                break
        if message is None:
            candidate = data.get("kwargs", {}).get("message")
            if isinstance(candidate, UserMessage):
                message = candidate
        if message is None:
            return

        try:
            # Recall uses this already-redacted raw user intent rather than the
            # framework's protocol-wrapped history representation.
            honcho_helper.set_current_search_query(context, message.message)
            honcho_helper.enqueue_message(
                context,
                "user",
                message.message,
                metadata={
                    "id": message.id,
                    "source": "user_message",
                    "final": True,
                },
            )
        except Exception as exc:
            effective = honcho_helper.get_effective_config(context)
            honcho_helper._log(
                context,
                "User capture failed: "
                + honcho_helper.redact_text(type(exc).__name__, effective),
                "error",
            )
