import asyncio

from app.services import webhook_http
from app.services.webhook_security import ResolvedWebhookTarget


class FakeReader:
    def __init__(self):
        self.lines = [
            b"HTTP/1.1 204 No Content\r\n",
            b"Content-Length: 0\r\n",
            b"\r\n",
        ]

    async def readline(self):
        return self.lines.pop(0) if self.lines else b""


class FakeWriter:
    def __init__(self):
        self.payload = b""
        self.closed = False

    def write(self, data):
        self.payload += data

    async def drain(self):
        return None

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


def test_https_sender_connects_to_validated_ip_but_preserves_sni_and_host(monkeypatch):
    async def run():
        calls = []
        writer = FakeWriter()

        async def fake_open_connection(host, port, **kwargs):
            calls.append((host, port, kwargs))
            return FakeReader(), writer

        monkeypatch.setattr(webhook_http.asyncio, "open_connection", fake_open_connection)

        target = ResolvedWebhookTarget(
            hostname="merchant.example",
            port=443,
            path_and_query="/hooks/pepew?x=1",
            host_header="merchant.example",
            addresses=("93.184.216.34",),
        )

        status = await webhook_http.send_webhook_https(
            target,
            body=b'{"ok":true}',
            headers={"X-PepewPay-Event-Id": "evt_test"},
            timeout_seconds=1,
        )

        assert status == 204
        assert calls[0][0] == "93.184.216.34"
        assert calls[0][1] == 443
        assert calls[0][2]["server_hostname"] == "merchant.example"
        assert b"POST /hooks/pepew?x=1 HTTP/1.1\r\n" in writer.payload
        assert b"Host: merchant.example\r\n" in writer.payload
        assert b"X-PepewPay-Event-Id: evt_test\r\n" in writer.payload

    asyncio.run(run())
