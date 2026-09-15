from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock


helpers_api = ModuleType("helpers.api")
helpers_api.ApiHandler = object
helpers_api.Request = object
class Response:
    def __init__(self, status=200, response=""):
        self.status = status
        self.response = response
helpers_api.Response = Response
sys.modules.setdefault("helpers.api", helpers_api)

SPEC = importlib.util.spec_from_file_location(
    "honcho_status_under_test",
    Path(__file__).parents[1] / "api" / "honcho_status.py",
)
status = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(status)


class StatusApiTests(unittest.TestCase):
    def test_status_payload_redacts_key_and_exposes_effective_endpoints(self):
        effective = SimpleNamespace(
            api_key="never-return-this-key",
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
        )
        helper = SimpleNamespace(
            HONCHO_AVAILABLE=True,
            get_effective_config=lambda context: effective,
            cache_status=lambda: {"clients": 1, "contexts": 2},
        )
        with (
            mock.patch.object(status, "_helper", return_value=helper),
            mock.patch.object(status, "_context", return_value=None),
        ):
            payload = asyncio.run(status.HonchoStatus().process({"action": "status"}, object()))
        rendered = repr(payload)
        self.assertNotIn(effective.api_key, rendered)
        self.assertEqual(payload["api_key_masked"], "***")
        self.assertEqual(payload["api_url"], "http://api.internal")
        self.assertEqual(payload["mcp_url"], "http://mcp.internal")
        self.assertEqual(payload["cache"], {"clients": 1, "contexts": 2})

    def test_api_probe_redacts_exception(self):
        effective = SimpleNamespace(api_key="raw-key")
        helper = SimpleNamespace(
            HONCHO_AVAILABLE=True,
            get_probe_client=lambda config: (_ for _ in ()).throw(RuntimeError("failed raw-key")),
            redact_text=lambda value, config: str(value).replace("raw-key", "***"),
        )
        result = status._api_probe(helper, None, effective)
        self.assertFalse(result["ok"])
        self.assertNotIn("raw-key", result["detail"])

    def test_api_probe_uses_workspace_scoped_read(self):
        calls = []
        client = SimpleNamespace(get_metadata=lambda: calls.append("metadata"))
        effective = SimpleNamespace(api_key="key", mode="hosted")
        helper = SimpleNamespace(
            HONCHO_AVAILABLE=True,
            get_probe_client=lambda config: client,
            redact_text=lambda value, config: str(value),
        )
        result = status._api_probe(helper, None, effective)
        self.assertTrue(result["ok"])
        self.assertEqual(calls, ["metadata"])

    def test_mcp_probe_checks_origin_health_for_mcp_path(self):
        effective = SimpleNamespace(mcp_url="http://honcho.internal:3000/mcp")
        helper = SimpleNamespace(redact_text=lambda value, config: str(value))

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

        with mock.patch.object(status, "urlopen", return_value=FakeResponse()) as opened:
            result = status._mcp_probe(helper, effective)
        self.assertTrue(result["ok"])
        self.assertEqual(
            opened.call_args.args[0].full_url,
            "http://honcho.internal:3000/health",
        )

    def test_retry_dead_letters_is_bounded_and_returns_sanitized_status(self):
        effective = SimpleNamespace(api_key="key")
        calls = []
        outbox = {
            "counts": {"pending": 4, "dead": 0},
            "worker": {"running": True},
            "schema_version": 2,
        }
        helper = SimpleNamespace(
            get_effective_config=lambda context: effective,
            retry_dead_letters=lambda context, limit: calls.append(limit) or 4,
            outbox_status=lambda context: outbox,
            redact_text=lambda value, config: str(value),
        )
        with (
            mock.patch.object(status, "_helper", return_value=helper),
            mock.patch.object(status, "_context", return_value=None),
        ):
            payload = asyncio.run(
                status.HonchoStatus().process(
                    {"action": "retry_dead_letters", "limit": 999999},
                    object(),
                )
            )
        self.assertEqual(calls, [1000])
        self.assertEqual(payload, {"ok": True, "retried": 4, "outbox": outbox})

    def test_drain_reports_timeout_as_not_ok(self):
        effective = SimpleNamespace(api_key="key")
        outbox = {
            "counts": {"pending": 1, "dead": 0},
            "worker": {"running": True},
            "schema_version": 2,
        }
        calls = []
        helper = SimpleNamespace(
            get_effective_config=lambda context: effective,
            drain_worker=lambda context, timeout: calls.append(timeout)
            or {"drained": False, "counts": outbox["counts"]},
            outbox_status=lambda context: outbox,
            redact_text=lambda value, config: str(value),
        )
        with (
            mock.patch.object(status, "_helper", return_value=helper),
            mock.patch.object(status, "_context", return_value=None),
        ):
            payload = asyncio.run(
                status.HonchoStatus().process(
                    {"action": "drain", "timeout": 999},
                    object(),
                )
            )
        self.assertEqual(calls, [15])
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["drain"]["drained"])

    def test_operation_failure_returns_type_only(self):
        effective = SimpleNamespace(api_key="raw-key")
        helper = SimpleNamespace(
            get_effective_config=lambda context: effective,
            wake_worker=lambda context: (_ for _ in ()).throw(
                RuntimeError("raw-key sensitive server detail")
            ),
            redact_text=lambda value, config: str(value).replace("raw-key", "***"),
        )
        with (
            mock.patch.object(status, "_helper", return_value=helper),
            mock.patch.object(status, "_context", return_value=None),
        ):
            payload = asyncio.run(
                status.HonchoStatus().process({"action": "wake"}, object())
            )
        self.assertEqual(payload["error"], "operation_failed")
        self.assertEqual(payload["detail"], "RuntimeError")
        self.assertNotIn("sensitive server detail", repr(payload))


if __name__ == "__main__":
    unittest.main()
