import asyncio
import json
import ssl
from typing import Any

from ..config import Settings
from .errors import ElectrumXProtocolError, ElectrumXTimeoutError, ElectrumXUnavailableError


class ElectrumXClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._request_id = 0

    async def call(self, method: str, params: list[Any] | None = None) -> Any:
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params or [],
        }

        try:
            return await asyncio.wait_for(self._call_once(payload), timeout=self.settings.electrumx_timeout)
        except asyncio.TimeoutError as exc:
            raise ElectrumXTimeoutError("ElectrumX request timed out.") from exc

    async def _call_once(self, payload: dict[str, Any]) -> Any:
        ssl_context = ssl.create_default_context() if self.settings.electrumx_use_ssl else None

        try:
            reader, writer = await asyncio.open_connection(
                self.settings.electrumx_host,
                self.settings.electrumx_port,
                ssl=ssl_context,
            )
        except OSError as exc:
            raise ElectrumXUnavailableError("ElectrumX server is unavailable.") from exc

        try:
            writer.write(json.dumps(payload).encode("utf-8") + b"\n")
            await writer.drain()
            line = await reader.readline()
        finally:
            writer.close()
            await writer.wait_closed()

        if not line:
            raise ElectrumXProtocolError("ElectrumX returned an empty response.")

        try:
            data = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ElectrumXProtocolError("ElectrumX returned invalid JSON.") from exc

        if data.get("error"):
            raise ElectrumXProtocolError("ElectrumX returned a method error.")

        return data.get("result")
