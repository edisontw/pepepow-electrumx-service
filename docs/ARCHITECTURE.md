# Architecture

For detailed Payment Platform phases and current progress, see [PAYMENT_PLATFORM_ROADMAP.md](PAYMENT_PLATFORM_ROADMAP.md).

## Current production path

```text
Public User
  -> HTTPS
  -> Nginx rate limit
  -> FastAPI Gateway
  -> localhost TCP ElectrumX
  -> PEPEPOWd
```

Current production keeps PEPEPOWd RPC, ElectrumX, and FastAPI on localhost/private boundaries. The public entrypoint is Nginx.

The existing `/api/payment/check` endpoint is a stateless address-level monitor. It is retained for compatibility and simple checks, but it is not an invoice database or merchant payment authority.

## Repository boundaries

```text
electrumx-pepepow
  ElectrumX + PEPEPOW chain/indexing support

pepepow-electrumx-service
  FastAPI gateway
  ElectrumX access
  cache
  payment/event backend
  webhook infrastructure
  deployment

pepepow-light-wallet
  client-side non-custodial wallet
  mnemonic/derivation
  transaction construction/signing
  wallet UI

pepepow-devkit
  pepew-js
  PEPEW Payment URI
  PepewPay
  examples/test vectors
```

Mnemonic, private keys, derivation secrets, and signing material remain client-side.

## Payment Platform direction

```text
pepew-js
  -> PEPEW Payment URI
  -> PepewPay
  -> transaction-level Payment/Event Gateway
  -> Webhook
```

The authoritative payment backend is transaction-level. It must not infer merchant payment state only from an address's current balance.

Target logical path:

```text
Wallet / PepewPay / Merchant
          |
          v
     Payment API
          |
          v
       SQLite
   payment + event state
      /         \
     v           v
Payment watcher  Webhook worker
     |
     v
private ElectrumX
```

The watcher should centralize blockchain observation so browser refreshes do not each become equivalent ElectrumX polling loops.

SQLite is the initial persistence choice. Additional infrastructure such as Redis, PostgreSQL, RabbitMQ, or Kafka should be introduced only when justified by actual operational requirements.

## Deployment direction

The current Oracle Cloud host is ARM64, single-core, and resource-constrained. PEPEPOWd, ElectrumX, PEPEW Light, and Nginx already share that host.

Code may be developed in the existing repositories before deployment is split. When payment watching and webhook delivery become active production workloads, a second VM is preferred for CPU, failure, and security isolation.

ElectrumX must remain non-public. Cross-VM access, if introduced, must use an approved private network path or controlled tunnel.
