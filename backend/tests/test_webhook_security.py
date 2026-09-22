import asyncio
import socket

import pytest

from app.services.webhook_security import (
    WebhookUrlError,
    resolve_webhook_target,
)


def _resolver(*addresses):
    async def resolve(_host, port, **_kwargs):
        rows = []
        for address in addresses:
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            sockaddr = (address, port, 0, 0) if family == socket.AF_INET6 else (address, port)
            rows.append((family, socket.SOCK_STREAM, 6, "", sockaddr))
        return rows
    return resolve


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/hook",
        "https://user:pass@example.com/hook",
        "https://example.com:8443/hook",
        "https://example.com/hook#fragment",
        "https://localhost/hook",
        "https://127.0.0.1/hook",
        "https://169.254.169.254/latest/meta-data/",
        "https://10.0.0.1/hook",
        "https://[::1]/hook",
    ],
)
def test_webhook_url_rejects_unsafe_static_targets(url):
    async def run():
        with pytest.raises(WebhookUrlError):
            await resolve_webhook_target(url, resolver=_resolver("93.184.216.34"))
    asyncio.run(run())


def test_webhook_url_rejects_private_dns_resolution():
    async def run():
        with pytest.raises(WebhookUrlError) as exc:
            await resolve_webhook_target(
                "https://merchant.example/hook",
                resolver=_resolver("10.0.0.5"),
            )
        assert exc.value.code == "unsafe_webhook_target"
    asyncio.run(run())


def test_webhook_url_rejects_mixed_public_private_dns_resolution():
    async def run():
        with pytest.raises(WebhookUrlError):
            await resolve_webhook_target(
                "https://merchant.example/hook",
                resolver=_resolver("93.184.216.34", "192.168.1.4"),
            )
    asyncio.run(run())


def test_webhook_url_accepts_public_dns_and_preserves_path_query():
    async def run():
        target = await resolve_webhook_target(
            "https://merchant.example/hooks/pepew?source=pay",
            resolver=_resolver("93.184.216.34"),
        )
        assert target.hostname == "merchant.example"
        assert target.port == 443
        assert target.addresses == ("93.184.216.34",)
        assert target.path_and_query == "/hooks/pepew?source=pay"
        assert target.host_header == "merchant.example"
    asyncio.run(run())
