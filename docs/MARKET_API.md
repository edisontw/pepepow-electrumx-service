# Market API

Endpoint: `GET /api/market`

Purpose: provide one cached multi-source PEPEPOW market summary for community bots and public status surfaces.

Sources:

- CMC price: existing cached `https://api.pepepow.net/v1/price` service
- NonKYC price: PEPEW/USDT
- NonKYC 24h USD volume: PEPEW/USDT + PEPEW/BNB
- NestEx price and quote-volume: PEPEW/USDT via `https://api.nestex.one/cg/tickers/PEPEW_USDT`
- on-chain supply: PEPEPOW network aggregate

The Light host does not need its own CoinMarketCap API key.

Example:

```bash
curl -s https://light.pepepow.net/api/market
```

Main fields:

- `sources.cmc.price_usd`
- `sources.nonkyc.price_usd`
- `sources.nonkyc.volume_24h_usd`
- `sources.nestex.price_usd`
- `sources.nestex.volume_24h_usd`
- `total_volume_24h_usd`
- `money_supply`
- `market_cap_onchain_usd`
- `market_cap_price_source`: `CMC`
- `failures`: degraded source names
- `status`: `ok`, `partial`, `stale`, or `unavailable`

Default settings:

```env
CACHE_MARKET_SECONDS=120
CACHE_MARKET_STALE_SECONDS=900
MARKET_FETCH_TIMEOUT_SECONDS=5.0
CMC_PRICE_PROXY_URL=https://api.pepepow.net/v1/price
NONKYC_BNB_TICKER_URL=https://api.nonkyc.io/api/v2/ticker/PEPEW_BNB
NESTEX_TICKER_URL=https://api.nestex.one/cg/tickers/PEPEW_USDT
```

`/api/price` remains the lightweight canonical NonKYC endpoint for backward compatibility.
