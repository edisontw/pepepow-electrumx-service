# Phase H3 — Reference Merchant Integration Flow

Status: **COMPLETE — canonical merchant flow implemented and contract-tested**

This document is the canonical merchant-side integration sequence for the PEPEW
Payment Platform v1.

The reference implementation is:

```text
backend/examples/reference_merchant.py
```

It is standalone example code. The production PEPEW Payment Platform does not
import it, and H3 adds no production daemon, database, queue, or network service.

## 1. Trust boundary

Merchant architecture:

```text
Customer browser
    |
    | public checkout capability only
    v
https://pay.pepepow.net/?payment_id=pay_...
    |
    v
PepewPay
    |
    | read-only capability GET
    v
PEPEW Payment Platform

Merchant backend
    |
    | Bearer API key (server-side only)
    +--> POST /api/v1/payments
    +--> GET  /api/v1/payments?merchant_reference=...
    |
    | endpoint signing secret (server-side only)
    <--- signed webhook POST
```

Never expose to the customer browser:

- merchant Bearer API key
- webhook endpoint signing secret
- webhook master key
- merchant backend credentials
- mnemonic/private key/signing material

The browser needs only the intended high-entropy `payment_id` capability URL.

## 2. Merchant identifiers

Keep these identities separate:

```text
merchant_reference = merchant business/order identity
Idempotency-Key    = stable retry identity for one create request
payment_id         = PEPEW Payment Platform capability identity
event_id           = webhook/event idempotency identity
payment_version    = authoritative ordering of payment-state changes
```

Recommended merchant flow for one order:

```text
order_id        = ORDER-1234
merchant_reference = ORDER-1234
idempotency_key = create:ORDER-1234:v1
```

The merchant must persist the chosen Idempotency-Key before the remote create
request. Do not generate a different retry key after a timeout or process crash.

## 3. Create payment

First reserve the merchant order locally:

```python
store.reserve_order(
    "ORDER-1234",
    "create:ORDER-1234:v1",
)
```

Then call the authenticated Payment API from the merchant backend:

```python
client = MerchantApiClient(
    api_key=MERCHANT_API_KEY,
)

payment = client.create_payment(
    merchant_reference="ORDER-1234",
    idempotency_key="create:ORDER-1234:v1",
    address=PEPEW_RECEIVE_ADDRESS,
    amount="12.34",
    confirmations=3,
    expires_in=900,
)
```

This sends:

```http
POST /api/v1/payments
Authorization: Bearer <merchant key>
Idempotency-Key: create:ORDER-1234:v1
Content-Type: application/json
```

The merchant then binds the returned `payment_id`, status, and version to its
own order:

```python
order = store.bind_payment(
    "ORDER-1234",
    payment,
)
```

The reference store never uses current address balance as merchant payment
authority.

## 4. Customer checkout handoff

The customer-facing checkout URL is:

```text
https://pay.pepepow.net/?payment_id=pay_...
```

Build it only from the returned capability ID:

```python
checkout_url = build_checkout_url(payment["payment_id"])
```

Do not append the merchant API key, webhook secret, internal order metadata, or
wallet secrets.

`merchant_reference` remains merchant-side business metadata. It is not
returned by the public capability-status endpoint.

## 5. Create retry and crash recovery

If the create request times out, the merchant does not know whether the server
committed the payment before the connection failed.

Safe choices:

1. retry the same create request with the same `Idempotency-Key`
2. recover by the durable `merchant_reference`

Reference recovery:

```python
payment = client.recover_payment("ORDER-1234")
if payment is not None:
    store.bind_payment("ORDER-1234", payment)
```

This calls:

```http
GET /api/v1/payments?merchant_reference=ORDER-1234&limit=2
Authorization: Bearer <merchant key>
```

The listing route is merchant-authenticated and SQLite-only.

Do not create a second invoice merely because the first HTTP response was lost.

## 6. Provision webhook endpoint

Webhook endpoint management is server-to-server.

Create an endpoint through:

```http
POST /api/v1/webhook-endpoints
Authorization: Bearer <merchant key>
Content-Type: application/json
```

Store the returned `signing_secret` in protected merchant configuration. It is
returned only at endpoint creation.

A typical merchant should subscribe at least to:

```text
payment.paid_unconfirmed
payment.paid_confirmed
payment.overpaid
payment.expired
```

Subscribing to all events is also valid when the merchant wants a full payment
audit trail.

## 7. Verify webhook before parsing/trusting it

PEPEW sends:

