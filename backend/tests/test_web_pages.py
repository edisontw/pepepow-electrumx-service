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


def test_address_page_renders_lookup_shell():
    client = TestClient(app)
    response = client.get(f"/address?q={KNOWN_ADDRESS}")

    assert response.status_code == 200
    assert "PEPEW Address Lookup" in response.text
    assert KNOWN_ADDRESS in response.text
    assert "/api/address/" in response.text
    assert "https://explorer.pepepow.net/tx/" in response.text
    assert 'link.rel = "noopener noreferrer"' in response.text
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
    assert "Public Service" in response.text
    assert "https://light.pepepow.net" in response.text
    assert "Gateway" in response.text
    assert "online" in response.text
    assert "connected" in response.text
    assert "123" in response.text
    assert "12.34 ms" in response.text
    assert "0s / 10s" in response.text


def test_address_head_returns_ok():
    client = TestClient(app)
    response = client.head("/address")

    assert response.status_code == 200
