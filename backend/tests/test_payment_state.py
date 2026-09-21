import pytest

from app.services.payment_state import (
    PaymentStateError,
    PaymentTransactionObservation,
    confirmations_for_height,
    evaluate_payment_observations,
    match_transaction_outputs,
)

ADDRESS = "PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb"
OTHER_ADDRESS = "PXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
TX_A = "a" * 64
TX_B = "b" * 64


def obs(txid, vout, value, height, seen=1000):
    return PaymentTransactionObservation(
        txid=txid,
        vout=vout,
        value_sats=value,
        height=height,
        first_seen_at=seen,
    )


def test_confirmations_are_derived_from_tip_and_height():
    assert confirmations_for_height(100, 100) == 1
    assert confirmations_for_height(100, 102) == 3
    assert confirmations_for_height(0, 102) == 0
    assert confirmations_for_height(-1, 102) == 0
    assert confirmations_for_height(103, 102) == 0


def test_match_transaction_outputs_uses_exact_decimal_conversion():
    tx = {
        "txid": TX_A,
        "vout": [
            {
                "n": 0,
                "value": "1.25000000",
                "scriptPubKey": {"addresses": [ADDRESS]},
            },
            {
                "n": 1,
                "value": 2,
                "scriptPubKey": {"address": OTHER_ADDRESS},
            },
            {
                "n": 2,
                "valueSat": 25,
                "scriptPubKey": {"address": ADDRESS},
            },
        ],
    }

    matched = match_transaction_outputs(tx, ADDRESS, height=101, first_seen_at=1000)

    assert [(item.vout, item.value_sats) for item in matched] == [
        (0, 125000000),
        (2, 25),
    ]


def test_match_transaction_outputs_rejects_sub_atomic_values():
    tx = {
        "txid": TX_A,
        "vout": [
            {
                "n": 0,
                "value": "0.000000001",
                "scriptPubKey": {"address": ADDRESS},
            }
        ],
    }

    with pytest.raises(PaymentStateError):
        match_transaction_outputs(tx, ADDRESS, height=101)


def test_preexisting_confirmed_output_is_not_a_new_payment():
    evaluation = evaluate_payment_observations(
        100,
        [obs(TX_A, 0, 100, height=500, seen=900)],
        tip_height=510,
        confirmations_required=3,
        created_height=500,
        created_at=1000,
        now=1001,
    )

    assert evaluation.status == "waiting"
    assert evaluation.received_sats == 0


def test_unconfirmed_output_seen_before_creation_is_not_counted():
    evaluation = evaluate_payment_observations(
        100,
        [obs(TX_A, 0, 100, height=0, seen=999)],
        tip_height=500,
        confirmations_required=1,
        created_height=500,
        created_at=1000,
        now=1001,
    )

    assert evaluation.status == "waiting"
    assert evaluation.received_sats == 0


def test_unconfirmed_without_first_seen_is_conservatively_excluded():
    observation = PaymentTransactionObservation(
        txid=TX_A,
        vout=0,
        value_sats=100,
        height=0,
        first_seen_at=None,
    )

    evaluation = evaluate_payment_observations(
        100,
        [observation],
        tip_height=500,
        confirmations_required=0,
        created_at=1000,
        now=1001,
    )

    assert evaluation.status == "waiting"


def test_partial_multiple_transactions_combine_to_payment():
    observations = [
        obs(TX_A, 0, 40, height=501, seen=1001),
        obs(TX_B, 1, 60, height=502, seen=1002),
    ]

    unconfirmed = evaluate_payment_observations(
        100,
        observations,
        tip_height=503,
        confirmations_required=3,
        created_height=500,
        created_at=1000,
        now=1003,
    )
    assert unconfirmed.received_sats == 100
    assert unconfirmed.policy_confirmed_sats == 40
    assert unconfirmed.status == "paid_unconfirmed"

    confirmed = evaluate_payment_observations(
        100,
        observations,
        tip_height=504,
        confirmations_required=3,
        created_height=500,
        created_at=1000,
        now=1004,
    )
    assert confirmed.policy_confirmed_sats == 100
    assert confirmed.status == "paid_confirmed"


