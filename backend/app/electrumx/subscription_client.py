import asyncio
import json
import ssl
from collections.abc import Awaitable, Callable
from typing import Any

from ..config import Settings
from .client import DEFAULT_STREAM_LIMIT_BYTES
from .errors import (
    ElectrumXConnectionError,
    ElectrumXMethodError,
    ElectrumXProtocolError,
    ElectrumXTimeoutError,
)

NotificationHandler = Callable[[str, list[Any]], Awaitable[None]]


class ElectrumXSubscriptionClient:
    """Persistent newline-delimited ElectrumX client with response correlation.

    ElectrumX requires server.version to be the first RPC on a new client session.
    A single reader task owns the socket read side and dispatches responses or
    subscription notifications.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        notification_handler: NotificationHandler | None = None,
    ) -> None:
        self.settings = settings
        self.notification_handler = notification_handler
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._request_id = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._write_lock = asyncio.Lock()
        self._closed_event = asyncio.Event()
        self._closing = False

    @property
    def connected(self) -> bool:
        return (
            self._writer is not None
            and not self._writer.is_closing()
            and self._reader_task is not None
            and not self._reader_task.done()
        )

    async def connect(self) -> None:
        if self.connected:
            return

        await self.close()
        self._closing = False
        self._closed_event = asyncio.Event()

        ssl_context = ssl.create_default_context() if self.settings.electrumx_use_ssl else None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    self.settings.electrumx_host,
                    self.settings.electrumx_port,
                    ssl=ssl_context,
                    limit=DEFAULT_STREAM_LIMIT_BYTES,
                ),
                timeout=self.settings.electrumx_timeout,
            )
        except asyncio.TimeoutError as exc:
            raise ElectrumXTimeoutError("electrumx_timeout") from exc
        except OSError as exc:
            raise ElectrumXConnectionError("electrumx_unavailable") from exc

        self._reader = reader
        self._writer = writer
        self._reader_task = asyncio.create_task(self._reader_loop(), name="electrumx-subscription-reader")

        try:
            # Server version negotiation must be the first message on this TCP session.
            await self._request_connected("server.version", ["pepew-payment-watcher", "1.4"])
        except Exception:
            await self.close()
            raise

    async def request(self, method: str, params: list[Any] | None = None) -> Any:
        if not self.connected:
            raise ElectrumXConnectionError("electrumx_unavailable")
        return await self._request_connected(method, params or [])

    async def subscribe_headers(self) -> Any:
        return await self.request("blockchain.headers.subscribe")

    async def subscribe_scripthash(self, scripthash: str) -> Any:
        return await self.request("blockchain.scripthash.subscribe", [scripthash])

    async def wait_disconnected(self) -> None:
        await self._closed_event.wait()

    async def close(self) -> None:
        self._closing = True
        reader_task = self._reader_task
        self._reader_task = None

        if reader_task is not None and reader_task is not asyncio.current_task():
            reader_task.cancel()
            try:
                await reader_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        writer = self._writer
        self._reader = None
        self._writer = None

        error = ElectrumXConnectionError("electrumx_unavailable")
        self._fail_pending(error)

        if writer is not None:
            writer.close()
            try:
                await asyncio.wait_for(
                    writer.wait_closed(),
                    timeout=self.settings.electrumx_timeout,
                )
            except Exception:
                pass

        self._closed_event.set()

    async def _request_connected(self, method: str, params: list[Any]) -> Any:
        writer = self._writer
        if writer is None or writer.is_closing():
            raise ElectrumXConnectionError("electrumx_unavailable")

        self._request_id += 1
        request_id = self._request_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[request_id] = future

        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        message = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"

        try:
            async with self._write_lock:
                writer = self._writer
                if writer is None or writer.is_closing():
                    raise ElectrumXConnectionError("electrumx_unavailable")
                writer.write(message)
                await asyncio.wait_for(
                    writer.drain(),
                    timeout=self.settings.electrumx_timeout,
                )

            return await asyncio.wait_for(
                asyncio.shield(future),
                timeout=self.settings.electrumx_timeout,
            )
        except asyncio.TimeoutError as exc:
            raise ElectrumXTimeoutError("electrumx_timeout") from exc
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()

    async def _reader_loop(self) -> None:
        error: Exception | None = None
        try:
            while True:
                reader = self._reader
                if reader is None:
                    raise ElectrumXConnectionError("electrumx_unavailable")
                try:
                    line = await reader.readline()
                except ValueError as exc:
                    raise ElectrumXProtocolError("electrumx_response_too_large") from exc
                if not line:
                    raise ElectrumXConnectionError("electrumx_connection_closed")

                try:
                    data = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ElectrumXProtocolError("electrumx_invalid_json") from exc
                if not isinstance(data, dict):
                    raise ElectrumXProtocolError("electrumx_invalid_response")

                response_id = data.get("id")
                if response_id is not None:
                    try:
                        request_id = int(response_id)
                    except (TypeError, ValueError):
                        continue
                    future = self._pending.get(request_id)
                    if future is None or future.done():
                        continue
                    if data.get("error") is not None:
                        future.set_exception(
                            ElectrumXMethodError(
                                "electrumx_method_error",
                                data=data.get("error"),
                            )
                        )
                    elif "result" not in data:
                        future.set_exception(
                            ElectrumXProtocolError("electrumx_missing_result")
                        )
                    else:
                        future.set_result(data["result"])
                    continue

                method = data.get("method")
                params = data.get("params")
                if not isinstance(method, str) or not isinstance(params, list):
                    continue
                if self.notification_handler is not None:
                    await self.notification_handler(method, params)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = exc
        finally:
            if error is None and not self._closing:
                error = ElectrumXConnectionError("electrumx_connection_closed")
            if error is not None:
                self._fail_pending(error)
            self._closed_event.set()

    def _fail_pending(self, error: Exception) -> None:
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(error)
