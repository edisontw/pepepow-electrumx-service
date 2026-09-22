import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ..config import Settings
from .payment_store import PaymentStore
from .webhook_http import WebhookHttpError, send_webhook_https
from .webhook_security import (
    WebhookResolutionError,
    WebhookUrlError,
    resolve_webhook_target,
)
from .webhook_signing import WebhookSigningError, derive_webhook_secret, sign_webhook_payload

logger = logging.getLogger(__name__)

WebhookSender = Callable[..., Awaitable[int]]


class WebhookWorker:
    def __init__(
        self,
        settings: Settings,
        *,
        sender: WebhookSender = send_webhook_https,
    ) -> None:
        self.settings = settings
        self.sender = sender
        self.store = PaymentStore(settings.payment_db_path)
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        # Fail startup rather than run an enabled worker with no signing key.
        derive_webhook_secret(self.settings.payment_webhook_master_key, "wh_startup_check")
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self.run_forever(), name="pepew-webhook-worker")

    async def stop(self) -> None:
        self._stop_event.set()
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def run_forever(self) -> None:
        poll_seconds = max(0.2, float(self.settings.payment_webhook_poll_seconds))
        while not self._stop_event.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Webhook worker cycle failed: %s", type(exc).__name__)

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=poll_seconds)
            except asyncio.TimeoutError:
                pass

    async def run_once(self) -> int:
        now = int(time.time())
        deliveries = await asyncio.to_thread(
            self.store.list_due_webhook_deliveries,
            now=now,
            limit=int(self.settings.payment_webhook_batch_size),
        )
        for delivery in deliveries:
            await self._deliver(delivery)
        return len(deliveries)

    def _retry_delay(self, attempt_number: int) -> int:
        base = max(1, int(self.settings.payment_webhook_retry_base_seconds))
        maximum = max(base, int(self.settings.payment_webhook_retry_max_seconds))
        exponent = max(0, int(attempt_number) - 1)
        return min(maximum, base * (2 ** exponent))

    async def _deliver(self, delivery: dict[str, Any]) -> None:
        delivery_id = str(delivery["delivery_id"])
        event_id = str(delivery["event_id"])
        endpoint_id = str(delivery["endpoint_id"])
        url = str(delivery["url"])
        payload_json = delivery.get("payload_json")
        if not isinstance(payload_json, str):
            await self._fail(
                delivery,
                error_code="invalid_event_payload",
                http_status=None,
                terminal=True,
            )
            return

        body = payload_json.encode("utf-8")
        attempted_at = int(time.time())
        attempt_number = int(delivery["attempt_count"]) + 1
        terminal = attempt_number >= max(1, int(self.settings.payment_webhook_max_attempts))

        try:
            target = await resolve_webhook_target(url)
            secret = derive_webhook_secret(
                self.settings.payment_webhook_master_key,
                endpoint_id,
            )
            signature = sign_webhook_payload(
                secret,
                event_id=event_id,
                timestamp=attempted_at,
                body=body,
            )
            status = await self.sender(
                target,
                body=body,
                headers={
                    "X-PepewPay-Event-Id": event_id,
                    "X-PepewPay-Delivery-Id": delivery_id,
                    "X-PepewPay-Timestamp": str(attempted_at),
                    "X-PepewPay-Signature": signature,
                },
                timeout_seconds=float(self.settings.payment_webhook_timeout_seconds),
            )
        except WebhookUrlError as exc:
            await self._fail(
                delivery,
                error_code=exc.code,
                http_status=None,
                terminal=True,
                attempted_at=attempted_at,
            )
            return
        except WebhookSigningError:
            await self._fail(
                delivery,
                error_code="webhook_signing_unavailable",
                http_status=None,
                terminal=True,
                attempted_at=attempted_at,
            )
            return
        except WebhookResolutionError:
            await self._fail(
                delivery,
                error_code="webhook_dns_error",
                http_status=None,
                terminal=terminal,
                attempted_at=attempted_at,
            )
            return
        except (WebhookHttpError, OSError, asyncio.TimeoutError):
            await self._fail(
                delivery,
                error_code="webhook_delivery_error",
                http_status=None,
                terminal=terminal,
                attempted_at=attempted_at,
            )
            return

        if 200 <= status < 300:
            await asyncio.to_thread(
                self.store.mark_webhook_delivery_success,
                delivery_id,
                http_status=status,
                attempted_at=attempted_at,
            )
            return

        retryable = status in {408, 409, 425, 429} or 500 <= status <= 599
        await self._fail(
            delivery,
            error_code="webhook_http_error",
            http_status=status,
            terminal=terminal or not retryable,
            attempted_at=attempted_at,
        )

    async def _fail(
        self,
        delivery: dict[str, Any],
        *,
        error_code: str,
        http_status: int | None,
        terminal: bool,
        attempted_at: int | None = None,
    ) -> None:
        timestamp = int(time.time()) if attempted_at is None else int(attempted_at)
        attempt_number = int(delivery["attempt_count"]) + 1
        next_attempt_at = timestamp + self._retry_delay(attempt_number)
        await asyncio.to_thread(
            self.store.mark_webhook_delivery_failure,
            str(delivery["delivery_id"]),
            http_status=http_status,
            error_code=error_code,
            attempted_at=timestamp,
            next_attempt_at=next_attempt_at,
            dead=terminal,
        )
