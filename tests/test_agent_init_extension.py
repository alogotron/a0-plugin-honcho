from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock


class AgentInitExtensionTests(unittest.TestCase):
    def _load_extension(self):
        agent_module = ModuleType("agent")
        agent_module.AgentContextType = SimpleNamespace(USER="user")

        extension_module = ModuleType("helpers.extension")

        class Extension:
            def __init__(self, agent=None):
                self.agent = agent

        extension_module.Extension = Extension

        helper = mock.Mock()
        honcho_helpers = ModuleType("usr.plugins.honcho.helpers")
        honcho_helpers.honcho_helper = helper

        modules = {
            "agent": agent_module,
            "helpers.extension": extension_module,
            "usr": ModuleType("usr"),
            "usr.plugins": ModuleType("usr.plugins"),
            "usr.plugins.honcho": ModuleType("usr.plugins.honcho"),
            "usr.plugins.honcho.helpers": honcho_helpers,
        }
        path = (
            Path(__file__).parents[1]
            / "extensions/python/agent_init/_20_honcho_init.py"
        )
        spec = importlib.util.spec_from_file_location("honcho_agent_init_test", path)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, modules):
            assert spec and spec.loader
            spec.loader.exec_module(module)
        return module.HonchoInit, helper

    def test_primary_agent_initializes_before_context_agent0_is_assigned(self):
        extension, helper = self._load_extension()
        context = SimpleNamespace(type="user")  # Deliberately no context.agent0 yet.
        agent = SimpleNamespace(number=0, context=context)

        extension(agent=agent).execute()

        helper.initialize_runtime.assert_called_once_with(context, start_worker=True)

    def test_subordinate_agent_does_not_initialize_runtime(self):
        extension, helper = self._load_extension()
        context = SimpleNamespace(type="user")
        agent = SimpleNamespace(number=1, context=context)

        extension(agent=agent).execute()

        helper.initialize_runtime.assert_not_called()


if __name__ == "__main__":
    unittest.main()
