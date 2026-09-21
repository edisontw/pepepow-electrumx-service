from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
import time
from typing import Any, Iterable

TXID_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class PaymentStateError(ValueError):
    pass


@dataclass(frozen=True)
class PaymentTransactionObservation:
    txid: str
    vout: int
    value_sats: int
    height: int
    first_seen_at: int | None = None


@dataclass(frozen=True)
class PaymentEvaluation:
    status: str
    requested_sats: int
    received_sats: int
    confirmed_sats: int
    policy_confirmed_sats: int
    confirmations_required: int
    matched_output_count: int
    mempool_output_count: int
    expired: bool

    @property
    def settled(self) -> bool:
        return self.policy_confirmed_sats >= self.requested_sats

    @property
    def overpaid_by_sats(self) -> int:
        return max(0, self.received_sats - self.requested_sats)


def confirmations_for_height(height: int, tip_height: int) -> int:
    """Return chain confirmations for an ElectrumX history height."""
    block_height = int(height)
    tip = int(tip_height)
    if block_height <= 0 or tip < block_height:
        return 0
    return tip - block_height + 1


def _addresses_from_script(script: Any) -> tuple[str, ...]:
    if not isinstance(script, dict):
        return ()

    addresses: list[str] = []
    address = script.get("address")
    if isinstance(address, str):
        addresses.append(address)

    raw_addresses = script.get("addresses")
    if isinstance(raw_addresses, list):
        addresses.extend(item for item in raw_addresses if isinstance(item, str))

    # Preserve order while avoiding duplicate matches from verbose RPC variants.
    return tuple(dict.fromkeys(addresses))


def _value_to_sats(output: dict[str, Any], decimals: int) -> int:
    for key in ("valueSat", "value_sats", "satoshis", "atoms"):
        if key not in output:
            continue
        try:
            value = int(output[key])
        except (TypeError, ValueError) as exc:
            raise PaymentStateError(f"Invalid atomic output value in {key}.") from exc
        if value < 0:
            raise PaymentStateError("Transaction output value must not be negative.")
        return value

    if "value" not in output:
        raise PaymentStateError("Transaction output does not contain a value.")

    try:
        coin_value = Decimal(str(output["value"]))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PaymentStateError("Transaction output value is invalid.") from exc

    if not coin_value.is_finite() or coin_value < 0:
        raise PaymentStateError("Transaction output value must be finite and non-negative.")

    scale = Decimal(10) ** int(decimals)
    atoms = coin_value * scale
    if atoms != atoms.to_integral_value():
        raise PaymentStateError(f"Transaction output exceeds {decimals} decimal places.")
    return int(atoms)


def match_transaction_outputs(
    tx_data: Any,
    address: str,
    *,
    height: int,
    txid: str | None = None,
    first_seen_at: int | None = None,
    decimals: int = 8,
) -> tuple[PaymentTransactionObservation, ...]:
    """Extract outputs paying exactly to address from verbose transaction data."""
    if not isinstance(tx_data, dict):
        raise PaymentStateError("Verbose transaction data must be an object.")

    resolved_txid = txid or tx_data.get("txid") or tx_data.get("hash") or tx_data.get("tx_hash")
    if not isinstance(resolved_txid, str) or TXID_RE.fullmatch(resolved_txid) is None:
        raise PaymentStateError("Transaction id must be 64 hex characters.")
    resolved_txid = resolved_txid.lower()

    outputs = tx_data.get("vout")
    if not isinstance(outputs, list):
        outputs = tx_data.get("outputs")
    if not isinstance(outputs, list):
        raise PaymentStateError("Verbose transaction data does not contain outputs.")

    observations: list[PaymentTransactionObservation] = []
    for fallback_vout, output in enumerate(outputs):
        if not isinstance(output, dict):
            continue

        script = output.get("scriptPubKey") or output.get("script_pub_key") or output.get("script")
        if address not in _addresses_from_script(script):
            continue

        raw_vout = output.get("n", fallback_vout)
        try:
            vout = int(raw_vout)
        except (TypeError, ValueError) as exc:
            raise PaymentStateError("Transaction output index is invalid.") from exc
        if vout < 0:
            raise PaymentStateError("Transaction output index must not be negative.")

        value_sats = _value_to_sats(output, decimals)
        if value_sats <= 0:
            continue

        observations.append(
            PaymentTransactionObservation(
                txid=resolved_txid,
                vout=vout,
                value_sats=value_sats,
                height=int(height),
                first_seen_at=None if first_seen_at is None else int(first_seen_at),
            )
        )

    return tuple(observations)