def test_duplicate_observation_does_not_double_count():
    first = obs(TX_A, 0, 100, height=0, seen=1001)
    confirmed = obs(TX_A, 0, 100, height=501, seen=1001)

    evaluation = evaluate_payment_observations(
        100,
        [first, confirmed, confirmed],
        tip_height=503,
        confirmations_required=3,
        created_height=500,
        created_at=1000,
        now=1004,
    )

    assert evaluation.received_sats == 100
    assert evaluation.matched_output_count == 1
    assert evaluation.status == "paid_confirmed"


def test_paid_state_does_not_depend_on_current_address_balance():
    observations = [obs(TX_A, 0, 100, height=501, seen=1001)]

    evaluation = evaluate_payment_observations(
        100,
        observations,
        tip_height=503,
        confirmations_required=3,
        created_height=500,
        created_at=1000,
        now=1100,
    )

    # There is intentionally no current-balance argument. Spending or
    # consolidating this output later cannot revert the recorded payment.
    assert evaluation.status == "paid_confirmed"
    assert evaluation.settled is True


def test_partial_payment_becomes_expired_but_paid_payment_does_not_revert():
    partial = evaluate_payment_observations(
        100,
        [obs(TX_A, 0, 40, height=501, seen=1001)],
        tip_height=510,
        confirmations_required=3,
        created_height=500,
        created_at=1000,
        expires_at=1050,
        now=1060,
    )
    assert partial.status == "expired"
    assert partial.received_sats == 40
    assert partial.expired is True

    paid = evaluate_payment_observations(
        100,
        [obs(TX_A, 0, 100, height=501, seen=1001)],
        tip_height=510,
        confirmations_required=3,
        created_height=500,
        created_at=1000,
        expires_at=1050,
        now=1060,
    )
    assert paid.status == "paid_confirmed"
    assert paid.expired is True


def test_late_payment_after_expiry_is_not_accepted():
    evaluation = evaluate_payment_observations(
        100,
        [obs(TX_A, 0, 100, height=501, seen=1051)],
        tip_height=510,
        confirmations_required=3,
        created_height=500,
        created_at=1000,
        expires_at=1050,
        now=1060,
    )

    assert evaluation.status == "expired"
    assert evaluation.received_sats == 0


def test_overpayment_is_preserved_with_confirmation_policy_detail():
    evaluation = evaluate_payment_observations(
        100,
        [obs(TX_A, 0, 150, height=501, seen=1001)],
        tip_height=501,
        confirmations_required=3,
        created_height=500,
        created_at=1000,
        now=1002,
    )

    assert evaluation.status == "overpaid"
    assert evaluation.overpaid_by_sats == 50
    assert evaluation.settled is False


def test_conflicting_duplicate_outpoint_is_rejected():
    with pytest.raises(PaymentStateError):
        evaluate_payment_observations(
            100,
            [
                obs(TX_A, 0, 100, height=0, seen=1001),
                obs(TX_A, 0, 101, height=501, seen=1001),
            ],
            tip_height=503,
            confirmations_required=3,
            created_height=500,
            created_at=1000,
            now=1004,
        )


def test_later_observation_can_downgrade_confirmation_after_reorg():
    confirmed = obs(TX_A, 0, 100, height=501, seen=1001)
    reorged = obs(TX_A, 0, 100, height=0, seen=1001)

    evaluation = evaluate_payment_observations(
        100,
        [confirmed, reorged],
        tip_height=503,
        confirmations_required=3,
        created_height=500,
        created_at=1000,
        now=1004,
    )

    assert evaluation.received_sats == 100
    assert evaluation.confirmed_sats == 0
    assert evaluation.policy_confirmed_sats == 0
    assert evaluation.status == "paid_unconfirmed"
