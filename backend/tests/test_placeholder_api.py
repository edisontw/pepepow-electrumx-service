from fastapi.testclient import TestClient

from app.api import tx as tx_api
from app.main import app


def test_tx_lookup_returns_success(monkeypatch):
    async def fake_get_transaction_details(txid):
        return {
            "ok": True,
            "txid": txid,
            "data": {"hex": "01000000...", "confirmations": 10}
        }

    monkeypatch.setattr(tx_api, "get_transaction_details", fake_get_transaction_details)

    client = TestClient(app)
    response = client.get(f"/api/tx/{'a' * 64}")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["txid"] == 'a' * 64
    assert data["data"]["hex"] == "01000000..."


