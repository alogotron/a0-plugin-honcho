"""Capture only the final user-facing Agent Zero response."""

from agent import AgentContextType
from helpers.extension import Extension
from helpers.tool import Response
from usr.plugins.honcho.helpers import honcho_helper


class HonchoCaptureResponse(Extension):
    def execute(
        self,
        tool_name: str = "",
        response: Response | None = None,
        **kwargs,
    ):
        del kwargs
        agent = self.agent
        if not agent or tool_name != "response" or response is None:
            return
        context = agent.context
        if (
            context.type != AgentContextType.USER
            or getattr(context, "agent0", None) is not agent
        ):
            return
        if response.break_loop is not True:
            return

        try:
            honcho_helper.enqueue_message(
                context,
                "assistant",
                response.message,
                metadata={"source": "response_tool", "final": True},
            )
        except Exception as exc:
            effective = honcho_helper.get_effective_config(context)
            honcho_helper._log(
                context,
                "Final-response capture failed: "
                + honcho_helper.redact_text(type(exc).__name__, effective),
                "error",
            )
