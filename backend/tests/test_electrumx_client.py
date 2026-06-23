import asyncio
import json

import pytest

from app.config import get_settings
from app.electrumx.client import ElectrumXClient
from app.electrumx.errors import (
    ElectrumXConnectionError,
    ElectrumXMethodError,
    ElectrumXProtocolError,
    ElectrumXTimeoutError,
)


def _settings_for(host: str, port: int, timeout: float = 1.0):
    settings = get_settings()
    settings.electrumx_host = host
    settings.electrumx_port = port
    settings.electrumx_timeout = timeout
    settings.electrumx_use_ssl = False
    return settings


def test_electrumx_client_success():
    async def run():
        async def handle(reader, writer):
            line = await reader.readline()
            payload = json.loads(line.decode("utf-8"))
            response = {"jsonrpc": "2.0", "id": payload["id"], "result": ["ElectrumX", "1.4"]}
            writer.write(json.dumps(response).encode("utf-8") + b"\n")
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            client = ElectrumXClient(_settings_for("127.0.0.1", port))
            result = await client.request("server.version", ["pepew-light", "1.4"])
            assert result == ["ElectrumX", "1.4"]
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(run())


def test_electrumx_client_method_error():
    async def run():
        async def handle(reader, writer):
            line = await reader.readline()
            payload = json.loads(line.decode("utf-8"))
            response = {"jsonrpc": "2.0", "id": payload["id"], "error": {"code": -1, "message": "bad"}}
            writer.write(json.dumps(response).encode("utf-8") + b"\n")
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            client = ElectrumXClient(_settings_for("127.0.0.1", port))
            with pytest.raises(ElectrumXMethodError):
                await client.request("server.version")
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(run())


def test_electrumx_client_invalid_json():
    async def run():
        async def handle(reader, writer):
            await reader.readline()
            writer.write(b"not-json\n")
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            client = ElectrumXClient(_settings_for("127.0.0.1", port))
            with pytest.raises(ElectrumXProtocolError):
                await client.request("server.version")
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(run())


def test_electrumx_client_timeout():
    async def run():
        async def handle(reader, writer):
            await reader.readline()
            await asyncio.sleep(0.2)
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            client = ElectrumXClient(_settings_for("127.0.0.1", port, timeout=0.05))
            with pytest.raises(ElectrumXTimeoutError):
                await client.request("server.version")
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(run())


def test_electrumx_client_unavailable():
    async def run():
        server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        server.close()
        await server.wait_closed()

        client = ElectrumXClient(_settings_for("127.0.0.1", port, timeout=0.1))
        with pytest.raises((ElectrumXConnectionError, ElectrumXTimeoutError)):
            await client.request("server.version")

    asyncio.run(run())
