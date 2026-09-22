# PEPEW Payment Webhooks

Status: **Phase E implementation**

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

## Production state

The implementation is complete in GitHub but production rollout remains gated.

Current safe defaults remain:

```text
PAYMENT_API_ENABLED=false
PAYMENT_WATCHER_ENABLED=false
PAYMENT_WEBHOOK_ENABLED=false
```

Production enablement should happen as an explicit E2E deployment step with protected environment-file permissions, a generated merchant API key, a generated webhook master key, Nginx/systemd reload, and end-to-end validation.
