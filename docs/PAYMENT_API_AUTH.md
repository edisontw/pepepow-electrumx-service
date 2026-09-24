# Payment API Authentication v1

Status: **selected for initial Payment Platform rollout**

## Policy

Payment creation and merchant payment recovery are server-to-server actions using the same current single-merchant credential:

```http
POST /api/v1/payments
Authorization: Bearer <PAYMENT_CREATE_API_KEY>

GET /api/v1/payments
Authorization: Bearer <PAYMENT_CREATE_API_KEY>
```

The authenticated list is bounded and intended for merchant operational recovery. It may return payment capability IDs and merchant-supplied idempotency keys, so it must never be exposed without Bearer authentication.

Payment status for one known payment remains a read-only capability URL:

```http
GET /api/v1/payments/{payment_id}
```

The status endpoint does not require the merchant Bearer key. The payment ID is generated from cryptographically secure random bytes and is intended to be shared with PepewPay or a customer browser.

## Why merchant operations are not a browser-secret flow

The static PepewPay frontend must not contain a merchant API key.

Merchant applications should create and recover payments from their own trusted backend, then provide only the intended `payment_id`/checkout URL to the browser.

The current standalone PepewPay shell can still create Payment URI data locally for demonstrations, but authoritative persisted payment creation is server-to-server.

## Configuration

```text
PAYMENT_API_ENABLED=false
PAYMENT_CREATE_API_KEY=
```

Authenticated merchant operations fail closed if the configured key is absent or shorter than 32 characters.

Recommended key generation on a trusted host:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Do not commit the generated value to GitHub.

## Error behavior

Missing or incorrect authorization:

```text
401 payment_auth_required
WWW-Authenticate: Bearer
```

Payment API enabled without a usable server key:

```text
503 payment_auth_unconfigured
```

The public response never echoes the configured secret or the presented credential.

## Secret handling

The API key:

- exists only in server configuration/environment
- must not appear in Payment URI or QR data
- must not be embedded in PepewPay JavaScript
- must not be logged
- should be rotated by changing the environment value and restarting the service
- requires restrictive permissions on the backend environment file before production enablement

For the current single-host deployment, the backend `.env` should be mode `600` or otherwise restricted to the service account before a real merchant key is stored there.

## Status capability privacy

Anyone holding a valid high-entropy `payment_id` can read that payment status.

Therefore:

- treat checkout/status URLs as capability links
- do not put passwords, secrets, medical/personal records, or other sensitive merchant data in `label` or `message`
- payment IDs must not be sequential or guessable
- logs should not unnecessarily retain full capability URLs long-term

The authenticated payment list does not change this public capability model: it is a separate merchant-only enumeration surface protected by the Bearer credential.

A future multi-merchant system may add scoped API credentials or account ownership, but that complexity is not required for the current SQLite-based rollout.
