import importlib.util
from pathlib import Path
import stat

from app.services.merchant_store import MerchantStore


ROOT = Path(__file__).resolve().parents[1]


def load_script():
    path = ROOT / "scripts" / "merchant_credential_admin.py"
    spec = importlib.util.spec_from_file_location("merchant_credential_admin", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


admin = load_script()


def test_secret_file_is_created_once_with_mode_0600(tmp_path):
    output = tmp_path / "merchant.secret"
    admin.write_secret_file(output, "pepew_live.test.secret")

    assert output.read_text(encoding="utf-8") == "pepew_live.test.secret\n"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600

    try:
        admin.write_secret_file(output, "replacement")
    except FileExistsError:
        pass
    else:
        raise AssertionError("secret output must never overwrite an existing file")

    assert output.read_text(encoding="utf-8") == "pepew_live.test.secret\n"


def test_read_env_uses_payment_db_path_without_exposing_other_values(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "PAYMENT_DB_PATH=/tmp/payments.sqlite3\n"
        "PAYMENT_CREATE_API_KEY=do-not-print\n",
        encoding="utf-8",
    )

    values = admin.read_env(env)

    assert values["PAYMENT_DB_PATH"] == "/tmp/payments.sqlite3"
    assert values["PAYMENT_CREATE_API_KEY"] == "do-not-print"



def test_create_credential_to_file_writes_once_and_authenticates(tmp_path):
    store = MerchantStore(str(tmp_path / "payments.sqlite3"))
    merchant = store.create_merchant(
        merchant_id="mrc_admin_ok",
        display_name="Admin merchant",
        now=100,
    )
    secret_file = tmp_path / "merchant.secret"

    metadata = admin.create_credential_to_file(
        store,
        merchant_id=merchant["merchant_id"],
        label="primary",
        secret_file=secret_file,
        now=101,
    )

    token = secret_file.read_text(encoding="utf-8").strip()
    assert token.startswith("pepew_live.")
    assert stat.S_IMODE(secret_file.stat().st_mode) == 0o600
    assert store.authenticate_credential(token) == {
        "merchant_id": merchant["merchant_id"],
        "credential_id": metadata["credential_id"],
    }


def test_create_credential_to_file_revokes_if_secret_delivery_fails(tmp_path):
    store = MerchantStore(str(tmp_path / "payments.sqlite3"))
    merchant = store.create_merchant(
        merchant_id="mrc_admin_fail",
        display_name="Admin merchant",
        now=100,
    )
    secret_file = tmp_path / "merchant.secret"
    secret_file.write_text("already-present\n", encoding="utf-8")

    try:
        admin.create_credential_to_file(
            store,
            merchant_id=merchant["merchant_id"],
            label="primary",
            secret_file=secret_file,
            now=101,
        )
    except FileExistsError:
        pass
    else:
        raise AssertionError("secret delivery failure must fail credential creation")

    credentials = store.list_credentials(merchant["merchant_id"])
    assert len(credentials) == 1
    assert credentials[0]["enabled"] is False
    assert credentials[0]["disabled_at"] == 101
    assert secret_file.read_text(encoding="utf-8") == "already-present\n"
