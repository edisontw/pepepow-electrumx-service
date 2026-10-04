# Network API

Endpoint: `GET /api/network`

Purpose: provide one cached PEPEPOW network summary for low-frequency clients such as community Discord/Telegram bots and public status surfaces.

Example:

```bash
curl -s https://light.pepepow.net/api/network
```

Main fields:

- `status`: `ok`, `partial`, `stale`, or `unavailable`
- `height`: current ElectrumX chain height
- `network_hashrate_hps`: network hashrate in H/s
- `money_supply`: current on-chain supply
- `price_usdt`: cached PEPEW/USDT price
- `market_cap_usdt`: `money_supply * price_usdt` when both inputs are available
- `sources`: per-field provenance
- `failures`: fields that could not be refreshed during a partial response
- `cached`: whether the returned aggregate came from local cache

The public client talks only to PEPEW Light. The gateway keeps upstream aggregation and caching server-side.

Default settings:

```env
CACHE_NETWORK_SECONDS=120
CACHE_NETWORK_STALE_SECONDS=900
NETWORK_FETCH_TIMEOUT_SECONDS=5.0
```
