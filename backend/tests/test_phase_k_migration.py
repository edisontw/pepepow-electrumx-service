import json
import sqlite3

from app.services.merchant_store import LEGACY_MERCHANT_ID, MerchantStore
from app.services.payment_store import PaymentStore


def create_pre_k_database(path) -> str:
    payload = json.dumps(
        {
            "schema_version": 1,
            "event_id": "evt_legacy",
            "event_type": "payment.created",
            "payment_id": "pay_legacy",
            "payment_version": 1,
            "created_at": 1000,
            "data": {
                "status": "waiting",
                "amount_sats": 100,
                "received_sats": 0,
                "confirmed_sats": 0,
                "policy_confirmed_sats": 0,
                "confirmations_required": 3,
                "expires_at": 1900,
                "merchant_reference": "ORDER-LEGACY",
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE payments (
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
                merchant_reference TEXT,
                updated_at INTEGER NOT NULL
            );

            CREATE UNIQUE INDEX idx_payments_merchant_reference
            ON payments (merchant_reference)
            WHERE merchant_reference IS NOT NULL;

            CREATE TABLE payment_idempotency_keys (
                idempotency_key TEXT PRIMARY KEY,
                request_hash TEXT NOT NULL,
                payment_id TEXT NOT NULL UNIQUE,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (payment_id)
                    REFERENCES payments(payment_id) ON DELETE CASCADE
            );

            CREATE TABLE payment_baseline_transactions (
                payment_id TEXT NOT NULL,
                txid TEXT NOT NULL,
                PRIMARY KEY (payment_id, txid),
                FOREIGN KEY (payment_id)
                    REFERENCES payments(payment_id) ON DELETE CASCADE
            );

            CREATE TABLE payment_transactions (
                payment_id TEXT NOT NULL,
                txid TEXT NOT NULL,
                vout INTEGER NOT NULL CHECK (vout >= 0),
                value_sats INTEGER NOT NULL CHECK (value_sats > 0),
                height INTEGER NOT NULL,
                first_seen_at INTEGER,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (payment_id, txid, vout),
                FOREIGN KEY (payment_id)
                    REFERENCES payments(payment_id) ON DELETE CASCADE
            );

            CREATE TABLE events (
                event_id TEXT PRIMARY KEY,
                payment_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payment_version INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                payload_json TEXT,
                UNIQUE (payment_id, event_type, payment_version),
                FOREIGN KEY (payment_id)
                    REFERENCES payments(payment_id) ON DELETE CASCADE
            );

            CREATE TABLE webhook_endpoints (
                endpoint_id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                event_types_json TEXT,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE webhook_deliveries (
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
                FOREIGN KEY (event_id)
                    REFERENCES events(event_id) ON DELETE CASCADE,
                FOREIGN KEY (endpoint_id)
                    REFERENCES webhook_endpoints(endpoint_id) ON DELETE CASCADE
            );

            CREATE TABLE chain_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                tip_height INTEGER NOT NULL CHECK (tip_height >= 0),
                tip_hash TEXT,
                updated_at INTEGER NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO payments (
                payment_id, address, scripthash, amount_sats,
                confirmations_required, created_at, created_height,
                expires_at, status, version, received_sats,
                confirmed_sats, policy_confirmed_sats,
                label, message, merchant_reference, updated_at
            ) VALUES (
                'pay_legacy',
                'PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb',
                ?,
                100,
                3,
                1000,
                500,
                1900,
                'waiting',
                1,
                0,
                0,
                0,
                'Legacy',
                'Order',
                'ORDER-LEGACY',
                1000
            )
            """,
            ("11" * 32,),
        )
        connection.execute(
            """
            INSERT INTO payment_idempotency_keys (
                idempotency_key, request_hash, payment_id, created_at
            ) VALUES (
                'retry-legacy', 'same-request', 'pay_legacy', 1000
            )
            """
        )
        connection.execute(
            """
            INSERT INTO payment_baseline_transactions (payment_id, txid)
            VALUES ('pay_legacy', ?)
            """,
            ("a" * 64,),
        )
        connection.execute(
            """
            INSERT INTO payment_transactions (
                payment_id, txid, vout, value_sats, height,
                first_seen_at, updated_at
            ) VALUES (
                'pay_legacy', ?, 0, 100, 0, 1001, 1001
            )
            """,
            ("b" * 64,),
        )
        connection.execute(
            """
            INSERT INTO events (
                event_id, payment_id, event_type,
                payment_version, created_at, payload_json
            ) VALUES (
                'evt_legacy', 'pay_legacy', 'payment.created',
                1, 1000, ?
            )
            """,
            (payload,),
        )
        connection.execute(
            """
            INSERT INTO webhook_endpoints (
                endpoint_id, url, event_types_json,
                enabled, created_at, updated_at
            ) VALUES (
                'wh_legacy',
                'https://merchant.example/hook',
                NULL,
                1,
                900,
                900
            )
            """
        )
        connection.execute(
            """
            INSERT INTO webhook_deliveries (
                delivery_id, event_id, endpoint_id, status,
                attempt_count, next_attempt_at, last_attempt_at,
                http_status, error_code, delivered_at,
                created_at, updated_at
            ) VALUES (
                'dlv_legacy',
                'evt_legacy',
                'wh_legacy',
                'delivered',
                1,
                1000,
                1000,
                204,
                NULL,
                1000,
                1000,
                1000
            )
            """
        )
        connection.execute(
            """
            INSERT INTO chain_state (
                id, tip_height, tip_hash, updated_at
            ) VALUES (1, 500, 'tip', 1000)
            """
        )
    return payload


def table_counts(path) -> dict[str, int]:
    names = (
        "payments",
        "payment_idempotency_keys",
        "payment_baseline_transactions",
        "payment_transactions",
        "events",
        "webhook_endpoints",
        "webhook_deliveries",
        "chain_state",
    )
    with sqlite3.connect(path) as connection:
        return {
            name: connection.execute(
                f"SELECT COUNT(*) FROM {name}"
            ).fetchone()[0]
            for name in names
        }


def test_pre_k_database_migrates_losslessly_and_restart_safely(tmp_path):
    path = tmp_path / "pre-k.sqlite3"
    original_payload = create_pre_k_database(path)
    before_counts = table_counts(path)

    store = PaymentStore(str(path))
    store.initialize()

    assert table_counts(path) == before_counts

    with sqlite3.connect(path) as connection:
        payment_owner = connection.execute(
            """
            SELECT merchant_id
            FROM payments
            WHERE payment_id = 'pay_legacy'
            """
        ).fetchone()[0]
        idempotency_owner = connection.execute(
            """
            SELECT merchant_id
            FROM payment_idempotency_keys
            WHERE idempotency_key = 'retry-legacy'
            """
        ).fetchone()[0]
        event_owner, migrated_payload = connection.execute(
            """
            SELECT merchant_id, payload_json
            FROM events
            WHERE event_id = 'evt_legacy'
            """
        ).fetchone()
        endpoint_owner = connection.execute(
            """
            SELECT merchant_id
            FROM webhook_endpoints
            WHERE endpoint_id = 'wh_legacy'
            """
        ).fetchone()[0]
        delivery_relation = connection.execute(
            """
            SELECT event_id, endpoint_id, status, http_status
            FROM webhook_deliveries
            WHERE delivery_id = 'dlv_legacy'
            """
        ).fetchone()

        reference_index = connection.execute(
            "PRAGMA index_info(idx_payments_merchant_reference)"
        ).fetchall()
        idempotency_pk = {
            row[1]: row[5]
            for row in connection.execute(
                "PRAGMA table_info(payment_idempotency_keys)"
            ).fetchall()
        }

    assert payment_owner == LEGACY_MERCHANT_ID
    assert idempotency_owner == LEGACY_MERCHANT_ID
    assert event_owner == LEGACY_MERCHANT_ID
    assert endpoint_owner == LEGACY_MERCHANT_ID
    assert migrated_payload == original_payload
    assert delivery_relation == ("evt_legacy", "wh_legacy", "delivered", 204)
    assert [row[2] for row in reference_index] == [
        "merchant_id",
        "merchant_reference",
    ]
    assert idempotency_pk["merchant_id"] == 1
    assert idempotency_pk["idempotency_key"] == 2

    legacy = MerchantStore(str(path)).get_merchant(LEGACY_MERCHANT_ID)
    assert legacy["enabled"] is True
    assert MerchantStore(str(path)).list_credentials(LEGACY_MERCHANT_ID) == []

    # A second initialize from a fresh object must be a no-op for authoritative
    # data and historical event bytes.
    PaymentStore(str(path)).initialize()
    assert table_counts(path) == before_counts
    with sqlite3.connect(path) as connection:
        payload_after_restart = connection.execute(
            """
            SELECT payload_json
            FROM events
            WHERE event_id = 'evt_legacy'
            """
        ).fetchone()[0]
    assert payload_after_restart == original_payload
