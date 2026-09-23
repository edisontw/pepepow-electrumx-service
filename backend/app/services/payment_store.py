import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .payment_state import (
    PaymentEvaluation,
    PaymentTransactionObservation,
    evaluate_payment_observations,
)


class PaymentStoreError(RuntimeError):
    pass


class PaymentNotFoundError(PaymentStoreError):
    pass


class PaymentIdempotencyConflictError(PaymentStoreError):
    pass


def _event_id(payment_id: str, payment_version: int, event_type: str) -> str:
    material = f"pepew-event-v1:{payment_id}:{int(payment_version)}:{event_type}".encode("utf-8")
    return "evt_" + hashlib.sha256(material).hexdigest()


def _delivery_id(event_id: str, endpoint_id: str) -> str:
    material = f"pepew-webhook-delivery-v1:{event_id}:{endpoint_id}".encode("utf-8")
    return "dlv_" + hashlib.sha256(material).hexdigest()


def _event_payload(
    payment: dict[str, Any],
    *,
    event_id: str,
    event_type: str,
    created_at: int,
) -> str:
    payload = {
        "schema_version": 1,
        "event_id": event_id,
        "event_type": event_type,
        "payment_id": str(payment["payment_id"]),
        "payment_version": int(payment["version"]),
        "created_at": int(created_at),
        "data": {
            "status": str(payment["status"]),
            "amount_sats": int(payment["amount_sats"]),
            "received_sats": int(payment["received_sats"]),
            "confirmed_sats": int(payment["confirmed_sats"]),
            "policy_confirmed_sats": int(payment["policy_confirmed_sats"]),
            "confirmations_required": int(payment["confirmations_required"]),
            "expires_at": int(payment["expires_at"]),
        },
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _insert_event(
    connection: sqlite3.Connection,
    payment: dict[str, Any],
    *,
    event_type: str,
    created_at: int,
) -> str:
    event_id = _event_id(
        str(payment["payment_id"]),
        int(payment["version"]),
        event_type,
    )
    payload_json = _event_payload(
        payment,
        event_id=event_id,
        event_type=event_type,
        created_at=int(created_at),
    )
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO events (
            event_id, payment_id, event_type, payment_version, created_at, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            str(payment["payment_id"]),
            event_type,
            int(payment["version"]),
            int(created_at),
            payload_json,
        ),
    )

    if cursor.rowcount:
        endpoints = connection.execute(
            """
            SELECT endpoint_id, event_types_json
            FROM webhook_endpoints
            WHERE enabled = 1
            ORDER BY endpoint_id
            """
        ).fetchall()
        for endpoint in endpoints:
            raw_types = endpoint["event_types_json"]
            if isinstance(raw_types, str):
                try:
                    event_types = json.loads(raw_types)
                except json.JSONDecodeError:
                    event_types = []
                if not isinstance(event_types, list) or event_type not in event_types:
                    continue

            endpoint_id = str(endpoint["endpoint_id"])
            delivery_id = _delivery_id(event_id, endpoint_id)
            connection.execute(
                """
                INSERT OR IGNORE INTO webhook_deliveries (
                    delivery_id, event_id, endpoint_id, status,
                    attempt_count, next_attempt_at, created_at, updated_at
                ) VALUES (?, ?, ?, 'pending', 0, ?, ?, ?)
                """,
                (
                    delivery_id,
                    event_id,
                    endpoint_id,
                    int(created_at),
                    int(created_at),
                    int(created_at),
                ),
            )
    return event_id


