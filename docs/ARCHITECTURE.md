# Architecture

```text
Public User
  -> HTTPS
  -> Nginx rate limit
  -> FastAPI Gateway
  -> localhost TCP ElectrumX
  -> PEPEPOWd
```

Phase 0 provides project skeleton and `/api/health`.

Phase 1 adds ElectrumX status calls.

Phase 2 adds PEPEW address validation, scriptPubKey conversion, ElectrumX scripthash lookup, and transaction history.

Phase 3 adds payment monitor statuses: waiting, seen_in_mempool, partial, paid_unconfirmed, paid_confirmed, overpaid, expired, error.
