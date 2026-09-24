# PEPEW Payment Webhooks

Status: **Phase E complete — production E2E verified 2026-09-22**

PEPEW Payment Webhooks deliver durable Payment Event Envelope v1 records to merchant HTTPS endpoints.

The webhook layer consumes persisted SQLite events. It does not query address balances and it does not create a second source of payment truth.

## Feature gate

Webhook management and delivery are disabled by default:

```text
PAYMENT_WEBHOOK_ENABLED=false
PAYMENT_WEBHOOK_MASTER_KEY=
```

Do not enable delivery until the master key and merchant endpoint configuration are ready.

Recommended master-key generation:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Store the value only in protected server configuration. Do not commit it.

## Endpoint management

All webhook management routes use the same merchant server-side Bearer authorization as persisted payment creation.

### Create endpoint

```http
POST /api/v1/webhook-endpoints
Authorization: Bearer <PAYMENT_CREATE_API_KEY>
Content-Type: application/json
```

Example:

```json
{
  "url": "https://merchant.example/webhooks/pepew",
  "event_types": [
    "payment.paid_unconfirmed",
    "payment.paid_confirmed"
  ]
}
```

If `event_types` is omitted or empty, the endpoint receives every supported payment event generated after the endpoint exists.

The create response returns a high-entropy endpoint ID and a `signing_secret`.

The signing secret is returned on creation only. It is not stored in plaintext in SQLite and is not returned by endpoint-list APIs.

### List endpoints

```http
GET /api/v1/webhook-endpoints
Authorization: Bearer <PAYMENT_CREATE_API_KEY>
```

### Disable endpoint

```http
DELETE /api/v1/webhook-endpoints/{endpoint_id}
Authorization: Bearer <PAYMENT_CREATE_API_KEY>
```

Deletion is a logical disable for delivery selection.

### Delivery log

```http
GET /api/v1/webhook-deliveries
Authorization: Bearer <PAYMENT_CREATE_API_KEY>
```

Optional query parameters:

```text
endpoint_id
limit=1..500
```

The delivery log exposes operational metadata such as:

- delivery ID
- event ID
- endpoint ID
- event type
- payment ID/version
- current delivery status
- attempt count
- last HTTP status
- stable error code
- next retry time
- delivered time

It never returns signing secrets.

## Secret derivation

The server stores one webhook master key in its environment.

Each endpoint signing secret is derived deterministically:

```text
HMAC-SHA256(
  PAYMENT_WEBHOOK_MASTER_KEY,
  "pepew-webhook-secret-v1:" + endpoint_id
)
```

The digest is encoded as URL-safe base64 without padding.

This avoids storing plaintext webhook secrets in SQLite while allowing the same endpoint secret to survive process restarts.

### Master-key rotation

Changing the master key changes every derived endpoint signing secret.

For the initial rollout, rotate by:

1. disable existing webhook endpoints
2. change the master key
3. restart the service
4. recreate endpoints
5. update the merchant receiver with the new signing secret

Do not rotate the master key silently while old endpoints remain active.

## Event delivery

When a Payment Event Envelope is first inserted, the same SQLite transaction creates one delivery row for each currently enabled endpoint whose event filter matches.

The unique logical delivery key is:

```text
(event_id, endpoint_id)
```

A deterministic `delivery_id` is also generated from that pair.

Creating an endpoint later does not backfill historical events.

## Request format

Webhook delivery uses HTTPS POST.

Headers:

```text
Content-Type: application/json
X-PepewPay-Event-Id: evt_...
X-PepewPay-Delivery-Id: dlv_...
X-PepewPay-Timestamp: <Unix seconds>
X-PepewPay-Signature: v1=<hex HMAC-SHA256>
```

The body is the exact persisted Payment Event Envelope v1 JSON.

The signature input is:

```text
<timestamp> + "." + <event_id> + "." + <exact body bytes>
```

and the signature is:

```text
HMAC-SHA256(endpoint_signing_secret, signature_input)
```

The merchant should:

1. read the raw request body before JSON reserialization
2. recompute the HMAC using the endpoint signing secret
3. compare signatures in constant time
4. reject timestamps outside its allowed replay window
5. deduplicate processing by `event_id`
6. return a 2xx response only after it has safely accepted the event

The stable `event_id` is the merchant idempotency key. Retries reuse the same event ID, delivery ID, and body.

For newly created payments, Payment Event Envelope v1 may also contain `data.merchant_reference`. This is the optional merchant-owned business/order identifier supplied during payment creation. Historical event bodies created before the field existed are not rewritten, so receivers must treat the field as optional.

Do not put passwords, access tokens, personal records, or other secrets into `merchant_reference`; webhook bodies are merchant operational data but still pass through the configured receiver.

## Delivery semantics

Delivery is **at least once**.

If the receiver accepts the HTTP request but the PEPEW service crashes before SQLite records success, the same delivery may be retried.

Merchant consumers must therefore be idempotent by `event_id`.

Success:

```text
HTTP 2xx
```

Retryable responses:

```text
408
409
425
429
5xx
network/TLS timeout or connection failure
temporary DNS failure
```

Other HTTP statuses are terminal for that delivery.

Default retry settings:

