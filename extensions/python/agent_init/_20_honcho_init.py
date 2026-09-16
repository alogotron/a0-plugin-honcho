"""Initialize the local durable Honcho runtime for an agent."""

from agent import AgentContextType
from helpers.extension import Extension
from usr.plugins.honcho.helpers import honcho_helper


class HonchoInit(Extension):
    def execute(self, **kwargs):
        del kwargs
        agent = self.agent
        if not agent:
            return
        context = agent.context
        # AgentContext assigns ``agent0`` only after Agent(0) finishes
        # construction, while this extension runs from inside that constructor.
        if getattr(agent, "number", None) != 0 or context.type != AgentContextType.USER:
            return
        try:
            # Local schema initialization and daemon startup only. Remote session
            # creation is deferred to the worker or an explicit recall operation.
            honcho_helper.initialize_runtime(context, start_worker=True)
            # Best-effort Honcho scope registration (SDK >= 2.4.0) happens on a
            # daemon thread so agent startup is never blocked by remote I/O.
            honcho_helper.register_session_scope_async(context)
        except Exception as exc:
            effective = honcho_helper.get_effective_config(context)
            honcho_helper._log(
                context,
                "Runtime initialization failed: "
                + honcho_helper.redact_text(type(exc).__name__, effective),
                "error",
            )
