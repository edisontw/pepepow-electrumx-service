import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from ..config import Settings
from ..electrumx.errors import ElectrumXConnectionError, ElectrumXError
from ..electrumx.subscription_client import ElectrumXSubscriptionClient
from .payment_state import PaymentStateError, match_transaction_outputs
from .payment_store import PaymentStore

logger = logging.getLogger(__name__)


class PaymentWatcher:
    """Persistent ElectrumX subscription watcher for persisted payments."""

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[..., ElectrumXSubscriptionClient] = ElectrumXSubscriptionClient,
    ) -> None:
        self.settings = settings
        self.client_factory = client_factory
        self.store = PaymentStore(settings.payment_db_path)
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._notifications: asyncio.Queue[tuple[str, list[Any]]] = asyncio.Queue(maxsize=1024)
        self._subscribed: set[str] = set()
        self._needs_full_reconcile = False

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self.run_forever(), name="pepew-payment-watcher")

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
                logger.warning("Payment watcher connection cycle failed: %s", type(exc).__name__)
            finally:
                await client.close()
                self._subscribed.clear()

            if self._stop_event.is_set():
                break

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(max_backoff, backoff * 2)

    async def _run_connected(self, client: ElectrumXSubscriptionClient) -> None:
        self._drain_notifications()
        await client.connect()

        header = await client.subscribe_headers()
        await self._apply_header(header)

        await self._sync_subscriptions(client, reconcile_new=True)

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

    async def _reconcile_all_subscriptions(
        self,
        client: ElectrumXSubscriptionClient,
    ) -> None:
        for scripthash in tuple(self._subscribed):
            await self._reconcile_scripthash(client, scripthash)

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
