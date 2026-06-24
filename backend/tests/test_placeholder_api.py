from fastapi.testclient import TestClient

from app.main import app


def test_tx_placeholder_returns_standard_error():
    client = TestClient(app)
    response = client.get(f"/api/tx/{'a' * 64}")

    assert response.status_code == 501
    assert response.json() == {
        "ok": False,
        "error": {
            "code": "transaction_lookup_unavailable",
            "message": "Transaction lookup is not available yet.",
        },
    }


def test_payment_check_placeholder_returns_standard_error():
    client = TestClient(app)
    response = client.get("/api/payment/check?address=invalid&amount=1")

    assert response.status_code == 501
    assert response.json() == {
        "ok": False,
        "error": {
            "code": "payment_check_unavailable",
            "message": "Payment checking is not available yet.",
        },
    }
