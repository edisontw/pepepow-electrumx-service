# Price API

Endpoint: `GET /api/price`

Purpose: returns cached PEPEW/USDT market price from NonKYC public ticker data.

Example:

```bash
curl -s https://light.pepepow.net/api/price
```

Main fields:

- `status`: `ok`, `stale`, or `unavailable`
- `source`: `NonKYC`
- `market`: `PEPEW/USDT`
- `price_usdt`: latest price when available
- `cached`: true when served from local cache
- `cache_ttl_seconds`: cache TTL

Default settings:

```env
CACHE_PRICE_SECONDS=120
CACHE_PRICE_STALE_SECONDS=900
PRICE_FETCH_TIMEOUT_SECONDS=5.0
```

Price data is informational only and may be delayed or temporarily unavailable.
