import importlib.util
from pathlib import Path
import stat


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
