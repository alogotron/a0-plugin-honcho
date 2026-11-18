from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
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
        recall_search_top_k=8,
        recall_max_distance=0.8,
        recall_max_conclusions=20,
    )
    values.update(updates)
    return hh.EffectiveConfig(**values)


def context(identifier: str = "ctx"):
    return SimpleNamespace(id=identifier, agent0=SimpleNamespace(history=None))


class _FakeHttp:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def post(self, path, body=None, query=None, **kwargs):
        self.calls.append(("POST", path, body, query))
        for (method, prefix), value in self.responses.items():
            if method == "POST" and path.startswith(prefix):
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(f"unexpected POST {path}")

    def get(self, path, **kwargs):
        self.calls.append(("GET", path, None, None))
        for (method, prefix), value in self.responses.items():
            if method == "GET" and path.startswith(prefix):
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(f"unexpected GET {path}")


class DreamHelperTests(unittest.TestCase):
    def setUp(self):
        hh.cleanup()

    def tearDown(self):
        hh.cleanup()

    def test_list_dream_runs_posts_filters_and_query(self):
        http = _FakeHttp(
            {("POST", "/v3/workspaces/workspace/dreams/list"): {"items": [
                {"id": "run-2", "status": "completed"},
                {"id": "run-1", "status": "failed"},
            ]}}
        )
        client = SimpleNamespace(_http=http)
        with mock.patch.object(hh, "get_client", return_value=client):
            runs = hh.list_dream_runs(
                None,
                effective=config(),
                observed_id="user",
                observer_id="agent",
                size=5,
            )
        self.assertEqual([r["id"] for r in runs], ["run-2", "run-1"])
        method, path, body, query = http.calls[0]
        self.assertIn("/dreams/list", path)
        self.assertEqual(body, {"observed_id": "user", "observer_id": "agent"})
        self.assertEqual(query, {"page": 1, "size": 5})

    def test_list_dream_runs_without_client_returns_empty(self):
        with mock.patch.object(hh, "get_client", return_value=None):
            self.assertEqual(hh.list_dream_runs(None, effective=config()), [])

    def test_get_dream_run_rejects_invalid_identifier(self):
        self.assertIsNone(hh.get_dream_run("bad id with spaces", effective=config()))
        self.assertIsNone(hh.get_dream_run("", effective=config()))

    def test_get_dream_run_fetches_detail(self):
        detail = {
            "id": "run-1",
            "status": "completed",
            "created": [],
            "removed": [],
            "peer_card": None,
        }
        http = _FakeHttp(
            {("GET", "/v3/workspaces/workspace/dreams/run-1"): detail}
        )
        client = SimpleNamespace(_http=http)
        with mock.patch.object(hh, "get_client", return_value=client):
            result = hh.get_dream_run("run-1", effective=config())
        self.assertEqual(result, detail)
        self.assertEqual(http.calls[0][1], "/v3/workspaces/workspace/dreams/run-1")

    def test_search_conclusions_with_citations_returns_raw_items(self):
        items = [
            {
                "id": "c1",
                "content": "prefers concise answers",
                "source_message_ids": ["msg-a", "msg-b"],
            }
        ]
        http = _FakeHttp(
            {("POST", "/v3/workspaces/workspace/conclusions/query"): items}
        )
        client = SimpleNamespace(_http=http)
        with mock.patch.object(hh, "get_client", return_value=client):
            results = hh.search_conclusions_with_citations(
                "preferences", effective=config(), top_k=3, distance=0.5
            )
        self.assertEqual(results, items)
        _, path, body, _query = http.calls[0]
        self.assertIn("/conclusions/query", path)
        self.assertEqual(body["top_k"], 3)
        self.assertEqual(body["distance"], 0.5)
        self.assertEqual(
            body["filters"], {"observer_id": "agent", "observed_id": "user"}
        )

    def test_citations_context_renders_cited_suffix(self):
        items = [
            {"content": "fact with sources", "source_message_ids": ["m1", "m2"]},
            {"content": "fact without sources", "source_message_ids": None},
        ]
        with mock.patch.object(
            hh, "search_conclusions_with_citations", return_value=items
        ):
            text = hh.citations_context("q", effective=config())
        self.assertIn("Conclusions with source citations:", text)
        self.assertIn("- fact with sources [cited: m1, m2]", text)
        self.assertNotIn("fact without sources", text)

    def test_citations_context_fails_closed_on_error(self):
        with (
            mock.patch.object(
                hh,
                "search_conclusions_with_citations",
                side_effect=RuntimeError("boom"),
            ),
            mock.patch.object(hh, "_log"),
        ):
            self.assertIsNone(hh.citations_context("q", effective=config()))

    def test_citations_context_skips_empty_query(self):
        self.assertIsNone(hh.citations_context("  ", effective=config()))

    def test_recent_dream_runs_returns_sanitized_fields(self):
        run = {
            "id": "run-1",
            "status": "completed",
            "observer_id": "agent",
            "observed_id": "user",
            "dream_type": "omni",
            "trigger_reason": "manual",
            "created_count": 2,
            "removed_count": 1,
            "peer_card_updated": True,
            "started_at": "2026-11-18T10:00:00Z",
            "finished_at": "2026-11-18T10:05:00Z",
            "internal_secret_field": "should-not-appear",
        }
        http = _FakeHttp(
            {("POST", "/v3/workspaces/workspace/dreams/list"): {"items": [run]}}
        )
        with mock.patch.object(
            hh, "get_probe_client", return_value=SimpleNamespace(_http=http)
        ):
            runs = hh.recent_dream_runs(config(), limit=5)
        self.assertEqual(len(runs), 1)
        self.assertNotIn("internal_secret_field", runs[0])
        self.assertEqual(runs[0]["id"], "run-1")
        self.assertEqual(runs[0]["status"], "completed")

    def test_recent_dream_runs_returns_none_on_error(self):
        with mock.patch.object(
            hh, "get_probe_client", side_effect=RuntimeError("unreachable")
        ):
            self.assertIsNone(hh.recent_dream_runs(config()))

    def test_recall_context_appends_citations(self):
        cfg = config()
        ctx = context("cited")
        session = SimpleNamespace(
            context=lambda **kwargs: SimpleNamespace(
                messages=[],
                summary=SimpleNamespace(content="summary text"),
                peer_representation=None,
                peer_card=None,
            )
        )
        items = [
            {
                "content": "uses Docker daily",
                "source_message_ids": ["m1"],
            }
        ]
        http = _FakeHttp(
            {("POST", "/v3/workspaces/workspace/conclusions/query"): items}
        )
        client = SimpleNamespace(session=lambda sid: session, _http=http)
        with (
            mock.patch.object(hh, "get_effective_config", return_value=cfg),
            mock.patch.object(hh, "ensure_initialized", return_value=True),
            mock.patch.object(hh, "get_client", return_value=client),
            mock.patch.object(
                hh, "_mask_outbound", side_effect=lambda _c, value, _e: value
            ),
        ):
            result = hh.get_user_context(ctx, search_query="environment")
        self.assertIn("Current session summary:\nsummary text", result)
        self.assertIn("Conclusions with source citations:", result)
        self.assertIn("- uses Docker daily [cited: m1]", result)


