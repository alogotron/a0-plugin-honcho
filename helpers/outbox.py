"""Durable, bounded SQLite outbox for already-redacted Honcho messages."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Iterable
import uuid

SCHEMA_VERSION = 2
VALID_STATES = {"pending", "leased", "delivered", "dead"}


@dataclass(frozen=True)
class OutboxEvent:
    event_id: str
    endpoint: str
    workspace_id: str
    session_id: str
    peer_id: str
    content: str
    created_at: float
    updated_at: float
    attempts: int
    next_attempt_at: float
    lease_owner: str | None
    lease_until: float | None
    state: str
    error_code: str | None
    role: str = ""
    user_peer_id: str = ""
    agent_peer_id: str = ""
    config_id: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "OutboxEvent":
        columns = set(row.keys())
        return cls(
            **{
                field: row[field] if field in columns else definition.default
                for field, definition in cls.__dataclass_fields__.items()
            }
        )

    @property
    def delivery_identity(self) -> tuple[str, str, str]:
        return (self.endpoint, self.workspace_id, self.config_id)

    @property
    def delivery_group(self) -> tuple[str, str, str, str]:
        return (*self.delivery_identity, self.session_id)


class Outbox:
    """Transactional WAL queue with bounded storage and process-safe leases.

    Callers must redact content before :meth:`enqueue`. Delivery is at-least-once:
    a process can fail after Honcho accepts a batch but before ``mark_delivered``
    commits. Stable event IDs suppress duplicate local enqueue attempts.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        max_rows: int = 10_000,
        max_content_chars: int = 10_000,
        delivered_retention_seconds: int = 86_400,
        dead_retention_seconds: int = 2_592_000,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        self.path = Path(path).expanduser()
        self.max_rows = max(1, int(max_rows))
        self.max_content_chars = max(1, int(max_content_chars))
        self.delivered_retention_seconds = max(0, int(delivered_retention_seconds))
        self.dead_retention_seconds = max(0, int(dead_retention_seconds))
        self.busy_timeout_ms = max(1, int(busy_timeout_ms))
        self._init_lock = threading.Lock()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.path.exists():
            try:
                descriptor = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
        connection = sqlite3.connect(
            str(self.path),
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA foreign_keys=ON")
            self._secure_files()
            return connection
        except Exception:
            connection.close()
            raise

    def _secure_files(self) -> None:
        for candidate in (
            self.path,
            Path(f"{self.path}-wal"),
            Path(f"{self.path}-shm"),
        ):
            if candidate.exists():
                os.chmod(candidate, 0o600)

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            with self._connect() as connection:
                existing_version = int(
                    connection.execute("PRAGMA user_version").fetchone()[0]
                )
                if existing_version > SCHEMA_VERSION:
                    raise RuntimeError(
                        f"outbox schema {existing_version} is newer than supported "
                        f"schema {SCHEMA_VERSION}"
                    )
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS outbox_events (
                        event_id TEXT PRIMARY KEY,
                        endpoint TEXT NOT NULL,
                        workspace_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        peer_id TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                        next_attempt_at REAL NOT NULL,
                        lease_owner TEXT,
                        lease_until REAL,
                        state TEXT NOT NULL DEFAULT 'pending'
                            CHECK(state IN ('pending','leased','delivered','dead')),
                        error_code TEXT,
                        role TEXT NOT NULL DEFAULT '',
                        user_peer_id TEXT NOT NULL DEFAULT '',
                        agent_peer_id TEXT NOT NULL DEFAULT '',
                        config_id TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(outbox_events)")
                }
                migrations = {
                    "role": "TEXT NOT NULL DEFAULT ''",
                    "user_peer_id": "TEXT NOT NULL DEFAULT ''",
                    "agent_peer_id": "TEXT NOT NULL DEFAULT ''",
                    "config_id": "TEXT NOT NULL DEFAULT ''",
                }
                for name, definition in migrations.items():
                    if name not in columns:
                        connection.execute(
                            f"ALTER TABLE outbox_events ADD COLUMN {name} {definition}"
                        )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS outbox_ready_idx "
                    "ON outbox_events(state, next_attempt_at, created_at, event_id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS outbox_lease_idx "
                    "ON outbox_events(state, lease_until)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS outbox_session_order_idx "
                    "ON outbox_events(endpoint, workspace_id, session_id, state, "
                    "created_at, event_id)"
                )
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                connection.commit()
                self._secure_files()
            self._initialized = True

    def schema_version(self) -> int:
        self.initialize()
        with self._connect() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def enqueue(
        self,
        *,
        endpoint: str,
        workspace_id: str,
        session_id: str,
        peer_id: str,
        content: str,
        role: str = "",
        user_peer_id: str = "",
        agent_peer_id: str = "",
        config_id: str = "",
        event_id: str | None = None,
        created_at: float | None = None,
    ) -> str | None:
        """Insert one redacted event; return its ID, or ``None`` at capacity."""
        self.initialize()
        values = {
            "endpoint": str(endpoint or "").strip(),
            "workspace_id": str(workspace_id or "").strip(),
            "session_id": str(session_id or "").strip(),
            "peer_id": str(peer_id or "").strip(),
        }
        if any(not value for value in values.values()):
            raise ValueError("endpoint, workspace_id, session_id, and peer_id are required")
        text = str(content or "")[: self.max_content_chars]
        if not text.strip():
            return None
        identifier = str(event_id or uuid.uuid4())
        now = float(created_at if created_at is not None else time.time())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM outbox_events WHERE event_id=?", (identifier,)
            ).fetchone():
                connection.commit()
                return identifier
            self._prune_locked(connection, now)
            count = int(
                connection.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0]
            )
            if count >= self.max_rows:
                connection.rollback()
                return None
            connection.execute(
                """
                INSERT INTO outbox_events (
                    event_id, endpoint, workspace_id, session_id, peer_id, content,
                    created_at, updated_at, attempts, next_attempt_at, state, role,
                    user_peer_id, agent_peer_id, config_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'pending', ?, ?, ?, ?)
                """,
                (
                    identifier,
                    values["endpoint"],
                    values["workspace_id"],
                    values["session_id"],
                    values["peer_id"],
                    text,
                    now,
                    now,
                    now,
                    str(role or ""),
                    str(user_peer_id or ""),
                    str(agent_peer_id or ""),
                    str(config_id or ""),
                ),
            )
            connection.commit()
        return identifier

    def recover_expired_leases(self, *, now: float | None = None) -> int:
        self.initialize()
        current = float(now if now is not None else time.time())
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE outbox_events
                   SET state='pending', lease_owner=NULL, lease_until=NULL,
                       updated_at=?, error_code='lease_expired'
                 WHERE state='leased' AND lease_until IS NOT NULL AND lease_until <= ?
                """,
                (current, current),
            )
            return max(0, cursor.rowcount)

    def lease_batch(
        self,
        owner: str,
        *,
        limit: int,
        lease_seconds: float,
        now: float | None = None,
        blocked_identities: Iterable[tuple[str, ...]] = (),
    ) -> list[OutboxEvent]:
        """Lease due events while preserving order inside each session.

        A second process cannot lease a session that already has leased events.
        If an earlier event in a session is delayed, later events wait behind it.
        Multiple due events from the same session may be leased together and are
        returned in deterministic creation order for one remote batch.
        """
        self.initialize()
        current = float(now if now is not None else time.time())
        lease_until = current + max(1.0, float(lease_seconds))
        bounded_limit = max(1, min(100, int(limit)))
        blocked = {
            tuple(str(part) for part in identity[:3])
            for identity in blocked_identities
            if len(identity) >= 2
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE outbox_events
                   SET state='pending', lease_owner=NULL, lease_until=NULL,
                       updated_at=?, error_code='lease_expired'
                 WHERE state='leased' AND lease_until IS NOT NULL AND lease_until <= ?
                """,
                (current, current),
            )
            blocked_sql: list[str] = []
            blocked_args: list[str] = []
            for identity in sorted(blocked):
                if len(identity) >= 3:
                    blocked_sql.append(
                        "NOT (e.endpoint=? AND e.workspace_id=? AND e.config_id=?)"
                    )
                    blocked_args.extend(identity[:3])
                else:
                    blocked_sql.append("NOT (e.endpoint=? AND e.workspace_id=?)")
                    blocked_args.extend(identity[:2])
            exclusion = " AND " + " AND ".join(blocked_sql) if blocked_sql else ""
            rows = connection.execute(
                f"""
                SELECT e.* FROM outbox_events AS e
                 WHERE e.state='pending' AND e.next_attempt_at <= ?
                   AND NOT EXISTS (
                       SELECT 1 FROM outbox_events AS leased
                        WHERE leased.endpoint=e.endpoint
                          AND leased.workspace_id=e.workspace_id
                          AND leased.session_id=e.session_id
                          AND leased.state='leased'
                   )
                   AND NOT EXISTS (
                       SELECT 1 FROM outbox_events AS earlier
                        WHERE earlier.endpoint=e.endpoint
                          AND earlier.workspace_id=e.workspace_id
                          AND earlier.session_id=e.session_id
                          AND earlier.state='pending'
                          AND earlier.next_attempt_at > ?
                          AND (earlier.created_at < e.created_at OR
                               (earlier.created_at=e.created_at
                                AND earlier.event_id < e.event_id))
                   )
                   AND e.config_id = (
                       SELECT first.config_id FROM outbox_events AS first
                        WHERE first.endpoint=e.endpoint
                          AND first.workspace_id=e.workspace_id
                          AND first.session_id=e.session_id
                          AND first.state='pending'
                        ORDER BY first.created_at, first.event_id
                        LIMIT 1
                   )
                   {exclusion}
                 ORDER BY e.created_at, e.event_id
                 LIMIT ?
                """,
                (current, current, *blocked_args, bounded_limit),
            ).fetchall()
            identifiers = [str(row["event_id"]) for row in rows]
            if identifiers:
                placeholders = ",".join("?" for _ in identifiers)
                connection.execute(
                    f"""
                    UPDATE outbox_events
                       SET state='leased', lease_owner=?, lease_until=?, updated_at=?
                     WHERE state='pending' AND event_id IN ({placeholders})
                    """,
                    (str(owner), lease_until, current, *identifiers),
                )
                rows = connection.execute(
                    f"SELECT * FROM outbox_events WHERE event_id IN ({placeholders}) "
                    "ORDER BY created_at, event_id",
                    identifiers,
                ).fetchall()
            connection.commit()
        return [OutboxEvent.from_row(row) for row in rows]

    def mark_delivered(
        self, event_id: str, owner: str, *, now: float | None = None
    ) -> bool:
        return self.mark_delivered_many([event_id], owner, now=now) == 1

    def mark_delivered_many(
        self,
        event_ids: Iterable[str],
        owner: str,
        *,
        now: float | None = None,
    ) -> int:
        return self._finish_many(
            event_ids,
            owner,
            state="delivered",
            attempts_delta=1,
            error_code=None,
            next_attempt_at=float(now if now is not None else time.time()),
        )

    def reschedule(
        self,
        event_id: str,
        owner: str,
        *,
        next_attempt_at: float,
        error_code: str,
    ) -> bool:
        return (
            self.reschedule_many(
                [event_id],
                owner,
                next_attempt_at=next_attempt_at,
                error_code=error_code,
            )
            == 1
        )

    def reschedule_many(
        self,
        event_ids: Iterable[str],
        owner: str,
        *,
        next_attempt_at: float,
        error_code: str,
    ) -> int:
        return self._finish_many(
            event_ids,
            owner,
            state="pending",
            attempts_delta=1,
            error_code=error_code,
            next_attempt_at=float(next_attempt_at),
        )

    def defer_many(
        self,
        event_ids: Iterable[str],
        owner: str,
        *,
        next_attempt_at: float,
        error_code: str,
    ) -> int:
        """Release leased events without consuming a delivery attempt."""
        return self._finish_many(
            event_ids,
            owner,
            state="pending",
            attempts_delta=0,
            error_code=error_code,
            next_attempt_at=float(next_attempt_at),
        )

    def dead_letter(self, event_id: str, owner: str, *, error_code: str) -> bool:
        return self.dead_letter_many([event_id], owner, error_code=error_code) == 1

    def dead_letter_many(
        self, event_ids: Iterable[str], owner: str, *, error_code: str
    ) -> int:
        return self._finish_many(
            event_ids,
            owner,
            state="dead",
            attempts_delta=1,
            error_code=error_code,
            next_attempt_at=time.time(),
        )

    def _finish_many(
        self,
        event_ids: Iterable[str],
        owner: str,
        *,
        state: str,
        attempts_delta: int,
        error_code: str | None,
        next_attempt_at: float,
    ) -> int:
        if state not in VALID_STATES:
            raise ValueError(f"invalid outbox state: {state}")
        identifiers = [str(event_id) for event_id in event_ids]
        if not identifiers:
            return 0
        self.initialize()
        placeholders = ",".join("?" for _ in identifiers)
        now = time.time()
        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE outbox_events
                   SET state=?, attempts=attempts+?, next_attempt_at=?,
                       lease_owner=NULL, lease_until=NULL, updated_at=?, error_code=?
                 WHERE event_id IN ({placeholders})
                   AND state='leased' AND lease_owner=?
                """,
                (
                    state,
                    max(0, int(attempts_delta)),
                    float(next_attempt_at),
                    now,
                    _safe_error_code(error_code),
                    *identifiers,
                    str(owner),
                ),
            )
            return max(0, cursor.rowcount)

    def retry_dead_letters(
        self, *, limit: int = 100, now: float | None = None
    ) -> int:
        self.initialize()
        current = float(now if now is not None else time.time())
        bounded = max(1, min(1_000, int(limit)))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT event_id FROM outbox_events WHERE state='dead' "
                "ORDER BY updated_at LIMIT ?",
                (bounded,),
            ).fetchall()
            identifiers = [str(row["event_id"]) for row in rows]
            if identifiers:
                placeholders = ",".join("?" for _ in identifiers)
                connection.execute(
                    f"""
                    UPDATE outbox_events
                       SET state='pending', attempts=0, next_attempt_at=?,
                           lease_owner=NULL, lease_until=NULL, updated_at=?, error_code=NULL
                     WHERE event_id IN ({placeholders})
                    """,
                    (current, current, *identifiers),
                )
            connection.commit()
        return len(identifiers)

    def counts(self) -> dict[str, int]:
        self.initialize()
        counts = {state: 0 for state in sorted(VALID_STATES)}
        with self._connect() as connection:
            for row in connection.execute(
                "SELECT state, COUNT(*) AS count FROM outbox_events GROUP BY state"
            ):
                counts[str(row["state"])] = int(row["count"])
        counts["total"] = sum(counts.values())
        counts["active"] = counts["pending"] + counts["leased"]
        return counts

    def timing(self, *, now: float | None = None) -> dict[str, int | None]:
        self.initialize()
        current = float(now if now is not None else time.time())
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT MIN(CASE WHEN state IN ('pending','leased') THEN created_at END)
                           AS oldest_active_at,
                       MIN(CASE WHEN state='pending' THEN next_attempt_at END)
                           AS next_attempt_at
                  FROM outbox_events
                """
            ).fetchone()
        oldest = row["oldest_active_at"] if row else None
        next_attempt = row["next_attempt_at"] if row else None
        return {
            "oldest_active_age_seconds": (
                max(0, int(current - float(oldest))) if oldest is not None else None
            ),
            "next_attempt_in_seconds": (
                max(0, int(float(next_attempt) - current))
                if next_attempt is not None
                else None
            ),
        }

    def get(self, event_id: str) -> OutboxEvent | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM outbox_events WHERE event_id=?", (str(event_id),)
            ).fetchone()
        return OutboxEvent.from_row(row) if row else None

    def prune(self, *, now: float | None = None) -> int:
        self.initialize()
        current = float(now if now is not None else time.time())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            removed = self._prune_locked(connection, current)
            connection.commit()
        return removed

    def _prune_locked(self, connection: sqlite3.Connection, now: float) -> int:
        delivered_before = now - self.delivered_retention_seconds
        dead_before = now - self.dead_retention_seconds
        cursor = connection.execute(
            "DELETE FROM outbox_events WHERE "
            "(state='delivered' AND updated_at <= ?) OR "
            "(state='dead' AND updated_at <= ?)",
            (delivered_before, dead_before),
        )
        removed = max(0, cursor.rowcount)
        count = int(
            connection.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0]
        )
        excess = max(0, count - self.max_rows + 1)
        if excess:
            cursor = connection.execute(
                """
                DELETE FROM outbox_events WHERE event_id IN (
                    SELECT event_id FROM outbox_events
                     WHERE state IN ('delivered','dead')
                     ORDER BY CASE state WHEN 'delivered' THEN 0 ELSE 1 END,
                              updated_at
                     LIMIT ?
                )
                """,
                (excess,),
            )
            removed += max(0, cursor.rowcount)
        return removed


def _safe_error_code(value: Any) -> str | None:
    if value is None:
        return None
    text = "".join(
        character
        for character in str(value).lower()
        if character.isalnum() or character in "_-."
    )
    return text[:80] or "unknown"