class PaymentStore:
    def __init__(self, path: str) -> None:
        self.path = str(path)
        self._initialized = False
        self._init_lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        if self.path != ":memory:":
            Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = NORMAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS payments (
                        payment_id TEXT PRIMARY KEY,
                        address TEXT NOT NULL,
                        scripthash TEXT NOT NULL,
                        amount_sats INTEGER NOT NULL CHECK (amount_sats > 0),
                        confirmations_required INTEGER NOT NULL CHECK (confirmations_required >= 0),
                        created_at INTEGER NOT NULL,
                        created_height INTEGER NOT NULL CHECK (created_height >= 0),
                        expires_at INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
                        received_sats INTEGER NOT NULL DEFAULT 0,
                        confirmed_sats INTEGER NOT NULL DEFAULT 0,
                        policy_confirmed_sats INTEGER NOT NULL DEFAULT 0,
                        label TEXT,
                        message TEXT,
                        updated_at INTEGER NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_payments_scripthash_status
                    ON payments (scripthash, status);

                    CREATE INDEX IF NOT EXISTS idx_payments_expires_at
                    ON payments (expires_at);

                    CREATE TABLE IF NOT EXISTS payment_idempotency_keys (
                        idempotency_key TEXT PRIMARY KEY,
                        request_hash TEXT NOT NULL,
                        payment_id TEXT NOT NULL UNIQUE,
                        created_at INTEGER NOT NULL,
                        FOREIGN KEY (payment_id) REFERENCES payments(payment_id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS payment_baseline_transactions (
                        payment_id TEXT NOT NULL,
                        txid TEXT NOT NULL,
                        PRIMARY KEY (payment_id, txid),
                        FOREIGN KEY (payment_id) REFERENCES payments(payment_id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS payment_transactions (
                        payment_id TEXT NOT NULL,
                        txid TEXT NOT NULL,
                        vout INTEGER NOT NULL CHECK (vout >= 0),
                        value_sats INTEGER NOT NULL CHECK (value_sats > 0),
                        height INTEGER NOT NULL,
                        first_seen_at INTEGER,
                        updated_at INTEGER NOT NULL,
                        PRIMARY KEY (payment_id, txid, vout),
                        FOREIGN KEY (payment_id) REFERENCES payments(payment_id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_payment_transactions_txid
                    ON payment_transactions (txid);

                    CREATE TABLE IF NOT EXISTS events (
                        event_id TEXT PRIMARY KEY,
                        payment_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        payment_version INTEGER NOT NULL,
                        created_at INTEGER NOT NULL,
                        payload_json TEXT,
                        UNIQUE (payment_id, event_type, payment_version),
                        FOREIGN KEY (payment_id) REFERENCES payments(payment_id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS webhook_endpoints (
                        endpoint_id TEXT PRIMARY KEY,
                        url TEXT NOT NULL,
                        event_types_json TEXT,
                        enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS webhook_deliveries (
                        delivery_id TEXT PRIMARY KEY,
                        event_id TEXT NOT NULL,
                        endpoint_id TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (
                            status IN ('pending', 'retry', 'delivered', 'dead')
                        ),
                        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
                        next_attempt_at INTEGER NOT NULL,
                        last_attempt_at INTEGER,
                        http_status INTEGER,
                        error_code TEXT,
                        delivered_at INTEGER,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        UNIQUE (event_id, endpoint_id),
                        FOREIGN KEY (event_id) REFERENCES events(event_id) ON DELETE CASCADE,
                        FOREIGN KEY (endpoint_id) REFERENCES webhook_endpoints(endpoint_id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_due
                    ON webhook_deliveries (status, next_attempt_at);

                    CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_event
                    ON webhook_deliveries (event_id);

                    CREATE TABLE IF NOT EXISTS chain_state (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        tip_height INTEGER NOT NULL CHECK (tip_height >= 0),
                        tip_hash TEXT,
                        updated_at INTEGER NOT NULL
                    );
                    """
                )
            self._initialized = True

    def create_payment(
        self,
        *,
        payment_id: str,
        address: str,
        scripthash: str,
        amount_sats: int,
        confirmations_required: int,
        created_at: int,
        created_height: int,
        expires_at: int,
        label: str | None = None,
        message: str | None = None,
        baseline_txids: tuple[str, ...] = (),
        idempotency_key: str | None = None,
        request_hash: str | None = None,
    ) -> dict[str, Any]:
        self.initialize()
        if (idempotency_key is None) != (request_hash is None):
            raise PaymentStoreError("Idempotency key and request hash must be provided together.")

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if idempotency_key is not None:
                existing = connection.execute(
                    """
                    SELECT request_hash, payment_id
                    FROM payment_idempotency_keys
                    WHERE idempotency_key = ?
                    """,
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    if str(existing["request_hash"]) != str(request_hash):
                        raise PaymentIdempotencyConflictError(idempotency_key)
                    replay = connection.execute(
                        "SELECT * FROM payments WHERE payment_id = ?",
                        (str(existing["payment_id"]),),
                    ).fetchone()
                    if replay is None:
                        raise PaymentStoreError("Idempotency mapping references a missing payment.")
                    return dict(replay)

            connection.execute(
                """
                INSERT INTO payments (
                    payment_id, address, scripthash, amount_sats,
                    confirmations_required, created_at, created_height,
                    expires_at, status, version, received_sats,
                    confirmed_sats, policy_confirmed_sats,
                    label, message, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'waiting', 1, 0, 0, 0, ?, ?, ?)
                """,
                (
                    payment_id,
                    address,
                    scripthash,
                    int(amount_sats),
                    int(confirmations_required),
                    int(created_at),
                    int(created_height),
                    int(expires_at),
                    label,
                    message,
                    int(created_at),
                ),
            )
            connection.executemany(
                """
                INSERT OR IGNORE INTO payment_baseline_transactions (payment_id, txid)
                VALUES (?, ?)
                """,
                [(payment_id, txid.lower()) for txid in baseline_txids],
            )
            created_payment = connection.execute(
                "SELECT * FROM payments WHERE payment_id = ?",
                (payment_id,),
            ).fetchone()
            if created_payment is None:
                raise PaymentStoreError("Created payment could not be reloaded.")
            _insert_event(
                connection,
                dict(created_payment),
                event_type="payment.created",
                created_at=int(created_at),
            )
            if idempotency_key is not None:
                connection.execute(
                    """
                    INSERT INTO payment_idempotency_keys (
                        idempotency_key, request_hash, payment_id, created_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        idempotency_key,
                        str(request_hash),
                        payment_id,
                        int(created_at),
                    ),
                )
        return self.get_payment(payment_id)

    def get_payment_by_idempotency_key(
        self,
        idempotency_key: str,
        *,
        request_hash: str,
    ) -> dict[str, Any] | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT request_hash, payment_id
                FROM payment_idempotency_keys
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
        if row is None:
            return None
        if str(row["request_hash"]) != str(request_hash):
            raise PaymentIdempotencyConflictError(idempotency_key)
        try:
            return self.get_payment(str(row["payment_id"]))
        except PaymentNotFoundError as exc:
            raise PaymentStoreError(
                "Idempotency mapping references a missing payment."
            ) from exc

    def get_payment(self, payment_id: str) -> dict[str, Any]:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM payments WHERE payment_id = ?",
                (payment_id,),
            ).fetchone()
        if row is None:
            raise PaymentNotFoundError(payment_id)
        return dict(row)

    def list_events(
        self,
        *,
        payment_id: str | None = None,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.initialize()
        bounded_limit = min(1000, max(1, int(limit)))
        params: list[Any] = [max(0, int(after_sequence))]
        where = "rowid > ?"
        if payment_id is not None:
            where += " AND payment_id = ?"
            params.append(payment_id)
        params.append(bounded_limit)

        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    rowid AS sequence,
                    event_id,
                    payment_id,
                    event_type,
                    payment_version,
                    created_at,
                    payload_json
                FROM events
                WHERE {where}
                ORDER BY rowid ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()

        events: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            payload_json = item.pop("payload_json", None)
            if isinstance(payload_json, str):
                try:
                    item["payload"] = json.loads(payload_json)
                except json.JSONDecodeError:
                    item["payload"] = None
            else:
                item["payload"] = None
            events.append(item)
        return events

    def create_webhook_endpoint(
        self,
        *,
        endpoint_id: str,
        url: str,
        event_types: tuple[str, ...] | None,
        created_at: int,
    ) -> dict[str, Any]:
        self.initialize()
        event_types_json = (
            None
            if event_types is None
            else json.dumps(list(event_types), separators=(",", ":"))
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO webhook_endpoints (
                    endpoint_id, url, event_types_json, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, 1, ?, ?)
                """,
                (
                    endpoint_id,
                    url,
                    event_types_json,
                    int(created_at),
                    int(created_at),
                ),
            )
        return self.get_webhook_endpoint(endpoint_id)

    def get_webhook_endpoint(self, endpoint_id: str) -> dict[str, Any]:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT endpoint_id, url, event_types_json, enabled, created_at, updated_at
                FROM webhook_endpoints
                WHERE endpoint_id = ?
                """,
                (endpoint_id,),
            ).fetchone()
        if row is None:
            raise PaymentNotFoundError(endpoint_id)
        item = dict(row)
        raw_types = item.pop("event_types_json", None)
        item["event_types"] = None if raw_types is None else json.loads(raw_types)
        item["enabled"] = bool(item["enabled"])
        return item

    def list_webhook_endpoints(self) -> list[dict[str, Any]]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT endpoint_id, url, event_types_json, enabled, created_at, updated_at
                FROM webhook_endpoints
                ORDER BY created_at ASC, endpoint_id ASC
                """
            ).fetchall()
        endpoints: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            raw_types = item.pop("event_types_json", None)
            item["event_types"] = None if raw_types is None else json.loads(raw_types)
            item["enabled"] = bool(item["enabled"])
            endpoints.append(item)
        return endpoints

    def disable_webhook_endpoint(self, endpoint_id: str, *, updated_at: int) -> None:
        self.initialize()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE webhook_endpoints
                SET enabled = 0, updated_at = ?
                WHERE endpoint_id = ?
                """,
                (int(updated_at), endpoint_id),
            )
        if cursor.rowcount == 0:
            raise PaymentNotFoundError(endpoint_id)

    def list_due_webhook_deliveries(
        self,
        *,
        now: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        self.initialize()
        bounded_limit = min(100, max(1, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    d.delivery_id,
                    d.event_id,
                    d.endpoint_id,
                    d.status,
                    d.attempt_count,
                    d.next_attempt_at,
                    e.payload_json,
                    w.url
                FROM webhook_deliveries AS d
                JOIN events AS e ON e.event_id = d.event_id
                JOIN webhook_endpoints AS w ON w.endpoint_id = d.endpoint_id
                WHERE d.status IN ('pending', 'retry')
                  AND d.next_attempt_at <= ?
                  AND w.enabled = 1
                ORDER BY d.next_attempt_at ASC, d.created_at ASC, d.delivery_id ASC
                LIMIT ?
                """,
                (int(now), bounded_limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_webhook_delivery(self, delivery_id: str) -> dict[str, Any]:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM webhook_deliveries
                WHERE delivery_id = ?
                """,
                (delivery_id,),
            ).fetchone()
        if row is None:
            raise PaymentNotFoundError(delivery_id)
        return dict(row)

    def list_webhook_deliveries(
        self,
        *,
        endpoint_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.initialize()
        bounded_limit = min(500, max(1, int(limit)))
        params: list[Any] = []
        where = ""
        if endpoint_id is not None:
            where = "WHERE d.endpoint_id = ?"
            params.append(endpoint_id)
        params.append(bounded_limit)

        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    d.delivery_id,
                    d.event_id,
                    d.endpoint_id,
                    d.status,
                    d.attempt_count,
                    d.next_attempt_at,
                    d.last_attempt_at,
                    d.http_status,
                    d.error_code,
                    d.delivered_at,
                    d.created_at,
                    d.updated_at,
                    e.payment_id,
                    e.event_type,
                    e.payment_version
                FROM webhook_deliveries AS d
                JOIN events AS e ON e.event_id = d.event_id
                {where}
                ORDER BY d.created_at DESC, d.delivery_id DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_webhook_delivery_success(
        self,
        delivery_id: str,
        *,
        http_status: int,
        attempted_at: int,
    ) -> None:
        self.initialize()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE webhook_deliveries
                SET status = 'delivered',
                    attempt_count = attempt_count + 1,
                    last_attempt_at = ?,
                    http_status = ?,
                    error_code = NULL,
                    delivered_at = ?,
                    updated_at = ?
                WHERE delivery_id = ?
                """,
                (
                    int(attempted_at),
                    int(http_status),
                    int(attempted_at),
                    int(attempted_at),
                    delivery_id,
                ),
            )
        if cursor.rowcount == 0:
            raise PaymentNotFoundError(delivery_id)

    def mark_webhook_delivery_failure(
        self,
        delivery_id: str,
        *,
        http_status: int | None,
        error_code: str,
        attempted_at: int,
        next_attempt_at: int,
        dead: bool,
    ) -> None:
        self.initialize()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE webhook_deliveries
                SET status = ?,
                    attempt_count = attempt_count + 1,
                    last_attempt_at = ?,
                    http_status = ?,
                    error_code = ?,
                    next_attempt_at = ?,
                    updated_at = ?
                WHERE delivery_id = ?
                """,
                (
                    "dead" if dead else "retry",
                    int(attempted_at),
                    None if http_status is None else int(http_status),
                    error_code,
                    int(next_attempt_at),
                    int(attempted_at),
                    delivery_id,
                ),
            )
        if cursor.rowcount == 0:
            raise PaymentNotFoundError(delivery_id)


    def get_payments_by_scripthash(self, scripthash: str) -> list[dict[str, Any]]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM payments
                WHERE scripthash = ?
                ORDER BY created_at ASC, payment_id ASC
                """,
                (scripthash,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_watch_scripthashes(self, *, limit: int) -> list[str]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT scripthash, MAX(created_at) AS last_created
                FROM payments
                GROUP BY scripthash
                ORDER BY last_created DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [str(row["scripthash"]) for row in rows]

    def get_baseline_txids(self, payment_id: str) -> set[str]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT txid
                FROM payment_baseline_transactions
                WHERE payment_id = ?
                """,
                (payment_id,),
            ).fetchall()
        return {str(row["txid"]).lower() for row in rows}

    def delete_transactions_not_in(self, payment_id: str, txids: set[str]) -> None:
        self.initialize()
        normalized = {txid.lower() for txid in txids}
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT txid
                FROM payment_transactions
                WHERE payment_id = ?
                """,
                (payment_id,),
            ).fetchall()
            stale = [
                str(row["txid"])
                for row in rows
                if str(row["txid"]).lower() not in normalized
            ]
            connection.executemany(
                """
                DELETE FROM payment_transactions
                WHERE payment_id = ? AND txid = ?
                """,
                [(payment_id, txid) for txid in stale],
            )

    def list_refreshable_payment_ids(self, *, now: int, limit: int) -> list[str]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT p.payment_id
                FROM payments AS p
                WHERE p.status != 'expired'
                  AND (
                    p.expires_at <= ?
                    OR EXISTS (
                        SELECT 1
                        FROM payment_transactions AS t
                        WHERE t.payment_id = p.payment_id
                    )
                  )
                ORDER BY p.updated_at DESC
                LIMIT ?
                """,
                (int(now), max(1, int(limit))),
            ).fetchall()
        return [str(row["payment_id"]) for row in rows]

    def set_chain_tip(self, tip_height: int, *, tip_hash: str | None, updated_at: int) -> None:
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO chain_state (id, tip_height, tip_hash, updated_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    tip_height = excluded.tip_height,
                    tip_hash = excluded.tip_hash,
                    updated_at = excluded.updated_at
                """,
                (int(tip_height), tip_hash, int(updated_at)),
            )

    def get_chain_tip(self) -> dict[str, Any] | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT tip_height, tip_hash, updated_at FROM chain_state WHERE id = 1"
            ).fetchone()
        return None if row is None else dict(row)

    def get_observations(self, payment_id: str) -> tuple[PaymentTransactionObservation, ...]:
        self.initialize()
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM payments WHERE payment_id = ?",
                (payment_id,),
            ).fetchone()
            if exists is None:
                raise PaymentNotFoundError(payment_id)
            rows = connection.execute(
                """
                SELECT txid, vout, value_sats, height, first_seen_at
                FROM payment_transactions
                WHERE payment_id = ?
                ORDER BY first_seen_at, txid, vout
                """,
                (payment_id,),
            ).fetchall()
        return tuple(
            PaymentTransactionObservation(
                txid=str(row["txid"]),
                vout=int(row["vout"]),
                value_sats=int(row["value_sats"]),
                height=int(row["height"]),
                first_seen_at=None if row["first_seen_at"] is None else int(row["first_seen_at"]),
            )
            for row in rows
        )

    def upsert_transaction(
        self,
        payment_id: str,
        observation: PaymentTransactionObservation,
        *,
        updated_at: int,
    ) -> None:
        self.initialize()
        with self._connect() as connection:
            payment = connection.execute(
                "SELECT 1 FROM payments WHERE payment_id = ?",
                (payment_id,),
            ).fetchone()
            if payment is None:
                raise PaymentNotFoundError(payment_id)

            existing = connection.execute(
                """
                SELECT value_sats, first_seen_at
                FROM payment_transactions
                WHERE payment_id = ? AND txid = ? AND vout = ?
                """,
                (payment_id, observation.txid, int(observation.vout)),
            ).fetchone()

            if existing is not None and int(existing["value_sats"]) != int(observation.value_sats):
                raise PaymentStoreError("Conflicting value for existing payment outpoint.")

            first_seen_values = [
                value
                for value in (
                    None if existing is None else existing["first_seen_at"],
                    observation.first_seen_at,
                )
                if value is not None
            ]
            first_seen_at = min(int(value) for value in first_seen_values) if first_seen_values else None

            connection.execute(
                """
                INSERT INTO payment_transactions (
                    payment_id, txid, vout, value_sats, height, first_seen_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(payment_id, txid, vout) DO UPDATE SET
                    height = excluded.height,
                    first_seen_at = excluded.first_seen_at,
                    updated_at = excluded.updated_at
                """,
                (
                    payment_id,
                    observation.txid,
                    int(observation.vout),
                    int(observation.value_sats),
                    int(observation.height),
                    first_seen_at,
                    int(updated_at),
                ),
            )

    def refresh_payment(self, payment_id: str, *, now: int) -> dict[str, Any]:
        self.initialize()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM payments WHERE payment_id = ?",
                (payment_id,),
            ).fetchone()
            if row is None:
                raise PaymentNotFoundError(payment_id)

            tip_row = connection.execute(
                "SELECT tip_height FROM chain_state WHERE id = 1"
            ).fetchone()
            tip_height = int(tip_row["tip_height"]) if tip_row is not None else int(row["created_height"])

            tx_rows = connection.execute(
                """
                SELECT txid, vout, value_sats, height, first_seen_at
                FROM payment_transactions
                WHERE payment_id = ?
                ORDER BY first_seen_at, txid, vout
                """,
                (payment_id,),
            ).fetchall()

            observations = tuple(
                PaymentTransactionObservation(
                    txid=str(tx["txid"]),
                    vout=int(tx["vout"]),
                    value_sats=int(tx["value_sats"]),
                    height=int(tx["height"]),
                    first_seen_at=None if tx["first_seen_at"] is None else int(tx["first_seen_at"]),
                )
                for tx in tx_rows
            )

            evaluation: PaymentEvaluation = evaluate_payment_observations(
                int(row["amount_sats"]),
                observations,
                tip_height=tip_height,
                confirmations_required=int(row["confirmations_required"]),
                created_height=int(row["created_height"]),
                created_at=int(row["created_at"]),
                expires_at=int(row["expires_at"]),
                now=int(now),
            )

            changed = (
                str(row["status"]) != evaluation.status
                or int(row["received_sats"]) != evaluation.received_sats
                or int(row["confirmed_sats"]) != evaluation.confirmed_sats
                or int(row["policy_confirmed_sats"]) != evaluation.policy_confirmed_sats
            )

            if changed:
                connection.execute(
                    """
                    UPDATE payments
                    SET status = ?,
                        received_sats = ?,
                        confirmed_sats = ?,
                        policy_confirmed_sats = ?,
                        version = version + 1,
                        updated_at = ?
                    WHERE payment_id = ?
                    """,
                    (
                        evaluation.status,
                        evaluation.received_sats,
                        evaluation.confirmed_sats,
                        evaluation.policy_confirmed_sats,
                        int(now),
                        payment_id,
                    ),
                )

            refreshed = connection.execute(
                "SELECT * FROM payments WHERE payment_id = ?",
                (payment_id,),
            ).fetchone()
            if refreshed is None:
                raise PaymentNotFoundError(payment_id)

            refreshed_dict = dict(refreshed)
            if changed:
                _insert_event(
                    connection,
                    refreshed_dict,
                    event_type=f"payment.{evaluation.status}",
                    created_at=int(now),
                )
            return refreshed_dict
