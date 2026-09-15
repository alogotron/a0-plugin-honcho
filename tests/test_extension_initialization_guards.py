from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock


class UserMessage:
    def __init__(self, message: str, id: str = "message-id"):
        self.message = message
        self.id = id


class ExtensionInitializationGuardTests(unittest.TestCase):
    def _load(self, relative_path: str):
        agent_module = ModuleType("agent")
        agent_module.AgentContextType = SimpleNamespace(USER="user")
        agent_module.UserMessage = UserMessage

        extension_module = ModuleType("helpers.extension")

        class Extension:
            def __init__(self, agent=None):
                self.agent = agent

        extension_module.Extension = Extension

        tool_module = ModuleType("helpers.tool")
        tool_module.Response = SimpleNamespace

        helper = mock.Mock()
        honcho_helpers = ModuleType("usr.plugins.honcho.helpers")
        honcho_helpers.honcho_helper = helper

        modules = {
            "agent": agent_module,
            "helpers.extension": extension_module,
            "helpers.tool": tool_module,
            "usr": ModuleType("usr"),
            "usr.plugins": ModuleType("usr.plugins"),
            "usr.plugins.honcho": ModuleType("usr.plugins.honcho"),
            "usr.plugins.honcho.helpers": honcho_helpers,
        }
        path = Path(__file__).parents[1] / relative_path
        module_name = "honcho_guard_test_" + path.stem + relative_path.replace("/", "_")
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, modules):
            assert spec and spec.loader
            spec.loader.exec_module(module)
        return module, helper

    def test_user_capture_skips_synthetic_message_before_agent0_assignment(self):
        module, helper = self._load(
            "extensions/python/_functions/agent/Agent/"
            "hist_add_user_message/end/_20_honcho_capture_user.py"
        )
        context = SimpleNamespace(type="user")  # AgentContext.agent0 is not assigned yet.
        agent = SimpleNamespace(number=0, context=context)
        message = UserMessage("Hello!")

        module.HonchoCaptureUser(agent=agent).execute(
            data={"args": (agent, message), "kwargs": {}, "exception": None}
        )

        helper.set_current_search_query.assert_not_called()
        helper.enqueue_message.assert_not_called()

    def test_user_capture_runs_for_assigned_primary_agent(self):
        module, helper = self._load(
            "extensions/python/_functions/agent/Agent/"
            "hist_add_user_message/end/_20_honcho_capture_user.py"
        )
        context = SimpleNamespace(type="user")
        agent = SimpleNamespace(number=0, context=context)
        context.agent0 = agent
        message = UserMessage("real request", id="real-id")

        module.HonchoCaptureUser(agent=agent).execute(
            data={"args": (agent, message), "kwargs": {}, "exception": None}
        )

        helper.set_current_search_query.assert_called_once_with(context, "real request")
        helper.enqueue_message.assert_called_once_with(
            context,
            "user",
            "real request",
            metadata={"id": "real-id", "source": "user_message", "final": True},
        )

    def test_prompt_hook_skips_before_agent0_assignment(self):
        module, helper = self._load(
            "extensions/python/system_prompt/_30_honcho_context.py"
        )
        context = SimpleNamespace(type="user")
        agent = SimpleNamespace(number=0, context=context)
        prompt: list[str] = []

        asyncio.run(module.HonchoContext(agent=agent).execute(prompt))

        self.assertEqual(prompt, [])
        helper.get_user_context.assert_not_called()

    def test_final_response_hook_skips_before_agent0_assignment(self):
        module, helper = self._load(
            "extensions/python/tool_execute_after/_80_honcho_capture_response.py"
        )
        context = SimpleNamespace(type="user")
        agent = SimpleNamespace(number=0, context=context)
        response = SimpleNamespace(message="initial response", break_loop=True)

        module.HonchoCaptureResponse(agent=agent).execute(
            tool_name="response", response=response
        )

        helper.enqueue_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
