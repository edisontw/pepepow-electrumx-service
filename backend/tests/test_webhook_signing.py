from app.services.webhook_signing import (
    derive_webhook_secret,
    sign_webhook_payload,
    verify_webhook_signature,
)


def test_webhook_secret_is_deterministic_per_endpoint_and_not_master_key():
    master = "m" * 48
    first = derive_webhook_secret(master, "wh_example")
    second = derive_webhook_secret(master, "wh_example")
    other = derive_webhook_secret(master, "wh_other")

    assert first == second
    assert first != other
    assert master not in first
    assert len(first) >= 40


def test_webhook_signature_binds_timestamp_event_and_body():
    secret = derive_webhook_secret("k" * 48, "wh_example")
    body = b'{"event_id":"evt_test"}'
    signature = sign_webhook_payload(
        secret,
        event_id="evt_test",
        timestamp=1234,
        body=body,
    )

    assert signature.startswith("v1=")
    assert verify_webhook_signature(
        secret,
        event_id="evt_test",
        timestamp=1234,
        body=body,
        signature=signature,
    )
    assert not verify_webhook_signature(
        secret,
        event_id="evt_other",
        timestamp=1234,
        body=body,
        signature=signature,
    )
    assert not verify_webhook_signature(
        secret,
        event_id="evt_test",
        timestamp=1234,
        body=b'{"event_id":"evt_modified"}',
        signature=signature,
    )