helpers_tool = ModuleType("helpers.tool")


class _Response:
    def __init__(self, message, break_loop=False):
        self.message = message
        self.break_loop = break_loop


helpers_tool.Response = _Response
helpers_tool.Tool = object
sys.modules.setdefault("helpers.tool", helpers_tool)

usr = sys.modules.setdefault("usr", ModuleType("usr"))
plugins = sys.modules.setdefault("usr.plugins", ModuleType("usr.plugins"))
honcho_pkg = sys.modules.setdefault("usr.plugins.honcho", ModuleType("usr.plugins.honcho"))
honcho_helpers = ModuleType("usr.plugins.honcho.helpers")
honcho_helpers.honcho_helper = hh
sys.modules["usr.plugins.honcho.helpers"] = honcho_helpers
setattr(usr, "plugins", plugins)
setattr(plugins, "honcho", honcho_pkg)
setattr(honcho_pkg, "helpers", honcho_helpers)

SPEC = importlib.util.spec_from_file_location(
    "honcho_memory_dream_tests",
    Path(__file__).parents[1] / "tools" / "honcho_memory.py",
)
TOOL_MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(TOOL_MODULE)


class DreamsToolTests(unittest.TestCase):
    def make_tool(self):
        ctx = context("tool-dreams")
        agent = SimpleNamespace(context=ctx)
        return TOOL_MODULE.HonchoMemory(agent, "honcho_memory", None, {}, "", None), ctx

    def stub_client(self):
        peer = SimpleNamespace(conclusions_of=lambda target: SimpleNamespace())
        return SimpleNamespace(peer=lambda pid: peer)

    def test_dreams_action_lists_runs(self):
        tool, ctx = self.make_tool()
        runs = [
            {
                "id": "run-1",
                "status": "completed",
                "dream_type": "omni",
                "trigger_reason": "manual",
                "created_count": 2,
                "removed_count": 0,
                "peer_card_updated": False,
                "started_at": "2026-11-18T10:00:00Z",
            }
        ]
        with (
            mock.patch.object(hh, "get_effective_config", return_value=config()),
            mock.patch.object(hh, "ensure_initialized", return_value=True),
            mock.patch.object(hh, "get_client", return_value=self.stub_client()),
            mock.patch.object(hh, "mask_outbound", side_effect=lambda _c, value, _e: str(value)),
            mock.patch.object(hh, "list_dream_runs", return_value=runs) as listed,
        ):
            response = asyncio.run(tool.execute(action="dreams", limit=3))
        self.assertFalse(response.break_loop)
        self.assertIn("run-1", response.message)
        self.assertIn("completed", response.message)
        listed.assert_called_once()
        self.assertEqual(listed.call_args.kwargs["size"], 3)

    def test_dreams_action_with_run_id_fetches_detail(self):
        tool, ctx = self.make_tool()
        detail = {
            "id": "run-9",
            "status": "completed",
            "dream_type": "omni",
            "created_count": 1,
            "removed_count": 1,
            "peer_card_updated": True,
            "started_at": "2026-11-18T10:00:00Z",
            "created": [
                {
                    "conclusion": {"id": "c1", "content": "new fact", "status": "current"},
                    "sources": [
                        {"id": "c0", "content": "old fact", "status": "current"}
                    ],
                    "specialist": "deduction",
                }
            ],
            "removed": [
                {"id": "c0", "content": "stale fact", "replaced_by": "c1"}
            ],
            "peer_card": {
                "before": ["old trait"],
                "after": ["new trait"],
            },
        }
        with (
            mock.patch.object(hh, "get_effective_config", return_value=config()),
            mock.patch.object(hh, "ensure_initialized", return_value=True),
            mock.patch.object(hh, "get_client", return_value=self.stub_client()),
            mock.patch.object(hh, "mask_outbound", side_effect=lambda _c, value, _e: str(value)),
            mock.patch.object(hh, "get_dream_run", return_value=detail) as fetched,
        ):
            response = asyncio.run(tool.execute(action="dreams", run_id="run-9"))
        self.assertFalse(response.break_loop)
        self.assertIn("Created conclusions:", response.message)
        self.assertIn("new fact", response.message)
        self.assertIn("from: old fact", response.message)
        self.assertIn("Removed conclusions:", response.message)
        self.assertIn("stale fact", response.message)
        self.assertIn("Peer card before: old trait", response.message)
        self.assertIn("Peer card after: new trait", response.message)
        fetched.assert_called_once_with("run-9", ctx, effective=mock.ANY)

    def test_dreams_action_reports_missing_run(self):
        tool, _ctx = self.make_tool()
        with (
            mock.patch.object(hh, "get_effective_config", return_value=config()),
            mock.patch.object(hh, "ensure_initialized", return_value=True),
            mock.patch.object(hh, "get_client", return_value=self.stub_client()),
            mock.patch.object(hh, "mask_outbound", side_effect=lambda _c, value, _e: str(value)),
            mock.patch.object(hh, "get_dream_run", return_value=None),
        ):
            response = asyncio.run(tool.execute(action="dreams", run_id="run-x"))
        self.assertIn("Invalid request", response.message)

    def test_search_renders_citation_suffix(self):
        items = [
            {
                "id": "c1",
                "content": "prefers vim",
                "source_message_ids": ["m1", "m2"],
            }
        ]
        tool, _ctx = self.make_tool()
        with (
            mock.patch.object(hh, "get_effective_config", return_value=config()),
            mock.patch.object(hh, "ensure_initialized", return_value=True),
            mock.patch.object(hh, "get_client", return_value=self.stub_client()),
            mock.patch.object(hh, "mask_outbound", side_effect=lambda _c, value, _e: str(value)),
            mock.patch.object(
                hh, "search_conclusions_with_citations", return_value=items
            ) as searched,
        ):
            response = asyncio.run(
                tool.execute(action="search", query="editor", scope="peer")
            )
        self.assertIn("[cited: m1, m2]", response.message)
        searched.assert_called_once()


