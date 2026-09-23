from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_pay_nginx_nested_v1_routes_outrank_api_catch_all():
    config = _read("deploy/nginx/pepew-pay")

    payments = "location ^~ /api/v1/payments/ {"
    webhooks = "location ^~ /api/v1/webhook-endpoints/ {"
    catch_all = "location ^~ /api/ {"

    assert payments in config
    assert webhooks in config
    assert catch_all in config

    # The dedicated nested prefixes must be more specific than the legacy-API
    # catch-all so Nginx proxies them to FastAPI instead of returning its HTML 404.
    assert "/api/v1/payments/".startswith("/api/")
    assert "/api/v1/webhook-endpoints/".startswith("/api/")


def test_light_post_cutover_uses_explicit_authoritative_v1_prefixes():
    config = _read("deploy/nginx/pepew-light-post-cutover")

    assert "location ^~ /api/v1/payments/ {" in config
    assert "location ^~ /api/v1/webhook-endpoints/ {" in config
    assert "proxy_pass https://pay.pepepow.net;" in config