def observation_belongs_to_payment(
    observation: PaymentTransactionObservation,
    *,
    created_height: int | None = None,
    created_at: int | None = None,
    expires_at: int | None = None,
) -> bool:
    """Conservatively decide whether an observed output belongs to a payment window.

    Confirmed outputs at or below the creation height are pre-existing chain state.
    Unconfirmed outputs require first_seen_at when a creation timestamp is supplied.
    Expiring payments require first_seen_at so late arrivals cannot be accepted by
    reconstruction alone.
    """
    if created_height is not None and observation.height > 0 and observation.height <= int(created_height):
        return False

    if created_at is not None:
        if observation.height <= 0 and observation.first_seen_at is None:
            return False
        if observation.first_seen_at is not None and observation.first_seen_at < int(created_at):
            return False

    if expires_at is not None:
        if observation.first_seen_at is None:
            return False
        if observation.first_seen_at > int(expires_at):
            return False

    return True


def evaluate_payment_observations(
    requested_sats: int,
    observations: Iterable[PaymentTransactionObservation],
    *,
    tip_height: int,
    confirmations_required: int,
    created_height: int | None = None,
    created_at: int | None = None,
    expires_at: int | None = None,
    now: int | None = None,
) -> PaymentEvaluation:
    """Evaluate authoritative payment state from matched transaction outputs."""
    requested = int(requested_sats)
    required = int(confirmations_required)
    tip = int(tip_height)

    if requested <= 0:
        raise PaymentStateError("Requested payment amount must be greater than zero.")
    if required < 0:
        raise PaymentStateError("Confirmations must be zero or greater.")
    if tip < 0:
        raise PaymentStateError("Tip height must be zero or greater.")

    scoped = tuple(
        observation
        for observation in observations
        if observation_belongs_to_payment(
            observation,
            created_height=created_height,
            created_at=created_at,
            expires_at=expires_at,
        )
    )

    # txid:vout is the authoritative output identity. Duplicate notifications or
    # reconciliation reads must never double-count the same output.
    unique: dict[tuple[str, int], PaymentTransactionObservation] = {}
    for observation in scoped:
        key = (observation.txid, observation.vout)
        previous = unique.get(key)
        if previous is None:
            unique[key] = observation
            continue

        # Later observations are authoritative for chain position so a reorg can
        # move an output from confirmed back to mempool/unconfirmed. Preserve the
        # earliest known first-seen timestamp for creation/expiry semantics.
        first_seen_candidates = [
            value for value in (previous.first_seen_at, observation.first_seen_at) if value is not None
        ]
        first_seen_at = min(first_seen_candidates) if first_seen_candidates else None
        if previous.value_sats != observation.value_sats:
            raise PaymentStateError("Conflicting values observed for the same transaction output.")
        unique[key] = PaymentTransactionObservation(
            txid=observation.txid,
            vout=observation.vout,
            value_sats=observation.value_sats,
            height=observation.height,
            first_seen_at=first_seen_at,
        )

    matched = tuple(unique.values())
    received_sats = sum(item.value_sats for item in matched)
    confirmed_sats = sum(item.value_sats for item in matched if item.height > 0)
    mempool_output_count = sum(1 for item in matched if item.height <= 0)

    if required == 0:
        policy_confirmed_sats = received_sats
    else:
        policy_confirmed_sats = sum(
            item.value_sats
            for item in matched
            if confirmations_for_height(item.height, tip) >= required
        )

    current_time = int(time.time()) if now is None else int(now)
    is_expired = expires_at is not None and current_time > int(expires_at)

    if received_sats > requested:
        status = "overpaid"
    elif policy_confirmed_sats >= requested:
        status = "paid_confirmed"
    elif received_sats >= requested:
        status = "paid_unconfirmed"
    elif is_expired:
        status = "expired"
    elif received_sats > 0:
        status = "partial"
    elif mempool_output_count > 0:
        status = "seen_in_mempool"
    else:
        status = "waiting"

    return PaymentEvaluation(
        status=status,
        requested_sats=requested,
        received_sats=received_sats,
        confirmed_sats=confirmed_sats,
        policy_confirmed_sats=policy_confirmed_sats,
        confirmations_required=required,
        matched_output_count=len(matched),
        mempool_output_count=mempool_output_count,
        expired=is_expired,
    )