```text
X-PepewPay-Event-Id
X-PepewPay-Delivery-Id
X-PepewPay-Timestamp
X-PepewPay-Signature
```

The HMAC input is:

```text
<timestamp> + "." + <event_id> + "." + <exact raw body bytes>
```

Reference verification:

```python
raw_body = await request.body()

event = verify_webhook(
    headers=request.headers,
    raw_body=raw_body,
    signing_secret=WEBHOOK_SIGNING_SECRET,
)
```

The reference verifier:

- computes HMAC-SHA256 over the exact raw bytes
- compares signatures in constant time
- verifies the header/body event ID match
- enforces Payment Event Envelope v1
- applies a 300-second replay window by default

Do not deserialize and reserialize JSON before signature verification.

## 8. Durable webhook deduplication

Webhook delivery is at least once.

The stable merchant deduplication key is:

```text
event_id
```

The reference SQLite merchant store inserts `event_id` with a primary-key
constraint in the same transaction that updates its order state.

A repeated delivery therefore becomes:

```text
duplicate
```

and causes no second business action.

A merchant must make its real order fulfillment path equally idempotent. For
example, do not ship twice because the same `payment.paid_confirmed` event was
retried.

## 9. Webhook/create race

An enabled webhook can theoretically reach the merchant before the HTTP create
response is durably saved by the merchant.

H3 handles this by requiring:

1. reserve the local order and Idempotency-Key first
2. then call `POST /api/v1/payments`
3. allow a signed `payment.created` event carrying the matching
   `merchant_reference` to bind the returned `payment_id`

If a valid event arrives for an order the merchant genuinely does not yet know,
the reference store does **not** persist the event ID as consumed. The receiver
should return a retryable response such as HTTP 409 so the Payment Platform can
deliver it again after local state is ready.

## 10. Payment version, reorgs, and state ordering

Do not define merchant state progression using a ranking such as:

```text
waiting < paid_unconfirmed < paid_confirmed
```

That is unsafe because a chain reorg may legitimately produce:

```text
payment.paid_confirmed
  -> payment.paid_unconfirmed
```

at a later payment version.

The reference store therefore follows:

```text
higher payment_version wins
```

regardless of whether the new status appears to move forward or backward.

An older event arriving late is stored as seen but does not overwrite newer
merchant order state.

## 11. Example receiver behavior

Framework pseudocode:

```python
raw_body = await request.body()

try:
    event = verify_webhook(
        headers=request.headers,
        raw_body=raw_body,
        signing_secret=WEBHOOK_SIGNING_SECRET,
    )
    outcome = store.apply_verified_event(event)
except UnknownMerchantOrderError:
    # 409 is retryable by PEPEW webhook delivery.
    return Response(status_code=409)
except WebhookVerificationError:
    return Response(status_code=400)

# "applied", "duplicate", and "stale" are all safely accepted.
return Response(status_code=204)
```

Real merchants should perform their own durable order update/fulfillment action
in the same local transaction or an equivalently idempotent workflow.

## 12. Browser status versus merchant authority

PepewPay/customer browsers may use:

```http
GET /api/v1/payments/{payment_id}
```

for read-only display.

Merchant business recovery uses the authenticated merchant API and signed
webhooks.

The customer browser must not be treated as the authoritative source for order
fulfillment.

## 13. Reference implementation boundaries

`backend/examples/reference_merchant.py` intentionally uses only Python 3.10
standard-library modules.

It demonstrates:

- authenticated create request
- stable Idempotency-Key
- exact merchant-reference recovery
- public checkout URL construction
- raw-body HMAC webhook verification
- replay-window enforcement
- durable event-ID deduplication
- payment-version ordering
- reorg-safe status updates
- local create/webhook race handling

It is reference code, not a production merchant SDK. Reusable SDK/helpers belong
to Phase H4 in `pepepow-devkit`.

## 14. H3 exit criteria

- [x] merchant API key remains backend-only
- [x] webhook signing secret remains backend-only
- [x] order/reference and stable retry identity are persisted before remote create
- [x] create response is durably mapped to merchant order
- [x] customer receives only a `payment_id` checkout capability
- [x] uncertain create outcomes have idempotent retry/recovery guidance
- [x] webhook HMAC uses exact raw body and constant-time comparison
- [x] webhook replay window is enforced
- [x] webhook event IDs are durably deduplicated
- [x] unknown-order race does not consume the webhook event
- [x] payment updates are ordered by `payment_version`, including reorg rollback states
- [x] deterministic tests cover the reference flow
- [x] no production runtime or infrastructure is added
