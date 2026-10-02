from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
import secrets
import sqlite3
from typing import Any


LEGACY_MERCHANT_ID = "mrc_legacy_v1"
SCOPED_CREDENTIAL_PREFIX = "pepew_live"
MERCHANT_ID_PREFIX = "mrc_"
CREDENTIAL_ID_PREFIX = "mck_"


class MerchantStoreError(RuntimeError):
    pass


class MerchantNotFoundError(MerchantStoreError):
    pass


class MerchantCredentialNotFoundError(MerchantStoreError):
    pass


def _new_identifier(prefix: str, random_bytes: int = 12) -> str:
    return prefix + secrets.token_urlsafe(random_bytes)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _build_token(credential_id: str) -> str:
    secret = secrets.token_urlsafe(32)
    return f"{SCOPED_CREDENTIAL_PREFIX}.{credential_id}.{secret}"


def _credential_id_from_token(token: str) -> str | None:
    parts = token.split(".", 2)
    if len(parts) != 3 or parts[0] != SCOPED_CREDENTIAL_PREFIX:
        return None
    credential_id = parts[1]
    secret = parts[2]
    if not credential_id.startswith(CREDENTIAL_ID_PREFIX) or len(secret) < 32:
        return None
    return credential_id


class MerchantStore:
    def __init__(self, path: str) -> None:
        self.path = str(path)

    def _connect(self) -> sqlite3.Connection:
        if self.path != ":memory:":
            Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS merchants (
                    merchant_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS merchant_credentials (
                    credential_id TEXT PRIMARY KEY,
                    merchant_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    label TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                    created_at INTEGER NOT NULL,
                    disabled_at INTEGER,
                    FOREIGN KEY (merchant_id) REFERENCES merchants(merchant_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_merchant_credentials_owner_enabled
                ON merchant_credentials (merchant_id, enabled, created_at DESC);
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO merchants (
                    merchant_id, display_name, enabled, created_at, updated_at
                ) VALUES (?, ?, 1, 0, 0)
                """,
                (LEGACY_MERCHANT_ID, "Legacy production merchant"),
            )

    def get_merchant(self, merchant_id: str) -> dict[str, Any]:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT merchant_id, display_name, enabled, created_at, updated_at
                FROM merchants
                WHERE merchant_id = ?
                """,
                (merchant_id,),
            ).fetchone()
        if row is None:
            raise MerchantNotFoundError(merchant_id)
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        return item

    def create_merchant(
        self,
        *,
        display_name: str,
        now: int,
        merchant_id: str | None = None,
    ) -> dict[str, Any]:
        self.initialize()
        name = display_name.strip()
        if not name or len(name) > 128:
            raise MerchantStoreError("merchant display name must be 1-128 characters")
        merchant_id = merchant_id or _new_identifier(MERCHANT_ID_PREFIX)
        if not merchant_id.startswith(MERCHANT_ID_PREFIX) or len(merchant_id) > 96:
            raise MerchantStoreError("merchant identifier is invalid")

        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO merchants (
                        merchant_id, display_name, enabled, created_at, updated_at
                    ) VALUES (?, ?, 1, ?, ?)
                    """,
                    (merchant_id, name, int(now), int(now)),
                )
        except sqlite3.IntegrityError as exc:
            raise MerchantStoreError("merchant identifier already exists") from exc
        return self.get_merchant(merchant_id)

    def create_credential(
        self,
        *,
        merchant_id: str,
        now: int,
        label: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        self.initialize()
        merchant = self.get_merchant(merchant_id)
        if not merchant["enabled"]:
            raise MerchantStoreError("merchant is disabled")

        normalized_label = None if label is None else label.strip()
        if normalized_label == "":
            normalized_label = None
        if normalized_label is not None and len(normalized_label) > 128:
            raise MerchantStoreError("credential label must be at most 128 characters")

        credential_id = _new_identifier(CREDENTIAL_ID_PREFIX, random_bytes=9)
        token = _build_token(credential_id)
        token_hash = _token_hash(token)

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO merchant_credentials (
                    credential_id, merchant_id, token_hash, label,
                    enabled, created_at, disabled_at
                ) VALUES (?, ?, ?, ?, 1, ?, NULL)
                """,
                (
                    credential_id,
                    merchant_id,
                    token_hash,
                    normalized_label,
                    int(now),
                ),
            )

        metadata = {
            "credential_id": credential_id,
            "merchant_id": merchant_id,
            "label": normalized_label,
            "enabled": True,
            "created_at": int(now),
            "disabled_at": None,
        }
        return metadata, token

    def authenticate_credential(self, token: str) -> dict[str, str] | None:
        self.initialize()
        credential_id = _credential_id_from_token(token)
        if credential_id is None:
            return None

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    c.credential_id,
                    c.merchant_id,
                    c.token_hash,
                    c.enabled AS credential_enabled,
                    m.enabled AS merchant_enabled
                FROM merchant_credentials AS c
                JOIN merchants AS m ON m.merchant_id = c.merchant_id
                WHERE c.credential_id = ?
                """,
                (credential_id,),
            ).fetchone()

        if row is None:
            return None
        if not bool(row["credential_enabled"]) or not bool(row["merchant_enabled"]):
            return None

        presented_hash = _token_hash(token)
        if not hmac.compare_digest(presented_hash, str(row["token_hash"])):
            return None
        return {
            "merchant_id": str(row["merchant_id"]),
            "credential_id": str(row["credential_id"]),
        }

    def list_credentials(self, merchant_id: str) -> list[dict[str, Any]]:
        self.initialize()
        self.get_merchant(merchant_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT credential_id, merchant_id, label, enabled, created_at, disabled_at
                FROM merchant_credentials
                WHERE merchant_id = ?
                ORDER BY created_at ASC, credential_id ASC
                """,
                (merchant_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["enabled"] = bool(item["enabled"])
            result.append(item)
        return result

    def disable_credential(
        self,
        credential_id: str,
        *,
        now: int,
    ) -> None:
        self.initialize()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE merchant_credentials
                SET enabled = 0, disabled_at = ?
                WHERE credential_id = ? AND enabled = 1
                """,
                (int(now), credential_id),
            )
        if cursor.rowcount == 0:
            raise MerchantCredentialNotFoundError(credential_id)

    def has_active_credentials(self) -> bool:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM merchant_credentials AS c
                JOIN merchants AS m ON m.merchant_id = c.merchant_id
                WHERE c.enabled = 1 AND m.enabled = 1
                LIMIT 1
                """
            ).fetchone()
        return row is not None
