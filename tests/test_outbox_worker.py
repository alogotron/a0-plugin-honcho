from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

from helpers.outbox import Outbox, SCHEMA_VERSION
from helpers.worker import (
    OutboxWorker,
    WorkerSettings,
    classify_failure,
    get_singleton,
    start_singleton,
    stop_all,
)


def enqueue(outbox: Outbox, event_id: str, *, session: str = "s", created: float = 1.0):
    return outbox.enqueue(
        endpoint="http://api",
        workspace_id="workspace",
        session_id=session,
        peer_id="user",
        content=f"content-{event_id}",
        role="user",
        user_peer_id="user",
        agent_peer_id="agent",
        config_id="config",
        event_id=event_id,
        created_at=created,
    )


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "queue.sqlite3"
        self.outbox = Outbox(
            self.path,
            max_rows=5,
            max_content_chars=12,
            delivered_retention_seconds=10,
            dead_retention_seconds=20,
        )

    def tearDown(self):
        stop_all()
        self.temp.cleanup()

    def test_initialization_is_idempotent_secure_and_wal(self):
        self.outbox.initialize()
        self.outbox.initialize()
        self.assertEqual(self.outbox.schema_version(), SCHEMA_VERSION)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_schema_v1_migrates_without_data_loss(self):
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """CREATE TABLE outbox_events (
                event_id TEXT PRIMARY KEY, endpoint TEXT NOT NULL,
                workspace_id TEXT NOT NULL, session_id TEXT NOT NULL,
                peer_id TEXT NOT NULL, content TEXT NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL NOT NULL, lease_owner TEXT,
                lease_until REAL, state TEXT NOT NULL DEFAULT 'pending',
                error_code TEXT)"""
            )
            connection.execute(
                "INSERT INTO outbox_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("old", "http://api", "w", "s", "u", "redacted", 1, 1, 0, 1, None, None, "pending", None),
            )
            connection.execute("PRAGMA user_version=1")
        self.outbox.initialize()
        event = self.outbox.get("old")
        self.assertIsNotNone(event)
        self.assertEqual(event.content, "redacted")
        self.assertEqual(event.role, "")
        self.assertEqual(self.outbox.schema_version(), SCHEMA_VERSION)

    def test_enqueue_is_bounded_truncated_and_deduplicated(self):
        self.assertEqual(enqueue(self.outbox, "one"), "one")
        self.assertEqual(enqueue(self.outbox, "one"), "one")
        self.assertEqual(self.outbox.counts()["total"], 1)
        self.assertEqual(self.outbox.get("one").content, "content-one"[:12])
        for index in range(2, 6):
            enqueue(self.outbox, str(index), created=float(index))
        self.assertIsNone(enqueue(self.outbox, "overflow", created=10))
        self.assertEqual(self.outbox.counts()["total"], 5)

    def test_leases_preserve_session_order_and_recover(self):
        enqueue(self.outbox, "first", created=1)
        enqueue(self.outbox, "second", created=2)
        leased = self.outbox.lease_batch("owner", limit=10, lease_seconds=2, now=10)
        self.assertEqual([event.event_id for event in leased], ["first", "second"])
        self.assertEqual(self.outbox.lease_batch("other", limit=10, lease_seconds=2, now=11), [])
        self.assertEqual(self.outbox.recover_expired_leases(now=13), 2)
        recovered = self.outbox.lease_batch("other", limit=10, lease_seconds=2, now=13)
        self.assertEqual([event.event_id for event in recovered], ["first", "second"])

    def test_retry_dead_letter_and_recovery_transitions(self):
        enqueue(self.outbox, "event")
        leased = self.outbox.lease_batch("owner", limit=1, lease_seconds=10, now=2)[0]
        self.assertTrue(
            self.outbox.reschedule(
                leased.event_id,
                "owner",
                next_attempt_at=3,
                error_code="http_500 secret text",
            )
        )
        self.assertEqual(self.outbox.get("event").attempts, 1)
        leased = self.outbox.lease_batch("owner", limit=1, lease_seconds=10, now=3)[0]
        self.assertTrue(self.outbox.dead_letter(leased.event_id, "owner", error_code="http_400"))
        self.assertEqual(self.outbox.counts()["dead"], 1)
        self.assertEqual(self.outbox.retry_dead_letters(limit=1, now=4), 1)
        event = self.outbox.get("event")
        self.assertEqual((event.state, event.attempts, event.error_code), ("pending", 0, None))

    def test_prune_removes_only_expired_terminal_rows(self):
        enqueue(self.outbox, "delivered", created=1)
        event = self.outbox.lease_batch("owner", limit=1, lease_seconds=5, now=2)[0]
        self.outbox.mark_delivered(event.event_id, "owner", now=2)
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE outbox_events SET updated_at=1 WHERE event_id='delivered'")
        self.assertEqual(self.outbox.prune(now=20), 1)
        self.assertEqual(self.outbox.counts()["total"], 0)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.outbox = Outbox(Path(self.temp.name) / "worker.sqlite3")

    def tearDown(self):
        stop_all()
        self.temp.cleanup()

    def settings(self, **updates):
        values = dict(
            poll_seconds=0.05,
            batch_size=10,
            concurrency=2,
            lease_seconds=5,
            max_attempts=2,
            retry_base_seconds=0.05,
            retry_max_seconds=1,
            jitter_ratio=0,
            auth_circuit_seconds=10,
        )
        values.update(updates)
        return WorkerSettings(**values)

    def test_successful_batch_is_delivered_once_in_order(self):
        enqueue(self.outbox, "a", created=1)
        enqueue(self.outbox, "b", created=2)
        calls = []
        worker = OutboxWorker(self.outbox, lambda events: calls.append([e.event_id for e in events]), settings=self.settings())
        self.assertEqual(worker.process_once(), 2)
        self.assertEqual(calls, [["a", "b"]])
        self.assertEqual(self.outbox.counts()["delivered"], 2)

    def test_retry_then_dead_letter_after_max_attempts(self):
        enqueue(self.outbox, "event")
        worker = OutboxWorker(self.outbox, lambda events: (_ for _ in ()).throw(TimeoutError()), settings=self.settings())
        worker.process_once()
        first = self.outbox.get("event")
        self.assertEqual((first.state, first.attempts), ("pending", 1))
        with sqlite3.connect(self.outbox.path) as connection:
            connection.execute("UPDATE outbox_events SET next_attempt_at=0")
        worker.process_once()
        self.assertEqual(self.outbox.get("event").state, "dead")

    def test_auth_failure_dead_letters_and_opens_circuit(self):
        class Unauthorized(RuntimeError):
            status_code = 401

        enqueue(self.outbox, "event")
        worker = OutboxWorker(self.outbox, lambda events: (_ for _ in ()).throw(Unauthorized()), settings=self.settings())
        worker.process_once()
        status = worker.status()
        self.assertEqual(self.outbox.counts()["dead"], 1)
        self.assertEqual(status["last_error_code"], "http_401")
        self.assertEqual(len(status["circuits"]), 1)
        self.assertEqual(worker.reset_circuits(), 1)
        self.assertEqual(worker.status()["circuits"], [])

    def test_retry_after_and_runtime_defer_classification(self):
        response = type("Response", (), {"headers": {"Retry-After": "7"}})()
        rate_limited = type("RateLimited", (RuntimeError,), {"status_code": 429})()
        rate_limited.response = response
        failure = classify_failure(rate_limited)
        self.assertTrue(failure.retryable)
        self.assertEqual(failure.retry_after, 7.0)

        deferred = RuntimeError("config")
        deferred.failure_code = "runtime_config_unavailable"
        deferred.defer_without_attempt = True
        deferred.retry_after = 5
        failure = classify_failure(deferred)
        self.assertFalse(failure.consume_attempt)
        self.assertEqual(failure.code, "runtime_config_unavailable")

    def test_singleton_registry_reuses_running_worker(self):
        first = start_singleton(self.outbox, lambda events: None, settings=self.settings())
        second = start_singleton(self.outbox, lambda events: None, settings=self.settings())
        self.assertIs(first, second)
        self.assertIs(get_singleton(str(self.outbox.path)), first)
        stop_all()
        self.assertFalse(first.is_running)


if __name__ == "__main__":
    unittest.main()