helpers_api = ModuleType("helpers.api")
helpers_api.ApiHandler = object
helpers_api.Request = object


class _ApiResponse:
    def __init__(self, status=200, response=""):
        self.status = status
        self.response = response


helpers_api.Response = _ApiResponse
sys.modules.setdefault("helpers.api", helpers_api)

STATUS_SPEC = importlib.util.spec_from_file_location(
    "honcho_status_dream_tests",
    Path(__file__).parents[1] / "api" / "honcho_status.py",
)
status = importlib.util.module_from_spec(STATUS_SPEC)
assert STATUS_SPEC and STATUS_SPEC.loader
STATUS_SPEC.loader.exec_module(status)


class StatusDreamsTests(unittest.TestCase):
    def test_status_payload_includes_dream_summary(self):
        effective = SimpleNamespace(
            api_key="key",
            api_url="http://api.internal",
            mcp_url="http://mcp.internal",
            mode="self-hosted",
            mcp_mode="self-hosted",
            workspace_id="workspace",
            user_peer_id="user",
            agent_peer_id="agent",
            cache_ttl=45,
            max_context_tokens=700,
            debug=True,
            capture_enabled=True,
            recall_enabled=True,
        )
        runs = [
            {
                "id": "run-1",
                "status": "completed",
                "created_count": 3,
                "removed_count": 0,
                "peer_card_updated": True,
            }
        ]
        helper = SimpleNamespace(
            HONCHO_AVAILABLE=True,
            get_effective_config=lambda context: effective,
            cache_status=lambda: {"clients": 1, "contexts": 2},
            outbox_status=lambda context: {
                "counts": {},
                "worker": {"running": False},
                "schema_version": 0,
            },
            get_session_id=lambda context: "chat-1",
            resolve_scope_id=lambda effective, context: None,
            scope_supported=lambda: True,
            recent_dream_runs=lambda eff, limit=5: runs,
        )
        with (
            mock.patch.object(status, "_helper", return_value=helper),
            mock.patch.object(status, "_context", return_value=None),
        ):
            payload = asyncio.run(
                status.HonchoStatus().process({"action": "status"}, object())
            )
        self.assertEqual(payload["dreams"], runs)

    def test_status_payload_tolerates_missing_dream_support(self):
        effective = SimpleNamespace(
            api_key="key",
            api_url="http://api.internal",
            mcp_url="http://mcp.internal",
            mode="self-hosted",
            mcp_mode="self-hosted",
            workspace_id="workspace",
            user_peer_id="user",
            agent_peer_id="agent",
            cache_ttl=45,
            max_context_tokens=700,
            debug=True,
            capture_enabled=True,
            recall_enabled=True,
        )
        helper = SimpleNamespace(
            HONCHO_AVAILABLE=True,
            get_effective_config=lambda context: effective,
            cache_status=lambda: {"clients": 1, "contexts": 2},
            outbox_status=lambda context: {
                "counts": {},
                "worker": {"running": False},
                "schema_version": 0,
            },
            get_session_id=lambda context: "chat-1",
            resolve_scope_id=lambda effective, context: None,
            scope_supported=lambda: True,
        )
        with (
            mock.patch.object(status, "_helper", return_value=helper),
            mock.patch.object(status, "_context", return_value=None),
        ):
            payload = asyncio.run(
                status.HonchoStatus().process({"action": "status"}, object())
            )
        self.assertIsNone(payload["dreams"])


if __name__ == "__main__":
    unittest.main()
