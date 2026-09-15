"""Bounded background delivery worker for the Honcho SQLite outbox."""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import random
import socket
import threading
import time
from typing import Any, Callable, Iterable
import uuid

from .outbox import Outbox, OutboxEvent


@dataclass(frozen=True)
class WorkerSettings:
    poll_seconds: float = 1.0
    batch_size: int = 20
    concurrency: int = 2
    lease_seconds: float = 30.0
    max_attempts: int = 8
    retry_base_seconds: float = 2.0
    retry_max_seconds: float = 300.0
    jitter_ratio: float = 0.2
    auth_circuit_seconds: float = 900.0

    def normalized(self) -> "WorkerSettings":
        return WorkerSettings(
            poll_seconds=max(0.05, float(self.poll_seconds)),
            batch_size=max(1, min(100, int(self.batch_size))),
            concurrency=max(1, min(16, int(self.concurrency))),
            lease_seconds=max(1.0, float(self.lease_seconds)),
            max_attempts=max(1, int(self.max_attempts)),
            retry_base_seconds=max(0.05, float(self.retry_base_seconds)),
            retry_max_seconds=max(0.05, float(self.retry_max_seconds)),
            jitter_ratio=max(0.0, min(1.0, float(self.jitter_ratio))),
            auth_circuit_seconds=max(1.0, float(self.auth_circuit_seconds)),
        )


@dataclass(frozen=True)
class Failure:
    code: str
    retryable: bool
    auth: bool = False
    retry_after: float | None = None
    consume_attempt: bool = True


