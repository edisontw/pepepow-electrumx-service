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
    ) -> dict[str, Any]:
        self.initialize()
        with self._connect() as connection:
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
        return self.get_payment(payment_id)

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
            return dict(refreshed)
