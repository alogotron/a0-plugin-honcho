from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest import mock

from helpers import honcho_helper as hh


class FakeHoncho:
    calls = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        type(self).calls.append(kwargs)


class EffectiveConfigTests(unittest.TestCase):
    def setUp(self):
        hh.cleanup()

    def tearDown(self):
        hh.cleanup()

    def resolve(self, explicit=None, secrets=None, env=None, agent=None):
        with (
            mock.patch.object(hh, "_get_plugin_config", return_value=explicit or {}),
            mock.patch.object(hh, "_load_secret_values", return_value=secrets or {}),
            mock.patch.dict(os.environ, env or {}, clear=True),
        ):
            return hh.get_effective_config(agent=agent)

    def test_api_endpoint_precedence_and_defaults(self):
        explicit = {
            "honcho_api_url": "http://configured-api/",
            "honcho_mcp_url": "http://configured-mcp/",
        }
        cfg = self.resolve(
            explicit,
            secrets={"HONCHO_BASE_URL": "http://legacy-secret/"},
            env={"HONCHO_API_URL": "http://preferred-env/"},
        )
        self.assertEqual(cfg.api_url, "http://preferred-env")
        self.assertEqual(cfg.mcp_url, "http://configured-mcp")

        legacy = self.resolve(
            explicit,
            secrets={"HONCHO_BASE_URL": "http://legacy-secret/"},
        )
        self.assertEqual(legacy.api_url, "http://legacy-secret")

        defaults = self.resolve()
        self.assertEqual(defaults.api_url, hh.CLOUD_API_URL)
        self.assertEqual(defaults.mcp_url, hh.HOSTED_MCP_URL)

    def test_runtime_values_override_explicit_config(self):
        cfg = self.resolve(
            {
                "honcho_workspace_id": "configured-workspace",
                "honcho_user_id": "configured-user",
                "honcho_agent_peer_id": "configured-agent",
            },
            secrets={
                "HONCHO_WORKSPACE_ID": "secret-workspace",
                "HONCHO_USER_ID": "secret-user",
                "HONCHO_AGENT_PEER_ID": "secret-agent",
            },
        )
        self.assertEqual(cfg.workspace_id, "secret-workspace")
        self.assertEqual(cfg.user_peer_id, "secret-user")
        self.assertEqual(cfg.agent_peer_id, "secret-agent")

    def test_named_agent_does_not_rewrite_workspace_or_peer(self):
        named = SimpleNamespace(config=SimpleNamespace(profile="wonder1"))
        cfg = self.resolve(
            {
                "honcho_workspace_id": "shared-workspace",
                "honcho_user_id": "person-7",
                "honcho_agent_peer_id": "existing-peer-9",
            },
            agent=named,
        )
        self.assertEqual(cfg.workspace_id, "shared-workspace")
        self.assertEqual(cfg.agent_peer_id, "existing-peer-9")

    def test_client_construction_and_non_secret_cache_key(self):
        FakeHoncho.calls = []
        first = hh.EffectiveConfig(
            api_key="top-secret-one",
            api_url="http://honcho.local",
            mcp_url=hh.HOSTED_MCP_URL,
            workspace_id="workspace-a",
            user_peer_id="user",
            agent_peer_id="agent",
            cache_ttl=120,
            max_context_tokens=500,
            debug=False,
        )
        second = hh.EffectiveConfig(**{**first.__dict__, "api_key": "top-secret-two"})
        with (
            mock.patch.object(hh, "HONCHO_AVAILABLE", True),
            mock.patch.object(hh, "Honcho", FakeHoncho),
        ):
            client_a = hh.get_client(config=first)
            self.assertIs(client_a, hh.get_client(config=first))
            client_b = hh.get_client(config=second)

        self.assertIsNot(client_a, client_b)
        self.assertEqual(len(FakeHoncho.calls), 2)
        self.assertEqual(
            FakeHoncho.calls[0],
            {
                "api_key": "top-secret-one",
                "base_url": "http://honcho.local",
                "workspace_id": "workspace-a",
                "timeout": 10.0,
                "max_retries": 1,
            },
        )
        cache_keys = repr(tuple(hh._client_cache))
        self.assertNotIn("top-secret-one", cache_keys)
        self.assertNotIn("secret-one", cache_keys)

    def test_context_cache_key_isolates_every_identity_dimension(self):
        cfg = hh.EffectiveConfig(
            api_key="key",
            api_url="http://api-one",
            mcp_url="http://mcp",
            workspace_id="workspace",
            user_peer_id="target",
            agent_peer_id="observer",
            cache_ttl=120,
            max_context_tokens=500,
            debug=False,
        )
        base = hh.context_cache_key(cfg, "session", 500)
        variants = [
            hh.context_cache_key(hh.EffectiveConfig(**{**cfg.__dict__, "api_url": "http://api-two"}), "session", 500),
            hh.context_cache_key(hh.EffectiveConfig(**{**cfg.__dict__, "workspace_id": "other"}), "session", 500),
            hh.context_cache_key(hh.EffectiveConfig(**{**cfg.__dict__, "agent_peer_id": "other"}), "session", 500),
            hh.context_cache_key(hh.EffectiveConfig(**{**cfg.__dict__, "user_peer_id": "other"}), "session", 500),
            hh.context_cache_key(cfg, "other-session", 500),
            hh.context_cache_key(cfg, "session", 900),
        ]
        self.assertEqual(len(set([base, *variants])), 7)

    @unittest.skipUnless(hh.SessionPeerConfig is not None, "Honcho SDK unavailable")
    def test_observation_flags_are_explicit(self):
        client = SimpleNamespace(peer=lambda peer_id: SimpleNamespace(id=peer_id))
        cfg = hh.EffectiveConfig(
            api_key="key",
            api_url=hh.CLOUD_API_URL,
            mcp_url=hh.HOSTED_MCP_URL,
            workspace_id="workspace",
            user_peer_id="user",
            agent_peer_id="agent",
            cache_ttl=120,
            max_context_tokens=500,
            debug=False,
        )
        roster = hh._observation_roster(client, cfg)
        self.assertEqual(roster[0][1].model_dump(), {"observe_others": True, "observe_me": True})
        self.assertEqual(roster[1][1].model_dump(), {"observe_others": True, "observe_me": False})

    def test_only_known_already_present_conflicts_are_suppressed(self):
        known = SimpleNamespace(status=409, body={"detail": "peer already present"}, code="conflict")
        unknown = SimpleNamespace(status=409, body={"detail": "workspace locked"}, code="conflict")
        server = SimpleNamespace(status=500, body={"detail": "peer already present"}, code="server")
        self.assertTrue(hh._already_present(known))
        self.assertFalse(hh._already_present(unknown))
        self.assertFalse(hh._already_present(server))

    def test_keyless_self_hosted_client_is_allowed(self):
        FakeHoncho.calls = []
        cfg = hh.EffectiveConfig(
            api_key="",
            api_url="http://honcho.internal",
            mcp_url=hh.HOSTED_MCP_URL,
            workspace_id="workspace",
            user_peer_id="user",
            agent_peer_id="agent",
            cache_ttl=120,
            max_context_tokens=500,
            debug=False,
        )
        with (
            mock.patch.object(hh, "HONCHO_AVAILABLE", True),
            mock.patch.object(hh, "Honcho", FakeHoncho),
        ):
            self.assertIsNotNone(hh.get_client(config=cfg))
        self.assertIsNone(FakeHoncho.calls[0]["api_key"])

    def test_probe_client_has_bounded_timeout_and_no_retries(self):
        FakeHoncho.calls = []
        cfg = hh.EffectiveConfig(
            api_key="key",
            api_url=hh.CLOUD_API_URL,
            mcp_url=hh.HOSTED_MCP_URL,
            workspace_id="workspace",
            user_peer_id="user",
            agent_peer_id="agent",
            cache_ttl=120,
            max_context_tokens=500,
            debug=False,
        )
        with (
            mock.patch.object(hh, "HONCHO_AVAILABLE", True),
            mock.patch.object(hh, "Honcho", FakeHoncho),
        ):
            hh.get_probe_client(cfg)
        self.assertEqual(FakeHoncho.calls[0]["timeout"], 5)
        self.assertEqual(FakeHoncho.calls[0]["max_retries"], 0)

    def test_endpoint_normalization_removes_embedded_credentials_and_query(self):
        self.assertEqual(
            hh._normalize_url("https://user:password@example.test:8443/base/?token=secret#x"),
            "https://example.test:8443/base",
        )

    def test_endpoint_normalization_rejects_non_http_schemes_fail_closed(self):
        self.assertEqual(
            hh._normalize_url("file:///etc/passwd", hh.CLOUD_API_URL),
            "",
        )

    def test_sync_alias_enqueues_without_remote_network_work(self):
        cfg = hh.EffectiveConfig(
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
        queued = []
        outbox = SimpleNamespace(
            enqueue=lambda **values: queued.append(values) or values["event_id"]
        )
        context = SimpleNamespace(id="abc", agent0=SimpleNamespace(history=None))
        with (
            mock.patch.object(hh, "get_effective_config", return_value=cfg),
            mock.patch.object(hh, "get_outbox", return_value=outbox),
            mock.patch.object(hh, "initialize_runtime", return_value=None),
            mock.patch.object(hh, "_mask_outbound", return_value="masked"),
            mock.patch.object(hh, "get_client") as remote_client,
        ):
            self.assertTrue(hh.sync_message(context, "user", "raw"))
        remote_client.assert_not_called()
        self.assertEqual(queued[0]["content"], "masked")
        self.assertEqual(queued[0]["session_id"], "chat-abc")
        self.assertEqual(queued[0]["peer_id"], "user")

    def test_context_text_keeps_messages_from_sdk_2_1(self):
        value = SimpleNamespace(
            messages=[SimpleNamespace(peer_id="user", content="fresh fact")],
            summary=None,
            peer_representation=None,
            peer_card=None,
        )
        self.assertIn("user: fresh fact", hh._context_text(value))

    def test_targeted_cache_clear_preserves_other_identity(self):
        cfg = hh.EffectiveConfig(
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
        other = hh.EffectiveConfig(**{**cfg.__dict__, "agent_peer_id": "other-agent"})
        key = hh.context_cache_key(cfg, "session", 500)
        other_key = hh.context_cache_key(other, "session", 500)
        hh._context_cache[key] = (0.0, "old")
        hh._context_cache[other_key] = (0.0, "other")
        hh.clear_context_cache("session", effective=cfg)
        self.assertNotIn(key, hh._context_cache)
        self.assertIn(other_key, hh._context_cache)

    def test_redaction_removes_full_key_and_bearer_value(self):
        cfg = hh.EffectiveConfig(
            api_key="sensitive-token-value",
            api_url=hh.CLOUD_API_URL,
            mcp_url=hh.HOSTED_MCP_URL,
            workspace_id="workspace",
            user_peer_id="user",
            agent_peer_id="agent",
            cache_ttl=120,
            max_context_tokens=500,
            debug=False,
        )
        with mock.patch.object(hh, "_load_secret_values", return_value={}):
            text = hh.redact_text(
                "bad sensitive-token-value Authorization: Bearer another-token",
                cfg,
            )
        self.assertNotIn("sensitive-token-value", text)
        self.assertNotIn("another-token", text)
        self.assertIn("***", text)


if __name__ == "__main__":
    unittest.main()