class OutboxWorker:
    """Deliver leased events in ordered per-session batches.

    Batches from different sessions may run concurrently. Events from one session
    are always sent together in creation order, and SQLite prevents another
    process from leasing that session until the current lease is released.
    """

    def __init__(
        self,
        outbox: Outbox,
        deliver_batch: Callable[[list[OutboxEvent]], None],
        *,
        settings: WorkerSettings | None = None,
        random_source: random.Random | None = None,
    ) -> None:
        self.outbox = outbox
        self.deliver_batch = deliver_batch
        self.settings = (settings or WorkerSettings()).normalized()
        self.owner = f"{socket.gethostname()}:{uuid.uuid4()}"
        self._random = random_source or random.Random()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._circuits: dict[tuple[str, str, str], float] = {}
        self._started_at: float | None = None
        self._stopped_at: float | None = None
        self._last_success_at: float | None = None
        self._last_error_at: float | None = None
        self._last_error_code: str | None = None
        self._last_latency_ms: int | None = None
        self._delivered = 0
        self._retried = 0
        self._dead = 0
        self._in_flight = 0

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> bool:
        with self._lock:
            if self.is_running:
                return False
            self.outbox.initialize()
            self.outbox.recover_expired_leases()
            self._stop.clear()
            self._wake.clear()
            self._started_at = time.time()
            self._stopped_at = None
            self._thread = threading.Thread(
                target=self._run,
                name="honcho-outbox-worker",
                daemon=True,
            )
            self._thread.start()
            return True

    def wake(self) -> None:
        self._wake.set()

    def reset_circuits(self) -> int:
        """Clear process-local auth backoff after an operator fixes credentials."""
        with self._lock:
            count = len(self._circuits)
            self._circuits.clear()
        self.wake()
        return count

    def stop(self, *, drain_seconds: float = 0.0) -> bool:
        if drain_seconds > 0 and self.is_running:
            self.drain(timeout=min(30.0, float(drain_seconds)))
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=min(30.0, max(1.0, self.settings.lease_seconds + 1.0)))
        with self._lock:
            self._stopped_at = time.time()
        return not self.is_running

    def drain(self, *, timeout: float = 5.0) -> dict[str, Any]:
        deadline = time.monotonic() + max(0.0, min(30.0, float(timeout)))
        self.wake()
        while time.monotonic() < deadline:
            counts = self.outbox.counts()
            with self._lock:
                in_flight = self._in_flight
            if counts["active"] == 0 and in_flight == 0:
                return {"drained": True, "counts": counts}
            time.sleep(0.05)
            self.wake()
        return {"drained": False, "counts": self.outbox.counts()}

    def status(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            circuits = [
                {
                    "endpoint": endpoint,
                    "workspace_id": workspace,
                    "retry_in_seconds": max(0, int(until - now)),
                }
                for (endpoint, workspace, _config_id), until in self._circuits.items()
                if until > now
            ]
            return {
                "running": self.is_running,
                "owner": self.owner.split(":", 1)[0],
                "started_at": _iso(self._started_at),
                "stopped_at": _iso(self._stopped_at),
                "last_success_at": _iso(self._last_success_at),
                "last_error_at": _iso(self._last_error_at),
                "last_error_code": self._last_error_code,
                "last_latency_ms": self._last_latency_ms,
                "delivered": self._delivered,
                "retried": self._retried,
                "dead_lettered": self._dead,
                "in_flight": self._in_flight,
                "circuits": circuits[:20],
                "settings": asdict(self.settings),
            }

    def process_once(self) -> int:
        now = time.time()
        with self._lock:
            self._circuits = {
                identity: until
                for identity, until in self._circuits.items()
                if until > now
            }
            blocked = tuple(self._circuits)
        events = self.outbox.lease_batch(
            self.owner,
            limit=self.settings.batch_size,
            lease_seconds=self.settings.lease_seconds,
            blocked_identities=blocked,
        )
        if not events:
            return 0
        batches = _group_batches(events)
        with ThreadPoolExecutor(
            max_workers=min(self.settings.concurrency, len(batches)),
            thread_name_prefix="honcho-delivery",
        ) as pool:
            futures = [pool.submit(self._process_batch, batch) for batch in batches]
            for future in as_completed(futures):
                # _process_batch contains delivery exceptions. A programming error
                # still reaches the worker loop and is surfaced as worker_internal.
                future.result()
        return len(events)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                processed = self.process_once()
                if processed:
                    continue
                self.outbox.prune()
            except Exception:
                with self._lock:
                    self._last_error_at = time.time()
                    self._last_error_code = "worker_internal"
            self._wake.wait(self.settings.poll_seconds)
            self._wake.clear()

    def _process_batch(self, events: list[OutboxEvent]) -> None:
        started = time.monotonic()
        with self._lock:
            self._in_flight += len(events)
        try:
            self.deliver_batch(events)
        except Exception as exc:
            self._handle_failure(events, classify_failure(exc))
        else:
            delivered = self.outbox.mark_delivered_many(
                (event.event_id for event in events), self.owner
            )
            with self._lock:
                self._delivered += delivered
                self._last_success_at = time.time()
                self._last_error_code = None
                self._last_latency_ms = max(
                    0, int((time.monotonic() - started) * 1000)
                )
        finally:
            with self._lock:
                self._in_flight = max(0, self._in_flight - len(events))

    def _handle_failure(self, events: list[OutboxEvent], failure: Failure) -> None:
        identity = events[0].delivery_identity
        now = time.time()
        if failure.auth:
            with self._lock:
                self._circuits[identity] = now + self.settings.auth_circuit_seconds

        identifiers = [event.event_id for event in events]
        retry_after = self._retry_delay(events, failure)
        dead_ids: list[str] = []
        retry_ids: list[str] = []

        if not failure.consume_attempt:
            retried = self.outbox.defer_many(
                identifiers,
                self.owner,
                next_attempt_at=now + retry_after,
                error_code=failure.code,
            )
            dead = 0
        else:
            for event in events:
                next_attempt = event.attempts + 1
                if not failure.retryable or next_attempt >= self.settings.max_attempts:
                    dead_ids.append(event.event_id)
                else:
                    retry_ids.append(event.event_id)
            dead = self.outbox.dead_letter_many(
                dead_ids, self.owner, error_code=failure.code
            )
            retried = self.outbox.reschedule_many(
                retry_ids,
                self.owner,
                next_attempt_at=now + retry_after,
                error_code=failure.code,
            )

        with self._lock:
            self._last_error_at = now
            self._last_error_code = failure.code
            self._dead += dead
            self._retried += retried

    def _retry_delay(self, events: list[OutboxEvent], failure: Failure) -> float:
        if failure.retry_after is not None:
            return min(86_400.0, max(0.05, failure.retry_after))
        attempt = max((event.attempts for event in events), default=0) + 1
        delay = min(
            self.settings.retry_max_seconds,
            self.settings.retry_base_seconds * (2 ** max(0, attempt - 1)),
        )
        spread = delay * self.settings.jitter_ratio
        return min(
            86_400.0,
            max(0.05, delay + self._random.uniform(-spread, spread)),
        )


_registry_lock = threading.RLock()
_workers: dict[str, OutboxWorker] = {}


def start_singleton(
    outbox: Outbox,
    deliver_batch: Callable[[list[OutboxEvent]], None],
    *,
    settings: WorkerSettings | None = None,
) -> OutboxWorker:
    key = str(outbox.path.resolve())
    normalized = (settings or WorkerSettings()).normalized()
    with _registry_lock:
        worker = _workers.get(key)
        if worker and worker.is_running:
            return worker
        worker = OutboxWorker(outbox, deliver_batch, settings=normalized)
        _workers[key] = worker
        worker.start()
        return worker


def get_singleton(path: str) -> OutboxWorker | None:
    key = str(Outbox(path).path.resolve())
    with _registry_lock:
        return _workers.get(key)


def stop_singleton(path: str, *, drain_seconds: float = 0.0) -> bool:
    key = str(Outbox(path).path.resolve())
    with _registry_lock:
        worker = _workers.pop(key, None)
    return True if worker is None else worker.stop(drain_seconds=drain_seconds)


def stop_all(*, drain_seconds: float = 0.0) -> None:
    with _registry_lock:
        workers = list(_workers.values())
        _workers.clear()
    for worker in workers:
        worker.stop(drain_seconds=drain_seconds)


def classify_failure(exc: Exception) -> Failure:
    code = _safe_failure_code(getattr(exc, "failure_code", ""))
    consume_attempt = not bool(getattr(exc, "defer_without_attempt", False))
    retry_after = _retry_after(exc)
    if code:
        return Failure(
            code,
            retryable=True,
            retry_after=retry_after,
            consume_attempt=consume_attempt,
        )

    status = _status_code(exc)
    if status in {401, 403}:
        return Failure(f"http_{status}", retryable=False, auth=True)
    if status == 429:
        return Failure("http_429", retryable=True, retry_after=retry_after)
    if status is not None and 500 <= status <= 599:
        return Failure(f"http_{status}", retryable=True)
    if status in {408, 409, 425}:
        return Failure(f"http_{status}", retryable=True)
    if status is not None and 400 <= status <= 499:
        return Failure(f"http_{status}", retryable=False)
    name = type(exc).__name__.lower()
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)) or any(
        word in name for word in ("timeout", "connection", "network")
    ):
        return Failure("transport", retryable=True)
    return Failure("unknown", retryable=True)


def _group_batches(events: Iterable[OutboxEvent]) -> list[list[OutboxEvent]]:
    groups: OrderedDict[tuple[str, str, str, str], list[OutboxEvent]] = OrderedDict()
    for event in events:
        groups.setdefault(event.delivery_group, []).append(event)
    return list(groups.values())


def _status_code(exc: Exception) -> int | None:
    candidates = (
        getattr(exc, "status", None),
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    )
    for value in candidates:
        try:
            status = int(value)
        except (TypeError, ValueError):
            continue
        if 100 <= status <= 599:
            return status
    return None


def _retry_after(exc: Exception) -> float | None:
    value: Any = getattr(exc, "retry_after", None)
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    if value is None and hasattr(headers, "get"):
        value = headers.get("Retry-After") or headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.05, float(value))
    except (TypeError, ValueError):
        pass
    try:
        parsed = parsedate_to_datetime(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(
            0.05, (parsed - datetime.now(timezone.utc)).total_seconds()
        )
    except (TypeError, ValueError, OverflowError):
        return None


def _safe_failure_code(value: Any) -> str:
    text = "".join(
        character
        for character in str(value or "").lower()
        if character.isalnum() or character in "_-."
    )
    return text[:80]


def _iso(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
