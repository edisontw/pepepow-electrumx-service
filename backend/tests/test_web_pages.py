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
    assert "PAGE_LIMIT = 20" in response.text
    assert "Prev" in response.text
    assert "Next" in response.text


def test_address_head_returns_ok():
    client = TestClient(app)
    response = client.head("/address")

    assert response.status_code == 200
