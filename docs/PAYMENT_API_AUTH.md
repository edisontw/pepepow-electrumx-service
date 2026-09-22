# Payment API Authentication v1

Status: **selected for initial Payment Platform rollout**

## Policy

Payment creation is a merchant server-to-server action.

```http
POST /api/v1/payments
Authorization: Bearer <PAYMENT_CREATE_API_KEY>
```

Payment status is a read-only capability URL:

```http
GET /api/v1/payments/{payment_id}
```

The status endpoint does not require the merchant Bearer key. The payment ID is generated from cryptographically secure random bytes and is intended to be shared with PepewPay or a customer browser.

## Why creation is not a browser-secret flow

The static PepewPay frontend must not contain a merchant API key.

Merchant applications should create a payment from their own trusted backend, then provide the resulting `payment_id`/checkout URL to the browser.

The current standalone PepewPay shell can still create Payment URI data locally for demonstrations, but authoritative persisted payment creation is server-to-server.

## Configuration

```text
PAYMENT_API_ENABLED=false
PAYMENT_CREATE_API_KEY=
```

The create API fails closed if the configured key is absent or shorter than 32 characters.

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

A future multi-merchant system may add scoped API credentials or account ownership, but that complexity is not required for the initial SQLite-based rollout.
