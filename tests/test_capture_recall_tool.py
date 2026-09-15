from __future__ import annotations

import asyncio
from dataclasses import dataclass
import importlib.util
from pathlib import Path
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

from helpers import honcho_helper as hh


def config(**updates):
    values = dict(
        api_key="key",
        api_url="http://api",
        mcp_url="http://mcp",
        workspace_id="workspace",
        user_peer_id="user",
        agent_peer_id="agent",
        cache_ttl=120,
        max_context_tokens=500,
        debug=False,
    )
    values.update(updates)
    return hh.EffectiveConfig(**values)


def context(identifier: str = "ctx"):
    return SimpleNamespace(id=identifier, agent0=SimpleNamespace(history=None))


class CaptureRecallTests(unittest.TestCase):
    def setUp(self):
        hh.cleanup()

    def tearDown(self):
        hh.cleanup()

    def test_capture_policy_excludes_internal_tool_protocol_and_intermediate(self):
        self.assertFalse(hh.should_capture("system", "system text"))
        self.assertFalse(hh.should_capture("assistant", "analysis", {"kind": "reasoning"}))
        self.assertFalse(hh.should_capture("assistant", "partial", {"is_final": False}))
        self.assertFalse(hh.should_capture("user", "[PROTOCOL]\nwrapped"))
        self.assertFalse(
            hh.should_capture(
                "assistant",
                '{"tool_name":"x","tool_args":{}}',
            )
        )
        self.assertTrue(
            hh.should_capture("user", "real request", {"source": "user_message"})
        )
        self.assertTrue(
            hh.should_capture(
                "assistant",
                "final answer",
                {"source": "response_tool", "final": True},
            )
        )

    def test_redaction_failure_prevents_persistence_and_network(self):
        cfg = config()
        ctx = context()
        with (
            mock.patch.object(hh, "get_effective_config", return_value=cfg),
            mock.patch.object(hh, "_mask_outbound", return_value=None),
            mock.patch.object(hh, "get_outbox") as get_outbox,
            mock.patch.object(hh, "get_client") as get_client,
        ):
            self.assertFalse(
                hh.enqueue_message(
                    ctx,
                    "user",
                    "sensitive",
                    metadata={"source": "user_message"},
                )
            )
        get_outbox.assert_not_called()
        get_client.assert_not_called()

    def test_adaptive_recall_passes_query_budget_and_identity(self):
        cfg = config(
            recall_search_top_k=7,
            recall_max_distance=0.55,
            recall_max_conclusions=11,
        )
        ctx = context("adaptive")
        calls = []

        class Session:
            def context(self, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(
                    messages=[SimpleNamespace(peer_id="user", content="relevant")],
                    summary=SimpleNamespace(content="summary"),
                    peer_representation="prefers concise answers",
                    peer_card=["stable fact"],
                )

        client = SimpleNamespace(session=lambda session_id: Session())
        with (
            mock.patch.object(hh, "get_effective_config", return_value=cfg),
            mock.patch.object(hh, "ensure_initialized", return_value=True),
            mock.patch.object(hh, "get_client", return_value=client),
            mock.patch.object(hh, "_mask_outbound", side_effect=lambda _c, value, _e: value),
        ):
            result = hh.get_user_context(ctx, 321, search_query="deployment prefs")
            cached = hh.get_user_context(ctx, 321, search_query="deployment prefs")
        self.assertEqual(result, cached)
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0],
            {
                "summary": True,
                "tokens": 321,
                "peer_target": "user",
                "peer_perspective": "agent",
                "search_query": "deployment prefs",
                "limit_to_session": False,
                "search_top_k": 7,
                "search_max_distance": 0.55,
                "include_most_frequent": True,
                "max_conclusions": 11,
            },
        )
        self.assertIn("summary", result)
        self.assertIn("prefers concise answers", result)

    def test_recall_cache_is_query_and_budget_isolated(self):
        cfg = config()
        key_a = hh.context_cache_key(cfg, "chat", 200, "alpha")
        key_b = hh.context_cache_key(cfg, "chat", 200, "beta")
        key_c = hh.context_cache_key(cfg, "chat", 300, "alpha")
        self.assertEqual(len({key_a, key_b, key_c}), 3)

    def test_single_flight_coalesces_concurrent_recall(self):
        cfg = config(sync_timeout_seconds=2)
        ctx = context("single-flight")
        entered = threading.Event()
        release = threading.Event()
        calls = []

        class Session:
            def context(self, **kwargs):
                calls.append(kwargs)
                entered.set()
                release.wait(2)
                return SimpleNamespace(
                    messages=[],
                    summary=SimpleNamespace(content="one result"),
                    peer_representation=None,
                    peer_card=None,
                )

        client = SimpleNamespace(session=lambda session_id: Session())
        results = []

        def run():
            results.append(hh.get_user_context(ctx, search_query="same query"))

        patches = (
            mock.patch.object(hh, "get_effective_config", return_value=cfg),
            mock.patch.object(hh, "ensure_initialized", return_value=True),
            mock.patch.object(hh, "get_client", return_value=client),
            mock.patch.object(hh, "_mask_outbound", side_effect=lambda _c, value, _e: value),
        )
        with patches[0], patches[1], patches[2], patches[3]:
            first = threading.Thread(target=run)
            second = threading.Thread(target=run)
            first.start()
            self.assertTrue(entered.wait(1))
            second.start()
            time.sleep(0.05)
            release.set()
            first.join(2)
            second.join(2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(results, ["Current session summary:\none result"] * 2)

    def test_failed_refresh_returns_stale_cached_value(self):
        cfg = config(cache_ttl=1)
        ctx = context("stale")
        key = hh.context_cache_key(cfg, "chat-stale", 500, "query")
        hh._context_cache[key] = (time.monotonic() - 10, "stale value")
        client = SimpleNamespace(
            session=lambda session_id: SimpleNamespace(
                context=lambda **kwargs: (_ for _ in ()).throw(TimeoutError())
            )
        )
        with (
            mock.patch.object(hh, "get_effective_config", return_value=cfg),
            mock.patch.object(hh, "ensure_initialized", return_value=True),
            mock.patch.object(hh, "get_client", return_value=client),
            mock.patch.object(hh, "_mask_outbound", side_effect=lambda _c, value, _e: value),
            mock.patch.object(hh, "_log"),
        ):
            self.assertEqual(
                hh.get_user_context(ctx, search_query="query"),
                "stale value",
            )


# Import the plugin tool against this project helper instead of any live plugin.
# Project-local `helpers/` intentionally shadows Agent Zero's framework package
# during unittest discovery, so provide the tiny Tool contract the plugin needs.
helpers_tool = ModuleType("helpers.tool")


@dataclass
class Response:
    message: str
    break_loop: bool
    additional: dict | None = None


class Tool:
    def __init__(self, agent, name, method, args, message, loop_data, **kwargs):
        del kwargs
        self.agent = agent
        self.name = name
        self.method = method
        self.args = args
        self.message = message
        self.loop_data = loop_data


helpers_tool.Response = Response
helpers_tool.Tool = Tool
sys.modules["helpers.tool"] = helpers_tool

usr = sys.modules.setdefault("usr", ModuleType("usr"))
plugins = sys.modules.setdefault("usr.plugins", ModuleType("usr.plugins"))
honcho = sys.modules.setdefault("usr.plugins.honcho", ModuleType("usr.plugins.honcho"))
honcho_helpers = ModuleType("usr.plugins.honcho.helpers")
honcho_helpers.honcho_helper = hh
sys.modules["usr.plugins.honcho.helpers"] = honcho_helpers
setattr(usr, "plugins", plugins)
setattr(plugins, "honcho", honcho)
setattr(honcho, "helpers", honcho_helpers)

SPEC = importlib.util.spec_from_file_location(
    "honcho_memory_under_test",
    Path(__file__).parents[1] / "tools" / "honcho_memory.py",
)
TOOL_MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(TOOL_MODULE)


class ToolTests(unittest.TestCase):
    def make_tool(self):
        ctx = context("tool")
        agent = SimpleNamespace(context=ctx)
        return TOOL_MODULE.HonchoMemory(agent, "honcho_memory", None, {}, "", None), ctx

    def test_forget_requires_exact_id_and_confirmation_before_delete(self):
        tool, ctx = self.make_tool()
        cfg = config()
        deleted = []
        scope = SimpleNamespace(delete=lambda identifier: deleted.append(identifier))
        observer = SimpleNamespace(conclusions_of=lambda target: scope)
        user = SimpleNamespace()
        client = SimpleNamespace(peer=lambda peer_id: observer if peer_id == "agent" else user)
        with (
            mock.patch.object(hh, "get_effective_config", return_value=cfg),
            mock.patch.object(hh, "ensure_initialized", return_value=True),
            mock.patch.object(hh, "get_client", return_value=client),
            mock.patch.object(hh, "mask_outbound", side_effect=lambda _c, value, _e: str(value)),
        ):
            response = asyncio.run(
                tool.execute(action="forget", conclusion_id="conclusion-1", confirm=False)
            )
        self.assertFalse(response.break_loop)
        self.assertIn("Refusing destructive forget", response.message)
        self.assertEqual(deleted, [])

    def test_forget_deletes_only_confirmed_exact_conclusion(self):
        tool, ctx = self.make_tool()
        cfg = config()
        deleted = []
        scope = SimpleNamespace(delete=lambda identifier: deleted.append(identifier))
        observer = SimpleNamespace(conclusions_of=lambda target: scope)
        user = SimpleNamespace()
        client = SimpleNamespace(peer=lambda peer_id: observer if peer_id == "agent" else user)
        with (
            mock.patch.object(hh, "get_effective_config", return_value=cfg),
            mock.patch.object(hh, "ensure_initialized", return_value=True),
            mock.patch.object(hh, "get_client", return_value=client),
            mock.patch.object(hh, "mask_outbound", side_effect=lambda _c, value, _e: str(value)),
            mock.patch.object(hh, "clear_context_cache"),
        ):
            response = asyncio.run(
                tool.execute(action="forget", conclusion_id="conclusion-1", confirm=True)
            )
        self.assertEqual(deleted, ["conclusion-1"])
        self.assertIn("Deleted conclusion conclusion-1", response.message)

    def test_tool_rejects_redaction_failure_without_sdk_call(self):
        tool, ctx = self.make_tool()
        cfg = config()
        with (
            mock.patch.object(hh, "get_effective_config", return_value=cfg),
            mock.patch.object(hh, "ensure_initialized", return_value=True),
            mock.patch.object(hh, "get_client") as get_client,
            mock.patch.object(hh, "mask_outbound", return_value=None),
        ):
            response = asyncio.run(tool.execute(action="recall", query="secret"))
        get_client.assert_not_called()
        self.assertEqual(
            response.message,
            "Honcho output could not be safely redacted.",
        )

    def test_status_output_is_bounded_and_contains_no_outbox_path(self):
        tool, ctx = self.make_tool()
        cfg = config(outbox_path="/private/secret/path.sqlite3")
        state = {
            "counts": {"pending": 1, "dead": 0},
            "worker": {"running": True},
            "schema_version": 2,
        }
        with (
            mock.patch.object(hh, "get_effective_config", return_value=cfg),
            mock.patch.object(hh, "outbox_status", return_value=state),
            mock.patch.object(hh, "is_configured", return_value=True),
            mock.patch.object(hh, "mask_outbound", side_effect=lambda _c, value, _e: str(value)),
        ):
            response = asyncio.run(tool.execute(action="status"))
        self.assertLessEqual(len(response.message), 6000)
        self.assertNotIn(cfg.outbox_path, response.message)
        self.assertNotIn(cfg.api_key, response.message)
        self.assertIn('"pending": 1', response.message)


if __name__ == "__main__":
    unittest.main()
