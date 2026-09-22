import asyncio
import ssl
from typing import Mapping

from .webhook_security import ResolvedWebhookTarget


class WebhookHttpError(RuntimeError):
    pass


def _safe_header_value(value: str) -> str:
    if "\r" in value or "\n" in value:
        raise WebhookHttpError("Invalid webhook header value.")
    return value


async def send_webhook_https(
    target: ResolvedWebhookTarget,
    *,
    body: bytes,
    headers: Mapping[str, str],
    timeout_seconds: float,
) -> int:
    timeout = max(0.1, float(timeout_seconds))
    ssl_context = ssl.create_default_context()
    last_error: Exception | None = None

    for address in target.addresses:
        writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    address,
                    target.port,
                    ssl=ssl_context,
                    server_hostname=target.hostname,
                    limit=64 * 1024,
                ),
                timeout=timeout,
            )

            request_headers = {
                "Host": target.host_header,
                "User-Agent": "PepewPay-Webhook/1.0",
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "Connection": "close",
                **dict(headers),
            }
            lines = [
                f"POST {target.path_and_query} HTTP/1.1",
                *[
                    f"{name}: {_safe_header_value(str(value))}"
                    for name, value in request_headers.items()
                ],
                "",
                "",
            ]
            payload = "\r\n".join(lines).encode("ascii") + body
            writer.write(payload)
            await asyncio.wait_for(writer.drain(), timeout=timeout)

            status_line = await asyncio.wait_for(reader.readline(), timeout=timeout)
            if not status_line or len(status_line) > 4096:
                raise WebhookHttpError("Invalid webhook HTTP status line.")
            try:
                decoded = status_line.decode("ascii").strip()
                protocol, status_text, _reason = decoded.split(" ", 2)
                status_code = int(status_text)
            except (UnicodeDecodeError, ValueError) as exc:
                raise WebhookHttpError("Invalid webhook HTTP status line.") from exc
            if protocol not in {"HTTP/1.0", "HTTP/1.1"} or not 100 <= status_code <= 599:
                raise WebhookHttpError("Invalid webhook HTTP response.")

            header_bytes = 0
            for _ in range(100):
                line = await asyncio.wait_for(reader.readline(), timeout=timeout)
                header_bytes += len(line)
                if header_bytes > 32 * 1024:
                    raise WebhookHttpError("Webhook response headers are too large.")
                if line in {b"\r\n", b"\n", b""}:
                    break
            else:
                raise WebhookHttpError("Webhook response contains too many headers.")

            return status_code
        except (OSError, asyncio.TimeoutError, ssl.SSLError, WebhookHttpError) as exc:
            last_error = exc
        finally:
            if writer is not None:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
                except Exception:
                    pass

    raise WebhookHttpError("Webhook delivery failed.") from last_error
