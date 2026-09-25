import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from ..config import Settings, get_settings
from ..electrumx.errors import ElectrumXConnectionError, ElectrumXError
from ..electrumx.subscription_client import ElectrumXSubscriptionClient
from .payment_state import PaymentStateError, match_transaction_outputs
from .payment_store import PaymentStore

logger = logging.getLogger(__name__)

_active_payment_watcher_health: "PaymentWatcherHealth | None" = None


def _watcher_error_code(exc: Exception) -> str:
    if isinstance(exc, ElectrumXError):
        message = str(exc)
        if len(message) <= 64 and (
            message.startswith("electrumx_")
            or message == "payment_subscription_set_changed"
        ):
            return message
        code = getattr(exc, "code", None)
        if isinstance(code, str) and code:
            return code
    return "watcher_cycle_error"


class PaymentWatcherHealth:
    """In-memory operational health for the persistent payment watcher."""

    def __init__(
        self,
        *,
        enabled: bool,
        stale_after_seconds: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.enabled = bool(enabled)
        self.stale_after_seconds = max(1.0, float(stale_after_seconds))
        self._clock = clock
        self.running = False
        self.connected = False
        self.started_at: int | None = None
        self.last_successful_connection_at: int | None = None
        self.last_disconnected_at: int | None = None
        self.last_reconciliation_at: int | None = None
        self.last_header_at: int | None = None
        self.last_activity_at: int | None = None
        self.last_failure_at: int | None = None
        self.chain_tip_height: int | None = None
        self.subscribed_count = 0
        self.connection_attempts = 0
        self.reconnect_count = 0
        self.failure_count = 0
        self.consecutive_failures = 0
        self.last_error: str | None = None

    def _now(self) -> int:
        return int(self._clock())

    def mark_started(self) -> None:
        now = self._now()
        self.running = True
        self.connected = False
        self.started_at = now
        self.last_successful_connection_at = None
        self.last_disconnected_at = None
        self.last_reconciliation_at = None
        self.last_header_at = None
        self.last_activity_at = None
        self.last_failure_at = None
        self.chain_tip_height = None
        self.subscribed_count = 0
        self.connection_attempts = 0
        self.reconnect_count = 0
        self.failure_count = 0
        self.consecutive_failures = 0
        self.last_error = None

    def mark_stopped(self) -> None:
        if self.connected:
            self.last_disconnected_at = self._now()
        self.running = False
        self.connected = False
        self.subscribed_count = 0

    def mark_connection_attempt(self) -> None:
        self.connection_attempts += 1

    def mark_connected(self) -> None:
        now = self._now()
        self.connected = True
        self.last_successful_connection_at = now
        self.last_activity_at = now

    def mark_recovered(self) -> int:
        recovered_failures = self.consecutive_failures
        self.consecutive_failures = 0
        self.last_error = None
        return recovered_failures

    def mark_disconnected(self) -> None:
        if self.connected:
            self.last_disconnected_at = self._now()
        self.connected = False
        self.subscribed_count = 0

    def mark_reconciliation(self, subscribed_count: int) -> None:
        now = self._now()
        self.last_reconciliation_at = now
        self.last_activity_at = now
        self.subscribed_count = max(0, int(subscribed_count))

    def mark_header(self, height: int) -> None:
        now = self._now()
        self.last_header_at = now
        self.last_activity_at = now
        self.chain_tip_height = int(height)

    def mark_failure(self, exc: Exception) -> tuple[str, int]:
        now = self._now()
        if self.connected:
            self.last_disconnected_at = now
        self.connected = False
        self.subscribed_count = 0
        self.last_failure_at = now
        self.failure_count += 1
        self.consecutive_failures += 1
        self.last_error = _watcher_error_code(exc)
        return self.last_error, self.consecutive_failures

    def mark_reconnect(self) -> None:
        self.reconnect_count += 1

    def snapshot(self, *, now: int | None = None) -> dict[str, Any]:
        checked_at = self._now() if now is None else int(now)
        age = None
        if self.last_activity_at is not None:
            age = max(0, checked_at - self.last_activity_at)

        stale = bool(
            self.enabled
            and self.running
            and self.connected
            and age is not None
            and age > self.stale_after_seconds
        )

        if not self.enabled:
            state = "disabled"
        elif not self.running:
            state = "stopped"
        elif not self.connected:
            state = "starting" if self.connection_attempts == 0 else "disconnected"
        elif stale:
            state = "stale"
        elif self.consecutive_failures:
            state = "recovering"
        else:
            state = "healthy"

        return {
            "enabled": self.enabled,
            "running": self.running,
            "connected": self.connected,
            "state": state,
            "degraded": state in {"stopped", "disconnected", "stale", "recovering"},
            "stale": stale,
            "stale_after_seconds": self.stale_after_seconds,
            "started_at": self.started_at,
            "last_successful_connection_at": self.last_successful_connection_at,
            "last_disconnected_at": self.last_disconnected_at,
            "last_reconciliation_at": self.last_reconciliation_at,
            "last_header_at": self.last_header_at,
            "last_activity_at": self.last_activity_at,
            "last_activity_age_seconds": age,
            "last_failure_at": self.last_failure_at,
            "chain_tip_height": self.chain_tip_height,
            "subscribed_count": self.subscribed_count,
            "connection_attempts": self.connection_attempts,
            "reconnect_count": self.reconnect_count,
            "failure_count": self.failure_count,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
        }


def get_payment_watcher_status(*, now: int | None = None) -> dict[str, Any]:
    """Return privacy-safe watcher health without touching SQLite or ElectrumX."""

    health = _active_payment_watcher_health
    if health is not None:
        return health.snapshot(now=now)

    settings = get_settings()
    return PaymentWatcherHealth(
        enabled=settings.payment_watcher_enabled,
        stale_after_seconds=settings.payment_watcher_stale_seconds,
    ).snapshot(now=now)


class PaymentWatcher:
    """Persistent ElectrumX subscription watcher for persisted payments."""

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[..., ElectrumXSubscriptionClient] = ElectrumXSubscriptionClient,
        health_clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.client_factory = client_factory
        self.store = PaymentStore(settings.payment_db_path)
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._notifications: asyncio.Queue[tuple[str, list[Any]]] = asyncio.Queue(maxsize=1024)
        self._subscribed: set[str] = set()
        self._needs_full_reconcile = False
        self.health = PaymentWatcherHealth(
            enabled=settings.payment_watcher_enabled,
            stale_after_seconds=settings.payment_watcher_stale_seconds,
            clock=health_clock,
        )

    async def start(self) -> None:
        global _active_payment_watcher_health

        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self.health.mark_started()
        self._task = asyncio.create_task(self.run_forever(), name="pepew-payment-watcher")
        _active_payment_watcher_health = self.health

    async def stop(self) -> None:
        global _active_payment_watcher_health

        self._stop_event.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.health.mark_stopped()
        if _active_payment_watcher_health is self.health:
            _active_payment_watcher_health = None

    async def _on_notification(self, method: str, params: list[Any]) -> None:
        try:
            self._notifications.put_nowait((method, params))
        except asyncio.QueueFull:
            # Dropping individual notifications is safe only if the next connected
            # iteration performs a full reconciliation.
            self._needs_full_reconcile = True

    async def run_forever(self) -> None:
        backoff = max(0.1, float(self.settings.payment_watcher_reconnect_min_seconds))
        max_backoff = max(backoff, float(self.settings.payment_watcher_reconnect_max_seconds))

        while not self._stop_event.is_set():
            self.health.mark_connection_attempt()
            client = self.client_factory(
                self.settings,
                notification_handler=self._on_notification,
            )
            try:
                await self._run_connected(client)
                backoff = max(
                    0.1,
                    float(self.settings.payment_watcher_reconnect_min_seconds),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error_code, failures = self.health.mark_failure(exc)
                if failures <= 3 or failures % 10 == 0:
                    logger.warning(
                        "Payment watcher connection cycle failed: %s "
                        "(consecutive=%d, reconnects=%d)",
                        error_code,
                        failures,
                        self.health.reconnect_count,
                    )
                else:
                    logger.debug(
                        "Payment watcher connection cycle still failing: %s "
                        "(consecutive=%d)",
                        error_code,
                        failures,
                    )
            finally:
                self.health.mark_disconnected()
                await client.close()
                self._subscribed.clear()

            if self._stop_event.is_set():
                break

            self.health.mark_reconnect()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(max_backoff, backoff * 2)

    async def _run_connected(self, client: ElectrumXSubscriptionClient) -> None:
        self._drain_notifications()
        await client.connect()
        self.health.mark_connected()

        header = await client.subscribe_headers()
        await self._apply_header(header)

        await self._sync_subscriptions(client, reconcile_new=True)
        recovered_failures = self.health.mark_recovered()
        if recovered_failures:
            logger.info(
                "Payment watcher ElectrumX connection recovered after %d consecutive failure(s)",
                recovered_failures,
            )

        refresh_seconds = max(
            1.0,
            float(self.settings.payment_watcher_subscription_refresh_seconds),
        )

        while not self._stop_event.is_set():
            if not client.connected:
                raise ElectrumXConnectionError("electrumx_connection_closed")

            try:
                method, params = await asyncio.wait_for(
                    self._notifications.get(),
                    timeout=refresh_seconds,
                )
            except asyncio.TimeoutError:
                await self._sync_subscriptions(client, reconcile_new=False)
                if self._needs_full_reconcile:
                    await self._reconcile_all_subscriptions(client)
                    self._needs_full_reconcile = False
                continue

            await self._process_notification(client, method, params)

    def _drain_notifications(self) -> None:
        while True:
            try:
                self._notifications.get_nowait()
            except asyncio.QueueEmpty:
                return

    async def _sync_subscriptions(
        self,
        client: ElectrumXSubscriptionClient,
        *,
        reconcile_new: bool,
    ) -> None:
        desired_list = await asyncio.to_thread(
            self.store.list_watch_scripthashes,
            limit=int(self.settings.payment_watcher_max_subscriptions),
        )
        desired = set(desired_list)

        # ElectrumX protocol 1.4 does not guarantee unsubscribe support. Reconnect
        # instead of allowing old subscriptions to grow beyond the configured cap.
        if self._subscribed - desired:
            raise ElectrumXConnectionError("payment_subscription_set_changed")

        for scripthash in desired_list:
            if scripthash in self._subscribed:
                continue
            await client.subscribe_scripthash(scripthash)
            self._subscribed.add(scripthash)
            if reconcile_new:
                await self._reconcile_scripthash(client, scripthash)
            else:
                # New payments must be reconciled immediately even if their initial
                # subscription status hash is unchanged after registration.
                await self._reconcile_scripthash(client, scripthash)

        self.health.mark_reconciliation(len(self._subscribed))

    async def _reconcile_all_subscriptions(
        self,
        client: ElectrumXSubscriptionClient,
    ) -> None:
        for scripthash in tuple(self._subscribed):
            await self._reconcile_scripthash(client, scripthash)
        self.health.mark_reconciliation(len(self._subscribed))

    async def _process_notification(
        self,
        client: ElectrumXSubscriptionClient,
        method: str,
        params: list[Any],
    ) -> None:
        if method == "blockchain.headers.subscribe":
            header = params[0] if params else None
            await self._apply_header(header)
            return

        if method == "blockchain.scripthash.subscribe":
            if not params or not isinstance(params[0], str):
                return
            scripthash = params[0]
            if scripthash in self._subscribed:
                await self._reconcile_scripthash(client, scripthash)

    async def _apply_header(self, header: Any) -> None:
        if not isinstance(header, dict):
            return
        height = header.get("height")
        if not isinstance(height, int) or height < 0:
            return
        tip_hash = header.get("hash")
        if tip_hash is not None and not isinstance(tip_hash, str):
            tip_hash = None

        now = int(time.time())
        await asyncio.to_thread(
            self.store.set_chain_tip,
            height,
            tip_hash=tip_hash,
            updated_at=now,
        )
        payment_ids = await asyncio.to_thread(
            self.store.list_refreshable_payment_ids,
            now=now,
            limit=max(1, int(self.settings.payment_watcher_max_subscriptions) * 2),
        )
        for payment_id in payment_ids:
            await asyncio.to_thread(self.store.refresh_payment, payment_id, now=now)
        self.health.mark_header(height)

    async def _reconcile_scripthash(
        self,
        client: ElectrumXSubscriptionClient,
        scripthash: str,
    ) -> None:
        history_result = await client.request(
            "blockchain.scripthash.get_history",
            [scripthash],
        )
        history: list[tuple[str, int]] = []
        if isinstance(history_result, list):
            for item in history_result:
                if not isinstance(item, dict):
                    continue
                txid = item.get("tx_hash")
                height = item.get("height")
                if not isinstance(txid, str):
                    continue
                try:
                    parsed_height = int(height or 0)
                except (TypeError, ValueError):
                    continue
                history.append((txid.lower(), parsed_height))

        current_txids = {txid for txid, _height in history}
        payments = await asyncio.to_thread(
            self.store.get_payments_by_scripthash,
            scripthash,
        )
        if not payments:
            self.health.mark_reconciliation(len(self._subscribed))
            return

        verbose_transactions: dict[str, Any] = {}
        observed_at = int(time.time())

        for payment in payments:
            payment_id = str(payment["payment_id"])
            baseline_txids = await asyncio.to_thread(
                self.store.get_baseline_txids,
                payment_id,
            )

            for txid, height in history:
                if txid in baseline_txids:
                    continue

                tx_data = verbose_transactions.get(txid)
                if tx_data is None:
                    tx_data = await client.request(
                        "blockchain.transaction.get",
                        [txid, True],
                    )
                    verbose_transactions[txid] = tx_data

                try:
                    observations = match_transaction_outputs(
                        tx_data,
                        str(payment["address"]),
                        height=height,
                        txid=txid,
                        first_seen_at=observed_at,
                        decimals=int(self.settings.pepew_decimals),
                    )
                except PaymentStateError:
                    logger.warning("Ignoring malformed verbose transaction for payment reconciliation")
                    continue

                for observation in observations:
                    await asyncio.to_thread(
                        self.store.upsert_transaction,
                        payment_id,
                        observation,
                        updated_at=observed_at,
                    )

            await asyncio.to_thread(
                self.store.delete_transactions_not_in,
                payment_id,
                current_txids,
            )
            await asyncio.to_thread(
                self.store.refresh_payment,
                payment_id,
                now=observed_at,
            )

        self.health.mark_reconciliation(len(self._subscribed))
