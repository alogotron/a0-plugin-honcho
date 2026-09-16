"""Honcho scope support tests (SDK >= 2.4.0, feature-detected).

Covers slugging, strategy resolution, config precedence, feature-detected
fallback on scope-less SDKs, idempotent/non-fatal registration, context cache
key scope inclusion, recall scope threading, and tool scope validation.
Everything is mocked; no live credentials or network.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import importlib.util
import os
from pathlib import Path
import sys
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
        recall_enabled=True,
        recall_search_top_k=8,
        recall_max_distance=0.8,
        recall_max_conclusions=20,
        sync_timeout_seconds=5,
        debug=False,
        scope_strategy="off",
        scope_prefix="a0-",
        scope_custom="",
    )
    values.update(updates)
    return hh.EffectiveConfig(**values)


def context(identifier: str = "ctx", project: str | None = None):
    data = {"project": project}
    ctx = SimpleNamespace(
        id=identifier,
        agent0=SimpleNamespace(history=None),
        get_data=lambda key: data.get(key),
        set_data=lambda key, value: data.__setitem__(key, value),
    )
    return ctx


class ScopeSlugTests(unittest.TestCase):
    def test_lowercase_and_unsafe_chars(self):
        self.assertEqual(hh.slugify_scope_name("Honcho V2!"), "honcho-v2")
        self.assertEqual(hh.slugify_scope_name("My Project_X"), "my-project_x")
        self.assertEqual(hh.slugify_scope_name("--weird--name--"), "weird-name")
        self.assertEqual(hh.slugify_scope_name("UPPER case"), "upper-case")

    def test_empty_and_none(self):
        self.assertEqual(hh.slugify_scope_name(None), "")
        self.assertEqual(hh.slugify_scope_name("   "), "")
        self.assertEqual(hh.slugify_scope_name("!!!"), "")

    def test_length_bounded(self):
        self.assertLessEqual(len(hh.slugify_scope_name("a" * 500)), hh._SCOPE_MAX_LENGTH)

    def test_prefix_sanitized(self):
        self.assertEqual(hh.normalize_scope_prefix("a0-"), "a0")
        self.assertEqual(hh.normalize_scope_prefix(" A0_ "), "a0")
        self.assertEqual(hh.normalize_scope_prefix(""), "")


class StrategyResolutionTests(unittest.TestCase):
    def test_off_returns_none(self):
        eff = config(scope_strategy="off")
        self.assertIsNone(hh.resolve_scope_id(eff, context(project="honcho-v2")))

    def test_project_strategy_slugifies_active_project(self):
        eff = config(scope_strategy="project")
        self.assertEqual(
            hh.resolve_scope_id(eff, context(project="Honcho V2")),
            "a0-honcho-v2",
        )

    def test_project_strategy_without_project(self):
        eff = config(scope_strategy="project")
        self.assertIsNone(hh.resolve_scope_id(eff, None))
        self.assertIsNone(hh.resolve_scope_id(eff, context(project=None)))

    def test_custom_strategy(self):
        eff = config(scope_strategy="custom", scope_custom="My Team")
        self.assertEqual(
            hh.resolve_scope_id(eff, context(project="honcho-v2")),
            "a0-my-team",
        )

    def test_custom_strategy_empty_name(self):
        eff = config(scope_strategy="custom", scope_custom="")
        self.assertIsNone(hh.resolve_scope_id(eff, context(project="honcho-v2")))

    def test_empty_prefix_disables_prefixing(self):
        eff = config(scope_strategy="custom", scope_custom="team", scope_prefix="")
        self.assertEqual(hh.resolve_scope_id(eff, None), "team")

    def test_reference_resolution_accepts_plain_or_prefixed(self):
        eff = config()
        self.assertEqual(hh.resolve_scope_reference("honcho-v2", eff), "a0-honcho-v2")
        self.assertEqual(hh.resolve_scope_reference("a0-honcho-v2", eff), "a0-honcho-v2")
        self.assertIsNone(hh.resolve_scope_reference("  ", eff))
        self.assertIsNone(hh.resolve_scope_reference(None, eff))


class FeatureDetectionTests(unittest.TestCase):
    def test_scope_less_sdk_is_unsupported(self):
        class OldSDK:
            pass

        self.assertFalse(hh.scope_supported(OldSDK()))

    def test_scoped_sdk_is_supported(self):
        class NewSDK:
            def scope(self, **kwargs):
                return None

        self.assertTrue(hh.scope_supported(NewSDK()))

    def test_unavailable_sdk_is_unsupported(self):
        with mock.patch.object(hh, "HONCHO_AVAILABLE", False):
            self.assertFalse(hh.scope_supported(SimpleNamespace(scope=lambda: None)))


class ScopeRegistrationTests(unittest.TestCase):
    def setUp(self):
        hh.cleanup()

    def tearDown(self):
        hh.cleanup()

    def _client(self, scope_obj):
        return SimpleNamespace(scope=lambda **kwargs: scope_obj)

    def test_registers_session_into_resolved_scope(self):
        added = []
        scope_obj = SimpleNamespace(add_sessions=lambda ids: added.append(list(ids)))
        eff = config(scope_strategy="custom", scope_custom="team")
        with mock.patch.object(hh, "scope_supported", return_value=True):
            self.assertTrue(
                hh._ensure_scope_membership(self._client(scope_obj), eff, "session-1")
            )
        self.assertEqual(added, [["session-1"]])

    def test_registration_is_cached_idempotent(self):
        calls = []
        scope_obj = SimpleNamespace(add_sessions=lambda ids: None)

        def scope_factory(**kwargs):
            calls.append(kwargs)
            return scope_obj

        client = SimpleNamespace(scope=scope_factory)
        eff = config(scope_strategy="custom", scope_custom="team")
        with mock.patch.object(hh, "scope_supported", return_value=True):
            self.assertTrue(hh._ensure_scope_membership(client, eff, "session-1"))
            self.assertTrue(hh._ensure_scope_membership(client, eff, "session-1"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["id"], "a0-team")

    def test_already_member_response_is_tolerated(self):
        class ConflictError(Exception):
            status = 409

            def __str__(self):
                return "session already exists in scope"

        scope_obj = SimpleNamespace(
            add_sessions=lambda ids: (_ for _ in ()).throw(ConflictError())
        )
        eff = config(scope_strategy="custom", scope_custom="team")
        with mock.patch.object(hh, "scope_supported", return_value=True):
            self.assertTrue(
                hh._ensure_scope_membership(self._client(scope_obj), eff, "session-1")
            )

    def test_failure_is_non_fatal_and_not_cached(self):
        state = {"fail": True}

        def add_sessions(ids):
            if state["fail"]:
                raise ConnectionError("connection reset")

        scope_obj = SimpleNamespace(add_sessions=add_sessions)
        eff = config(scope_strategy="custom", scope_custom="team")
        ctx = context("reg")
        with mock.patch.object(hh, "scope_supported", return_value=True):
            self.assertFalse(
                hh._ensure_scope_membership(
                    self._client(scope_obj), eff, "session-1", context=ctx
                )
            )
            state["fail"] = False
            self.assertTrue(
                hh._ensure_scope_membership(
                    self._client(scope_obj), eff, "session-1", context=ctx
                )
            )

    def test_registration_skipped_when_off_or_unsupported(self):
        calls = []
        client = SimpleNamespace(scope=lambda **kwargs: calls.append(kwargs))
        with mock.patch.object(hh, "scope_supported", return_value=False):
            self.assertFalse(
                hh._ensure_scope_membership(
                    client, config(scope_strategy="custom", scope_custom="team"), "s"
                )
            )
        with mock.patch.object(hh, "scope_supported", return_value=True):
            self.assertFalse(
                hh._ensure_scope_membership(client, config(scope_strategy="off"), "s")
            )
        self.assertEqual(calls, [])

    def test_async_registration_never_blocks_and_delegates(self):
        eff = config(scope_strategy="custom", scope_custom="team")
        ctx = context("async")
        with (
            mock.patch.object(hh, "get_effective_config", return_value=eff),
            mock.patch.object(hh, "destination_ready", return_value=True),
            mock.patch.object(hh, "scope_supported", return_value=True),
            mock.patch.object(hh, "register_session_scope", return_value=True) as reg,
        ):
            self.assertTrue(hh.register_session_scope_async(ctx))
            deadline = time.monotonic() + 2
            while not reg.called and time.monotonic() < deadline:
                time.sleep(0.01)
            reg.assert_called_once_with(ctx, effective=eff)

    def test_async_registration_skips_when_disabled(self):
        ctx = context("async-off")
        with mock.patch.object(
            hh, "get_effective_config", return_value=config(scope_strategy="off")
        ):
            self.assertFalse(hh.register_session_scope_async(ctx))


class ScopeCacheKeyTests(unittest.TestCase):
    def test_scope_included_in_context_cache_key(self):
        eff = config()
        plain = hh.context_cache_key(eff, "s", 100, "q")
        scoped = hh.context_cache_key(eff, "s", 100, "q", "a0-team")
        other = hh.context_cache_key(eff, "s", 100, "q", "a0-other")
        self.assertNotEqual(plain, scoped)
        self.assertNotEqual(scoped, other)
        self.assertEqual(plain[-1], "")
        self.assertEqual(scoped[-1], "a0-team")


class RecallScopeTests(unittest.TestCase):
    def setUp(self):
        hh.cleanup()

    def tearDown(self):
        hh.cleanup()

    def _recall(self, supported):
        eff = config(scope_strategy="custom", scope_custom="team")
        ctx = context("recall")
        captured = {}

        class FakeSession:
            def context(self, **kwargs):
                captured.update(kwargs)
                return SimpleNamespace(
                    messages=[],
                    summary=SimpleNamespace(content="summary text"),
                    peer_representation=None,
                    peer_card=None,
                )

        client = SimpleNamespace(session=lambda session_id: FakeSession())
        with (
            mock.patch.object(hh, "get_effective_config", return_value=eff),
            mock.patch.object(hh, "ensure_initialized", return_value=True),
            mock.patch.object(hh, "get_client", return_value=client),
            mock.patch.object(hh, "scope_supported", return_value=supported),
            mock.patch.object(hh, "_mask_outbound", side_effect=lambda _c, v, _e: v),
        ):
            result = hh.get_user_context(ctx, 100)
        return result, captured

    def test_scope_threaded_into_session_context_when_supported(self):
        result, captured = self._recall(True)
        self.assertIn("summary text", result)
        self.assertEqual(captured.get("scope"), "a0-team")

    def test_scopeless_sdk_degrades_to_global_view(self):
        result, captured = self._recall(False)
        self.assertIn("summary text", result)
        self.assertNotIn("scope", captured)


class ScopeConfigPrecedenceTests(unittest.TestCase):
    def _effective(self, explicit, env):
        clean_env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("HONCHO_SCOPE_")
        }
        clean_env.update(env)
        with (
            mock.patch.dict(os.environ, clean_env, clear=True),
            mock.patch.object(
                hh, "_get_plugin_config", return_value={**hh._DEFAULTS, **explicit}
            ),
            mock.patch.object(hh, "_load_secret_values", return_value={}),
        ):
            return hh.get_effective_config(None)

    def test_env_overrides_explicit_config(self):
        eff = self._effective(
            {
                "honcho_scope_strategy": "off",
                "honcho_scope_prefix": "zz-",
                "honcho_scope_custom": "explicit-name",
            },
            {
                "HONCHO_SCOPE_STRATEGY": "custom",
                "HONCHO_SCOPE_PREFIX": "env-",
                "HONCHO_SCOPE_CUSTOM": "env-name",
            },
        )
        self.assertEqual(eff.scope_strategy, "custom")
        self.assertEqual(eff.scope_prefix, "env")
        self.assertEqual(eff.scope_custom, "env-name")

    def test_explicit_config_used_without_env(self):
        eff = self._effective(
            {
                "honcho_scope_strategy": "off",
                "honcho_scope_prefix": "zz-",
                "honcho_scope_custom": "explicit-name",
            },
            {},
        )
        self.assertEqual(eff.scope_strategy, "off")
        self.assertEqual(eff.scope_prefix, "zz")
        self.assertEqual(eff.scope_custom, "explicit-name")

    def test_defaults_and_invalid_strategy_fallback(self):
        eff = self._effective({}, {})
        self.assertEqual(eff.scope_strategy, "project")
        self.assertEqual(eff.scope_prefix, "a0")
        self.assertEqual(eff.scope_custom, "")
        bad = self._effective({"honcho_scope_strategy": "bogus"}, {})
        self.assertEqual(bad.scope_strategy, "project")


# Import the plugin tool against this project helper instead of any live plugin,
# mirroring tests/test_capture_recall_tool.py: the project-local `helpers/`
# package shadows Agent Zero's framework package during unittest discovery, so
# provide the tiny Tool contract the plugin needs.
helpers_tool = sys.modules.get("helpers.tool") or ModuleType("helpers.tool")


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
honcho_helpers = sys.modules.get("usr.plugins.honcho.helpers") or ModuleType(
    "usr.plugins.honcho.helpers"
)
honcho_helpers.honcho_helper = hh
sys.modules["usr.plugins.honcho.helpers"] = honcho_helpers
setattr(usr, "plugins", plugins)
setattr(plugins, "honcho", honcho)
setattr(honcho, "helpers", honcho_helpers)

SPEC = importlib.util.spec_from_file_location(
    "honcho_memory_scope_tests",
    Path(__file__).parents[1] / "tools" / "honcho_memory.py",
)
TOOL_MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(TOOL_MODULE)


class ToolScopeTests(unittest.TestCase):
    def make_tool(self):
        ctx = context("tool")
        agent = SimpleNamespace(context=ctx)
        return TOOL_MODULE.HonchoMemory(agent, "honcho_memory", None, {}, "", None), ctx

    def _client(self):
        observer = SimpleNamespace(
            chat=lambda query, **kwargs: "dialectic answer",
            conclusions_of=lambda target: SimpleNamespace(query=lambda *a, **k: []),
        )
        user = SimpleNamespace()
        return SimpleNamespace(
            peer=lambda peer_id: observer if peer_id == "agent" else user,
            session=lambda session_id: SimpleNamespace(
                search=lambda query, **kwargs: []
            ),
            search=lambda query, **kwargs: [],
        ), observer

    def test_search_domains_still_resolve(self):
        tool, ctx = self.make_tool()
        client, observer = self._client()
        with (
            mock.patch.object(hh, "get_effective_config", return_value=config()),
            mock.patch.object(hh, "ensure_initialized", return_value=True),
            mock.patch.object(hh, "get_client", return_value=client),
            mock.patch.object(hh, "mask_outbound", side_effect=lambda _c, v, _e: str(v)),
        ):
            response = asyncio.run(
                tool.execute(action="search", query="hello", scope="peer")
            )
        self.assertIn("No results.", response.message)

    def test_scope_use_on_scopeless_sdk_returns_bounded_error(self):
        tool, ctx = self.make_tool()
        with (
            mock.patch.object(hh, "get_effective_config", return_value=config()),
            mock.patch.object(hh, "mask_outbound", side_effect=lambda _c, v, _e: str(v)),
            mock.patch.object(hh, "scope_supported", return_value=False),
        ):
            response = asyncio.run(
                tool.execute(action="recall", query="hello", scope="other-project")
            )
        self.assertIn("2.4.0", response.message)

    def test_invalid_scope_name_returns_bounded_error(self):
        tool, ctx = self.make_tool()
        with (
            mock.patch.object(hh, "get_effective_config", return_value=config()),
            mock.patch.object(hh, "mask_outbound", side_effect=lambda _c, v, _e: str(v)),
        ):
            response = asyncio.run(
                tool.execute(action="recall", query="hello", scope="!!!")
            )
        self.assertIn("scope", response.message.lower())

    def test_recall_threads_resolved_scope(self):
        tool, ctx = self.make_tool()
        seen = {}

        def fake_get_user_context(context_arg, budget, *, search_query="", scope_id=None):
            seen["scope_id"] = scope_id
            return "memory pack"

        with (
            mock.patch.object(hh, "get_effective_config", return_value=config()),
            mock.patch.object(hh, "get_user_context", side_effect=fake_get_user_context),
            mock.patch.object(hh, "mask_outbound", side_effect=lambda _c, v, _e: str(v)),
            mock.patch.object(hh, "scope_supported", return_value=True),
        ):
            response = asyncio.run(
                tool.execute(action="recall", query="hello", scope="My Team")
            )
        self.assertEqual(seen["scope_id"], "a0-my-team")
        self.assertIn("memory pack", response.message)

    def test_ask_threads_scope_when_supported(self):
        tool, ctx = self.make_tool()
        client, observer = self._client()
        chat_kwargs = {}
        observer.chat = lambda query, **kwargs: chat_kwargs.update(kwargs) or "answer"
        with (
            mock.patch.object(hh, "get_effective_config", return_value=config()),
            mock.patch.object(hh, "ensure_initialized", return_value=True),
            mock.patch.object(hh, "get_client", return_value=client),
            mock.patch.object(hh, "mask_outbound", side_effect=lambda _c, v, _e: str(v)),
            mock.patch.object(hh, "scope_supported", return_value=True),
        ):
            response = asyncio.run(
                tool.execute(action="ask", query="what?", scope="team")
            )
        self.assertEqual(chat_kwargs.get("scope"), "a0-team")
        self.assertIn("answer", response.message)

    def test_status_payload_surfaces_scope_view(self):
        tool, ctx = self.make_tool()
        state = {"counts": {"pending": 0}, "worker": {"running": False}}
        with (
            mock.patch.object(
                hh,
                "get_effective_config",
                return_value=config(scope_strategy="custom", scope_custom="team"),
            ),
            mock.patch.object(hh, "outbox_status", return_value=state),
            mock.patch.object(hh, "is_configured", return_value=True),
            mock.patch.object(hh, "mask_outbound", side_effect=lambda _c, v, _e: str(v)),
            mock.patch.object(hh, "scope_supported", return_value=True),
        ):
            response = asyncio.run(tool.execute(action="status"))
        self.assertIn('"resolved": "a0-team"', response.message)
        self.assertIn('"sdk_support": true', response.message)


if __name__ == "__main__":
    unittest.main()
