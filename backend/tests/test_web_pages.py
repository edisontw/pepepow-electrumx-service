from fastapi.testclient import TestClient

from app.main import app

KNOWN_ADDRESS = "PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb"


def test_homepage_has_address_lookup_form():
    client = TestClient(app)
    response = client.get("/")

    assert response.status_code == 200
    assert "Address Lookup" in response.text
    assert 'action="/address"' in response.text
    assert 'name="q"' in response.text


def test_homepage_contains_documentation_and_safety_details():
    client = TestClient(app)
    response = client.get("/")

    assert response.status_code == 200
    # Safety text assertions
    assert "Gateway Safety Boundary" in response.text
    assert "Read-Only" in response.text
    assert "No Custody" in response.text
    
    # Endpoint documentation assertions
    assert "/api/health" in response.text
    assert "/api/status" in response.text
    assert "/api/address/{address}" in response.text
    assert "/api/address/{address}/history" in response.text
    assert "/api/tx/{txid}" in response.text
    assert "/api/payment/check" in response.text
    assert "/api/wallet/address/{address}" in response.text
    
    # Key links assertions
    assert "PEPEPOW Ecosystem" in response.text
    assert "Block Explorer" in response.text
    assert "Mining Pool" in response.text
    assert "GitHub Codebase" in response.text


def test_address_page_renders_lookup_shell():
    client = TestClient(app)
    response = client.get(f"/address?q={KNOWN_ADDRESS}")

    assert response.status_code == 200
    assert "PEPEW Address Lookup" in response.text
    assert KNOWN_ADDRESS in response.text
    assert "/api/address/" in response.text
    assert "/tx?txid=" in response.text
    assert "PAGE_LIMIT = 20" in response.text
    assert "Prev" in response.text
    assert "Next" in response.text


def test_address_page_contains_clean_invalid_address_messages():
    client = TestClient(app)
    response = client.get("/address?q=invalid")

    assert response.status_code == 200
    assert "Please enter a PEPEPOW address." in response.text
    assert "error.message" in response.text
    assert "Traceback" not in response.text
    assert "InvalidPepewAddressError" not in response.text


def test_status_page_renders_public_summary(monkeypatch):
    async def fake_get_status():
        return {
            "ok": True,
            "electrumx": {
                "connected": True,
                "host": "127.0.0.1",
                "port": 50001,
                "ssl": False,
                "server_version": "ElectrumX 1.19.0",
                "protocol": "1.4",
                "height": 123,
                "tip_hash": None,
                "response_time_ms": 12.34,
            },
            "cache": {"ttl_seconds": 10, "hit": False},
            "checked_at": 1,
            "cache_ttl_seconds": 10,
            "cache_age_seconds": 0,
        }

    monkeypatch.setattr("app.main.get_status", fake_get_status)

    client = TestClient(app)
    response = client.get("/status")

    assert response.status_code == 200
    assert "Gateway" in response.text
    assert "online" in response.text
    assert "connected" in response.text
    assert "123" in response.text
    assert "12.34 ms" in response.text


def test_address_head_returns_ok():
    client = TestClient(app)
    response = client.head("/address")

    assert response.status_code == 200


def test_homepage_head_returns_ok():
    client = TestClient(app)
    response = client.head("/")

    assert response.status_code == 200


def test_pay_page_loads():
    client = TestClient(app)
    response = client.get("/pay")

    assert response.status_code == 200
    assert "PEPEW Payment Monitor" in response.text
    assert "/api/address/" in response.text
    assert "generate a unique, fresh receiving address for each payment" in response.text
    assert "This is not an invoice database" in response.text
    assert "does not create, reserve, store, or reconcile merchant invoices" in response.text
    assert "status-waiting" in response.text
    assert "status-paid-confirmed" in response.text
    assert "status-error" in response.text


def test_pay_page_renders_address_copy_and_qr_ui():
    client = TestClient(app)
    response = client.get("/pay")

    assert response.status_code == 200
    assert 'id="copy-address-button"' in response.text
    assert "Copy address" in response.text
    assert 'id="qr-section"' in response.text
    assert "QR encodes the address only." in response.text
    assert "api.qrserver.com/v1/create-qr-code" in response.text


def test_pay_page_labels_match_aligned_fields():
    client = TestClient(app)
    response = client.get("/pay")

    assert response.status_code == 200
    assert "Confirmed Balance" in response.text
    assert "Mempool Balance" in response.text
    assert "Payment Status" in response.text
    assert "Recent Transactions" in response.text


def test_homepage_transaction_lookup_is_functional():
    client = TestClient(app)
    response = client.get("/")

    assert response.status_code == 200
    assert "Transaction Lookup" in response.text
    assert 'action="/tx"' in response.text
    assert 'name="txid"' in response.text


def test_tx_page_loads():
    client = TestClient(app)
    response = client.get("/tx")

    assert response.status_code == 200
    assert "Transaction Lookup" in response.text
    assert 'id="lookup-form"' in response.text
    assert 'name="txid"' in response.text


def test_root_level_icons_and_manifest():
    client = TestClient(app)

    for path in [
        "/favicon.ico",
        "/favicon.svg",
        "/apple-touch-icon.png",
        "/icon-192.png",
        "/icon-512.png",
    ]:
        response = client.get(path)
        assert response.status_code == 200
        assert len(response.content) > 0

    manifest_response = client.get("/site.webmanifest")
    assert manifest_response.status_code == 200
    assert "application/manifest+json" in manifest_response.headers.get("content-type", "")
    assert b"PEPEW Light" in manifest_response.content
