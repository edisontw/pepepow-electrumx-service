# Security

PEPEW Light is a public ElectrumX gateway and lightweight website. It supports non-custodial wallet reads and future signed raw transaction broadcast. It must not become a custody service.

## Core rules

1. Never receive, store, log, derive, or sign with wallet recovery material.
2. Keep address derivation and transaction signing client-side in `pepepow-light-wallet`.
3. Accept only public addresses, txids, read query parameters, payment-check parameters, and signed raw transactions.
4. Keep ElectrumX private. Do not expose ElectrumX directly to the public internet.
5. Public traffic must pass through HTTPS, Nginx, rate limits, validation, and FastAPI.
6. Every ElectrumX call must use a timeout.
7. Error messages must not expose internal file paths, credentials, config values, stack traces, or raw upstream exception details.
8. Avoid long-term storage of identifiable IP/address associations.
9. Future broadcast must only accept signed raw transactions.
10. The wallet UI must display a clear non-custodial warning.

## Allowed API data

Allowed:

- PEPEW address
- txid
- pagination parameters
- cache/fresh query flag
- payment amount and expiry metadata for read-only payment checks
- signed raw transaction for broadcast

Not allowed:

- wallet recovery phrase
- signing secret material
- wallet password
- server-side wallet import payload
- custodial account model

## Backend hardening checklist

- Validate every address before ElectrumX lookup.
- Validate txid format before transaction lookup.
- Bound pagination and payload sizes.
- Set ElectrumX socket/connect/read timeouts.
- Map upstream errors to stable public error codes.
- Keep logs operational and short-lived.
- Do not log full request bodies for broadcast.
- Use Nginx rate limiting for public API routes.
- Keep Uvicorn bound to `127.0.0.1`.
- Keep ElectrumX bound to `127.0.0.1` or private interface.

## Broadcast-specific rules

`POST /api/wallet/broadcast` may exist only as a network submission endpoint.

It may:

- validate raw transaction shape and size
- submit the signed raw transaction to ElectrumX / node
- return txid or stable rejection code

It must not:

- accept wallet recovery material
- construct unsigned transactions on the server for user funds
- sign transactions
- store pending wallet sessions
- silently alter transaction outputs

## Privacy

Address lookup is public-chain data, but request metadata can still be sensitive.

Recommended behavior:

- avoid analytics on wallet pages
- avoid long-lived request logs that bind IP and address
- avoid user identity systems for the public web wallet unless explicitly required later
- keep payment monitor address-level and stateless unless a real invoice system is designed later

## Incident response priorities

If a suspected issue occurs:

1. Disable or rate-limit affected public route.
2. Keep `/api/status` and `/api/health` available if safe.
3. Preserve operational logs needed for diagnosis.
4. Do not publish internal secrets, stack traces, or host paths.
5. If wallet UI may be compromised, remove `/wallet/` static serving until verified.
6. Re-deploy wallet from a known safe commit.
