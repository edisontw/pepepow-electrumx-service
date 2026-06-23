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

DEFAULT_STREAM_LIMIT_BYTES = 16 * 1024 * 1024


class ElectrumXClient:
    """Small newline-delimited ElectrumX JSON-RPC TCP client."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._request_id = 0
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

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
            await self.close()
            raise ElectrumXTimeoutError("electrumx_timeout") from exc
        except ValueError as exc:
            # StreamReader.readline can raise ValueError when one JSON line exceeds the configured limit.
            await self.close()
            raise ElectrumXProtocolError("electrumx_response_too_large") from exc

    async def call(self, method: str, params: list[Any] | None = None) -> Any:
        """Backward-compatible alias for request()."""
        return await self.request(method, params)

    async def close(self) -> None:
        writer = self._writer
        self._reader = None
        self._writer = None
        if writer is None:
            return
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=self.settings.electrumx_timeout)
        except Exception:
            pass

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if self._reader is not None and self._writer is not None and not self._writer.is_closing():
            return self._reader, self._writer

        timeout = self.settings.electrumx_timeout
        ssl_context = ssl.create_default_context() if self.settings.electrumx_use_ssl else None
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(
                    self.settings.electrumx_host,
                    self.settings.electrumx_port,
                    ssl=ssl_context,
                    limit=DEFAULT_STREAM_LIMIT_BYTES,
                ),
                timeout=timeout,
            )
            return self._reader, self._writer
        except asyncio.TimeoutError as exc:
            raise ElectrumXTimeoutError("electrumx_timeout") from exc
        except OSError as exc:
            raise ElectrumXConnectionError("electrumx_unavailable") from exc

    async def _request_once(self, payload: dict[str, Any]) -> Any:
        timeout = self.settings.electrumx_timeout
        reader, writer = await self._connect()

        message = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
        writer.write(message)
        await asyncio.wait_for(writer.drain(), timeout=timeout)

        while True:
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=timeout)
            except ValueError as exc:
                await self.close()
                raise ElectrumXProtocolError("electrumx_response_too_large") from exc

            if not line:
                await self.close()
                raise ElectrumXProtocolError("electrumx_empty_response")

            try:
                data = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                await self.close()
                raise ElectrumXProtocolError("electrumx_invalid_json") from exc

            if not isinstance(data, dict):
                raise ElectrumXProtocolError("electrumx_invalid_response")

            response_id = data.get("id")
            notification_method = data.get("method")
            notification_params = data.get("params")

            if response_id is None:
                if notification_method == payload["method"] and isinstance(notification_params, list):
                    if len(notification_params) == 1:
                        return notification_params[0]
                    return notification_params
                continue

            if str(response_id) != str(payload["id"]):
                continue

            if data.get("error") is not None:
                raise ElectrumXMethodError("electrumx_method_error", data=data.get("error"))

            if "result" not in data:
                raise ElectrumXProtocolError("electrumx_missing_result")

            return data["result"]
