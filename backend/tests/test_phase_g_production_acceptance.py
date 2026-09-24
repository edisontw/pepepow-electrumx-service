import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "phase_g_production_acceptance.py"
SPEC = importlib.util.spec_from_file_location("phase_g_production_acceptance", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
acceptance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acceptance)


def create_acceptance_db(path: Path, *, with_reference_schema: bool = True) -> None:
    with sqlite3.connect(path) as connection:
        reference_column = ", merchant_reference TEXT" if with_reference_schema else ""
        connection.executescript(
            f"""
            CREATE TABLE payments (
                payment_id TEXT PRIMARY KEY,
                address TEXT NOT NULL
                {reference_column}
            );

            CREATE TABLE events (
                event_id TEXT PRIMARY KEY,
                payment_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT
            );

            CREATE TABLE webhook_endpoints (
                endpoint_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL
            );
            """
        )
        if with_reference_schema:
            connection.execute(
                """
                CREATE UNIQUE INDEX idx_payments_merchant_reference
                ON payments (merchant_reference)
                WHERE merchant_reference IS NOT NULL
                """
            )
        connection.execute(
            "INSERT INTO payments (payment_id, address) VALUES (?, ?)",
            ("pay_existing", "PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb"),
        )


def test_schema_check_requires_column_and_unique_index(tmp_path):
    good = tmp_path / "good.sqlite3"
    create_acceptance_db(good, with_reference_schema=True)
    acceptance.verify_merchant_reference_schema(good)

    old = tmp_path / "old.sqlite3"
    create_acceptance_db(old, with_reference_schema=False)
    with pytest.raises(acceptance.AcceptanceError):
        acceptance.verify_merchant_reference_schema(old)


def test_single_created_event_requires_exactly_one_event_and_reference(tmp_path):
    path = tmp_path / "payments.sqlite3"
    create_acceptance_db(path, with_reference_schema=True)

    payload = {
        "data": {
            "merchant_reference": "phase-g-acceptance/test",
        }
    }
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO events (event_id, payment_id, event_type, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                "evt_one",
                "pay_existing",
                "payment.created",
                json.dumps(payload),
            ),
        )

    acceptance.verify_single_created_event(
        path,
        "pay_existing",
        "phase-g-acceptance/test",
    )

    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO events (event_id, payment_id, event_type, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                "evt_two",
                "pay_existing",
                "payment.waiting",
                json.dumps(payload),
            ),
        )

    with pytest.raises(acceptance.AcceptanceError):
        acceptance.verify_single_created_event(
            path,
            "pay_existing",
            "phase-g-acceptance/test",
        )


def test_enabled_webhook_preflight_count(tmp_path):
    path = tmp_path / "payments.sqlite3"
    create_acceptance_db(path, with_reference_schema=True)

    assert acceptance.enabled_webhook_count(path) == 0

    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO webhook_endpoints (endpoint_id, enabled) VALUES (?, ?)",
            [
                ("wh_disabled", 0),
                ("wh_enabled", 1),
            ],
        )

    assert acceptance.enabled_webhook_count(path) == 1


def test_truthy_is_strict():
    assert acceptance.truthy("true") is True
    assert acceptance.truthy("ON") is True
    assert acceptance.truthy("0") is False
    assert acceptance.truthy(None) is False
