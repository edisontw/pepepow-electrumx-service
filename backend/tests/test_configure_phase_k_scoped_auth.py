import importlib.util
from pathlib import Path
import stat

import pytest

from app.services.merchant_store import MerchantStore
from app.services.payment_store import PaymentStore


ROOT = Path(__file__).resolve().parents[1]


def load_script():
    path = ROOT / "scripts" / "configure_phase_k_scoped_auth.py"
    spec = importlib.util.spec_from_file_location(
        "configure_phase_k_scoped_auth",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


config = load_script()


def write_env(path: Path, db_path: Path) -> None:
    path.write_text(
        "PAYMENT_DB_PATH=" + str(db_path) + "\n"
        + "PAYMENT_CREATE_API_KEY=" + ("x" * 48) + "\n"
        + "PAYMENT_SCOPED_MERCHANT_AUTH_ENABLED=false\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def test_enable_requires_active_database_credential(tmp_path):
    db = tmp_path / "payments.sqlite3"
    PaymentStore(str(db)).initialize()
    env = tmp_path / ".env"
    write_env(env, db)

    with pytest.raises(
        config.ScopedAuthConfigError,
        match="no active database-backed merchant credential",
    ):
        config.configure(env, enabled=True)

    assert "PAYMENT_SCOPED_MERCHANT_AUTH_ENABLED=false" in env.read_text(
        encoding="utf-8"
    )


def test_enable_preserves_legacy_key_and_writes_mode_0600(tmp_path):
    db = tmp_path / "payments.sqlite3"
    PaymentStore(str(db)).initialize()
    merchants = MerchantStore(str(db))
    merchant = merchants.create_merchant(
        merchant_id="mrc_config_test",
        display_name="Config merchant",
        now=100,
    )
    metadata, _token = merchants.create_credential(
        merchant_id=merchant["merchant_id"],
        label="primary",
        now=101,
    )
    env = tmp_path / ".env"
    write_env(env, db)

    config.configure(env, enabled=True)

    content = env.read_text(encoding="utf-8")
    assert "PAYMENT_SCOPED_MERCHANT_AUTH_ENABLED=true" in content
    assert "PAYMENT_CREATE_API_KEY=" + ("x" * 48) in content
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert merchants.list_credentials(merchant["merchant_id"])[0][
        "credential_id"
    ] == metadata["credential_id"]


def test_disable_does_not_require_active_database_credential(tmp_path):
    db = tmp_path / "payments.sqlite3"
    PaymentStore(str(db)).initialize()
    env = tmp_path / ".env"
    write_env(env, db)
    env.write_text(
        env.read_text(encoding="utf-8").replace(
            "PAYMENT_SCOPED_MERCHANT_AUTH_ENABLED=false",
            "PAYMENT_SCOPED_MERCHANT_AUTH_ENABLED=true",
        ),
        encoding="utf-8",
    )

    config.configure(env, enabled=False)

    assert "PAYMENT_SCOPED_MERCHANT_AUTH_ENABLED=false" in env.read_text(
        encoding="utf-8"
    )
