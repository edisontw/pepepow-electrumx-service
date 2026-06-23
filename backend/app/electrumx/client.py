import asyncio
import json
import ssl
from typing import Any

from ..config import Settings
from .errors import (
    ElectrumXConnectionError,
    ElectrumXMethodError,
    ElectrumXProtocolError,
    ElectrumXTimeoutError,
)


class ElectrumXClient:
    """Small newline-delimited ElectrumX JSON-RPC TCP client."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._request_id = 0

    async def request(self, method: str, params: list[Any] | None = None) -> Any:
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params or [],
        }

        try:
            return await asyncio.wait_for(
                self._request_once(payload),
                timeout=self.settings.electrumx_timeout,
            )
        except asyncio.TimeoutError as exc:
            raise ElectrumXTimeoutError("electrumx_timeout") from exc

    async def call(self, method: str, params: list[Any] | None = None) -> Any:
        """Backward-compatible alias for request()."""
        return await self.request(method, params)

    async def _request_once(self, payload: dict[str, Any]) -> Any:
        timeout = self.settings.electrumx_timeout
        ssl_context = ssl.create_default_context() if self.settings.electrumx_use_ssl else None

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    self.settings.electrumx_host,
                    self.settings.electrumx_port,
                    ssl=ssl_context,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise ElectrumXTimeoutError("electrumx_timeout") from exc
        except OSError as exc:
            raise ElectrumXConnectionError("electrumx_unavailable") from exc

        try:
            message = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
            writer.write(message)
            await asyncio.wait_for(writer.drain(), timeout=timeout)
            line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=timeout)
            except Exception:
                pass

        if not line:
            raise ElectrumXProtocolError("electrumx_empty_response")

        try:
            data = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ElectrumXProtocolError("electrumx_invalid_json") from exc

        if not isinstance(data, dict):
            raise ElectrumXProtocolError("electrumx_invalid_response")

        if data.get("id") != payload["id"]:
            raise ElectrumXProtocolError("electrumx_response_id_mismatch")

        if data.get("error") is not None:
            raise ElectrumXMethodError("electrumx_method_error", data=data.get("error"))

        if "result" not in data:
            raise ElectrumXProtocolError("electrumx_missing_result")

        return data["result"]
