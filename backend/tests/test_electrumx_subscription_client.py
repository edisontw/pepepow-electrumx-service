import asyncio
import json

from app.config import get_settings
from app.electrumx.subscription_client import ElectrumXSubscriptionClient


def _settings_for(port: int):
    return get_settings().model_copy(
        update={
            "electrumx_host": "127.0.0.1",
            "electrumx_port": port,
            "electrumx_timeout": 1.0,
            "electrumx_use_ssl": False,
        }
    )


def test_subscription_client_negotiates_first_and_dispatches_notifications():
    async def run():
        received_methods = []
        notifications = []

        async def handle(reader, writer):
            first = json.loads((await reader.readline()).decode())
            received_methods.append(first["method"])
            writer.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": first["id"],
                        "result": ["ElectrumX 1.19.0", "1.4"],
                    }
                ).encode()
                + b"\n"
            )
            await writer.drain()

            header_sub = json.loads((await reader.readline()).decode())
            received_methods.append(header_sub["method"])
            writer.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": header_sub["id"],
                        "result": {"height": 500, "hex": "00"},
                    }
                ).encode()
                + b"\n"
            )
            await writer.drain()

            script_sub = json.loads((await reader.readline()).decode())
            received_methods.append(script_sub["method"])
            scripthash = script_sub["params"][0]
            writer.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": script_sub["id"],
                        "result": None,
                    }
                ).encode()
                + b"\n"
            )
            writer.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "blockchain.headers.subscribe",
                        "params": [{"height": 501, "hex": "11"}],
                    }
                ).encode()
                + b"\n"
            )
            writer.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "blockchain.scripthash.subscribe",
                        "params": [scripthash, "status-hash"],
                    }
                ).encode()
                + b"\n"
            )
            await writer.drain()
            await asyncio.sleep(0.05)
            writer.close()
            await writer.wait_closed()

        async def on_notification(method, params):
            notifications.append((method, params))

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        client = ElectrumXSubscriptionClient(
            _settings_for(port),
            notification_handler=on_notification,
        )
        try:
            await client.connect()
            header = await client.subscribe_headers()
            assert header["height"] == 500
            result = await client.subscribe_scripthash("11" * 32)
            assert result is None
            await client.wait_disconnected()
        finally:
            await client.close()
            server.close()
            await server.wait_closed()

        assert received_methods[0] == "server.version"
        assert received_methods[1:] == [
            "blockchain.headers.subscribe",
            "blockchain.scripthash.subscribe",
        ]
        assert notifications == [
            ("blockchain.headers.subscribe", [{"height": 501, "hex": "11"}]),
            (
                "blockchain.scripthash.subscribe",
                ["11" * 32, "status-hash"],
            ),
        ]

    asyncio.run(run())


def test_subscription_client_correlates_out_of_order_responses():
    async def run():
        async def handle(reader, writer):
            first = json.loads((await reader.readline()).decode())
            writer.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": first["id"],
                        "result": ["ElectrumX", "1.4"],
                    }
                ).encode()
                + b"\n"
            )
            await writer.drain()

            req_a = json.loads((await reader.readline()).decode())
            req_b = json.loads((await reader.readline()).decode())
            writer.write(
                json.dumps(
                    {"jsonrpc": "2.0", "id": req_b["id"], "result": "B"}
                ).encode()
                + b"\n"
            )
            writer.write(
                json.dumps(
                    {"jsonrpc": "2.0", "id": req_a["id"], "result": "A"}
                ).encode()
                + b"\n"
            )
            await writer.drain()
            await asyncio.sleep(0.05)

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        client = ElectrumXSubscriptionClient(_settings_for(port))
        try:
            await client.connect()
            a, b = await asyncio.gather(
                client.request("example.a"),
                client.request("example.b"),
            )
            assert (a, b) == ("A", "B")
        finally:
            await client.close()
            server.close()
            await server.wait_closed()

    asyncio.run(run())