```text
PAYMENT_WEBHOOK_MAX_ATTEMPTS=8
PAYMENT_WEBHOOK_RETRY_BASE_SECONDS=30
PAYMENT_WEBHOOK_RETRY_MAX_SECONDS=3600
```

Backoff is bounded exponential:

```text
30s -> 60s -> 120s -> 240s -> ... -> max 3600s
```

The queue is persisted in SQLite, so pending/retry/dead/delivered state survives process restart.

## SSRF and network safety

Webhook targets are untrusted input.

The implementation therefore requires all of the following:

- HTTPS only
- port 443 only
- no URL userinfo
- no URL fragments
- bounded URL length
- no control characters in the request target
- localhost and known metadata names rejected
- literal private/loopback/link-local/reserved IPs rejected
- DNS resolution must return only globally routable addresses
- mixed public/private DNS answers are rejected
- DNS resolution is bounded by timeout
- destination is resolved again on every delivery attempt
- the HTTPS socket connects directly to the validated IP address
- TLS SNI and HTTP Host preserve the original merchant hostname
- redirects are not followed
- response headers are bounded
- request/connect/read operations are bounded by timeout

Connecting directly to the already validated IP prevents the HTTP client from performing a second hostname resolution after validation, reducing DNS-rebinding risk.

## Resource bounds

Initial defaults:

```text
PAYMENT_WEBHOOK_POLL_SECONDS=2
PAYMENT_WEBHOOK_BATCH_SIZE=20
PAYMENT_WEBHOOK_TIMEOUT_SECONDS=5
PAYMENT_WEBHOOK_MAX_ATTEMPTS=8
PAYMENT_WEBHOOK_RETRY_BASE_SECONDS=30
PAYMENT_WEBHOOK_RETRY_MAX_SECONDS=3600
```

The worker processes a bounded batch and does not require Redis, Celery, RabbitMQ, Kafka, or another queue service.

## Logging and privacy

Do not log:

- merchant Bearer API key
- webhook master key
- derived signing secret
- Authorization header
- full request bodies containing secrets

Operational logs should prefer endpoint IDs, delivery IDs, event IDs, stable error codes, and exception class names.

Webhook endpoint URLs may themselves contain merchant-sensitive routing information, so avoid unnecessary long-term logging of full URLs.

## Supported events

Current Payment Event Envelope v1 events:

```text
payment.created
payment.waiting
payment.partial
payment.paid_unconfirmed
payment.paid_confirmed
payment.overpaid
payment.expired
```

A chain reorg may legitimately produce a later-version event that moves payment state backward. Merchants should process event versions in context rather than assuming payment state is strictly monotonic.

## Production E2E procedure

The repository includes production helpers under:

```text
backend/scripts/configure_webhook_production.py
backend/scripts/webhook_production_e2e.py
```

The configuration helper:

- preserves an existing non-empty `PAYMENT_WEBHOOK_MASTER_KEY`
- generates a new 48-byte URL-safe master key only when the setting is empty
- enables `PAYMENT_WEBHOOK_ENABLED=true`
- rewrites `backend/.env` atomically with mode `0600`
- never prints the master-key value

After restarting `pepew-light.service`, the E2E helper uses an anonymous,
temporary Webhook.site HTTPS receiver and a `payment.created` event to verify
the production path without requiring another real PEPEW transfer.

The E2E flow verifies:

1. a loopback target is rejected as `unsafe_webhook_target`
2. endpoint creation succeeds while the endpoint list does not expose
   `signing_secret`
3. a one-atom test payment emits `payment.created`
4. an intentional HTTP 503 is persisted as `retry`
5. the captured request signature verifies against the exact raw body bytes
6. the receiver is switched to HTTP 204
7. the retry reaches `delivered` using the same event ID, delivery ID, and body
8. the authenticated delivery log reports delivery metadata without secrets
9. the temporary endpoint is disabled and the temporary receiver token is
   deleted on cleanup

Only non-sensitive test payment metadata is sent to the third-party receiver.
Do not put customer/private data into the E2E payment label or message.

Example:

```bash
cd /home/ubuntu/pepepow-electrumx-service
git pull --ff-only

cd backend
python3 scripts/configure_webhook_production.py

sudo systemctl restart pepew-light
sudo systemctl --no-pager --full status pepew-light

python3 scripts/webhook_production_e2e.py \
  --reference-payment-id pay_<existing-production-payment-id>
```

The E2E helper reads the existing merchant API key and webhook configuration
from `backend/.env` and never prints their values.

## Production state

The implementation and production E2E are complete. Repository defaults remain disabled, but the current production host has webhook delivery explicitly enabled with a protected master key after successful validation.

Verified on 2026-09-22:

- loopback webhook target rejected as `unsafe_webhook_target`
- endpoint creation/listing preserved the one-time signing-secret boundary
- `payment.created` produced a real outbound delivery
- HTTP 503 persisted as `retry`
- the captured exact raw body verified successfully with HMAC-SHA256
- retry to HTTP 204 reached `delivered`
- retry reused the same event ID, delivery ID, and body
- authenticated delivery log returned operational metadata without secrets
- temporary endpoint/receiver cleanup completed
- no merchant API key, webhook master key, or signing secret was printed by the helper

Future deployments should rerun the same production E2E after changing the webhook master key, network path, host, or worker runtime.
