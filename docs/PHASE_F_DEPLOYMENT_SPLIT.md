# Phase F — Dedicated Payment Platform Host

Status: **PLANNED — current-host production rollout verified; split not started**

This document defines the staged migration from the verified single-host rollout on
`light.pepepow.net` to a dedicated Payment Platform host, expected to use:

```text
https://pay.pepepow.net
```

GitHub `main` remains the implementation source of truth. Do not execute the
cutover until the new host has been inspected and the private ElectrumX network
path has been selected.

## 1. Why split

The current production rollout is functionally verified on the existing
PEPEPOW/ElectrumX host. The split is for:

- CPU and failure isolation from PEPEPOWd/ElectrumX
- a narrower public Payment Platform boundary
- independent webhook/network operations
- simpler future merchant scaling
- easier Payment Platform maintenance without disturbing chain services

The split is not currently driven by RAM pressure and does not justify adding
Redis, PostgreSQL, RabbitMQ, Kafka, or similar infrastructure.

## 2. Current verified production state

Current host:

```text
light.pepepow.net
```

Verified on 2026-09-22:

- Payment API authorization and persisted payment creation
- SQLite payment/event state
- persistent ElectrumX watcher
- real wallet broadcast and transaction detection
- true confirmation tracking
- PepewPay static production UI
- live 0.1 PEPEW checkout through `paid_unconfirmed` -> `paid_confirmed`
- webhook SSRF rejection
- real webhook HMAC verification
- persisted HTTP 503 retry -> HTTP 204 delivered
- stable event ID, delivery ID, and event body across retry
- authenticated delivery log without secret exposure

Production currently keeps:

```text
PAYMENT_API_ENABLED=true
PAYMENT_WATCHER_ENABLED=true
PAYMENT_WEBHOOK_ENABLED=true
```

Repository defaults remain disabled.

## 3. Target architecture

```text
Internet
   |
   +-- light.pepepow.net
   |     VM-A
   |     - PEPEPOWd
   |     - ElectrumX
   |     - PEPEW Light API
   |     - existing web wallet
   |
   +-- pay.pepepow.net
         VM-B
         - Nginx
         - PepewPay static UI
         - Payment API
         - payment watcher
         - SQLite payment/event state
         - webhook worker
                |
                +-- approved private path --> ElectrumX on VM-A
```

ElectrumX must never be exposed to the public Internet.

## 4. Authority boundary

After cutover there must be exactly one authoritative Payment Platform writer.

VM-B becomes authoritative for:

```text
payments.sqlite3
Payment API create
payment watcher
payment events
webhook endpoints
webhook deliveries
```

VM-A must not continue running a second enabled payment watcher or webhook
worker against a divergent SQLite database.

PEPEW Light's legacy address-level APIs may remain on VM-A.

## 5. Private ElectrumX connectivity decision gate

Do not open TCP 50001 publicly.

Choose one approved path after inspecting the new host/network:

### Option A — OCI private VCN path

Use when both hosts have a controlled private OCI route.

Requirements:

- ElectrumX listens only on localhost and/or the required private interface
- OCI NSG/security rules allow TCP 50001 only from VM-B's private IP
- host firewall applies the same restriction
- public ingress to 50001 remains blocked
- validate from VM-B and independently verify public refusal

This is operationally simple when both VMs share an appropriate private network.

### Option B — controlled SSH tunnel

Keep ElectrumX bound to `127.0.0.1:50001` on VM-A and create a persistent,
restricted tunnel from VM-B.

Requirements:

- dedicated key/user or equivalent restricted SSH policy
- tunnel restricted to the ElectrumX destination
- systemd-managed reconnect/restart behavior
- VM-B Payment Platform connects to a local tunnel endpoint
- no shell/forwarding permissions beyond what is required

Prefer the simplest option that preserves the private boundary after the actual
network topology is known.

## 5.1 Observed VM-B baseline (2026-09-22)

Candidate dedicated Payment Platform host:

```text
hostname: pepepow-nm2-ub3
OS: Ubuntu 22.04.2 LTS
architecture: aarch64
CPU: 1 core
RAM: 5.8 GiB
swap: none
root filesystem: ~45 GiB, ~29 GiB available
Python: 3.10.12
private address: 10.0.0.95/24
timezone: UTC
NTP: synchronized
```

Current observed services/ports:

```text
22/tcp        SSH
8833/tcp      PEPEPOWd P2P
8834/tcp      PEPEPOWd RPC, loopback only
111/tcp/udp   rpcbind
```

Nginx is not installed. Git and Node.js were not reported by the audit command;
confirm Git explicitly before repository deployment. Node.js is not required on
this host for PepewPay because static assets are built in CI.

The host currently runs PEPEPOWd. For the initial low-traffic rollout, keep this node running rather than removing it. This means VM-B is a separate Payment Platform host from the ElectrumX/Light host, but is not a payment-only machine. Revisit removal only if CPU/failure isolation becomes operationally necessary.

VM-A was subsequently confirmed at `10.0.0.132/24`, while VM-B is `10.0.0.95/24`; both report `10.0.0.0/24`. However, an actual VM-B -> VM-A TCP/22 private-path probe failed on 2026-09-22. Therefore matching CIDRs must not be treated as proof that the instances share one reachable OCI VCN/subnet. Keep ElectrumX localhost-only until VCN/subnet identity and reachability are verified. If the instances are in separate overlapping-CIDR VCNs, prefer the controlled SSH-tunnel option rather than attempting unsafe/public ElectrumX exposure.

## 6. New-host inspection before deployment

Before installing the Payment Platform on VM-B, record:

- OS/version and architecture
- CPU, RAM, disk, swap
- Python and Git versions
- whether Node.js is installed; do not install it merely for PepewPay
- public/private IP and OCI VCN/NSG topology
- current listening ports
- existing Nginx/systemd services
- DNS readiness for `pay.pepepow.net`
- TLS/certbot availability
- firewall state
- time synchronization/timezone
- available disk path for SQLite and backups

No production secret should be printed during the audit.

## 7. VM-B deployment shape

Use the existing repository and Python 3.10-compatible backend.

Suggested filesystem boundaries:

```text
/home/ubuntu/pepepow-electrumx-service
/var/lib/pepew-pay/payments.sqlite3
/var/www/pepewpay
```

Exact systemd `StateDirectory` names and paths should be finalized before
deployment and documented in the repo.

PepewPay remains static. Production does not require Node.js. Deploy a CI-built
artifact from `pepepow-devkit/pepewpay-dist` using a repository-scoped
read-only deploy key.

## 8. PepewPay API/domain handling

The current PepewPay browser default is:

```text
https://light.pepepow.net/api
```

Before making `pay.pepepow.net` authoritative, rebuild/configure PepewPay so
persisted checkout polling points to the intended Payment API on VM-B, preferably
same-origin:

```text
https://pay.pepepow.net/api
```

Do not silently leave production checkout status reads pointed at VM-A after the
SQLite authority moves to VM-B.

The native/web-wallet handoff can continue to use the existing PEPEW Light
wallet unless a separate wallet migration is intentionally planned.

## 9. Secret policy during split

Do not copy secrets through shell history, GitHub, chat, or logs.

Because the platform is not yet serving established third-party merchant
webhook endpoints, the preferred cutover is to rotate on VM-B:

- create a new merchant API key
- create a new webhook master key
- keep VM-B environment file mode `0600`

If real merchant integrations exist before migration, plan credential continuity
and signing-secret rotation explicitly rather than rotating silently.

Never copy mnemonic/private keys; Payment Platform does not need them.

## 10. SQLite migration

The existing SQLite database contains authoritative payment/event history and
must not be copied while two independent writers continue to diverge.

Staged migration:

1. deploy VM-B software with Payment API/watcher/webhook disabled
2. establish and test private ElectrumX connectivity
3. verify VM-B backend tests
4. verify Nginx/TLS and a non-authoritative health endpoint
5. choose a short maintenance/cutover window
6. stop or explicitly disable Payment API/watcher/webhook writes on VM-A
7. create a consistent SQLite backup/snapshot
8. transfer the snapshot to VM-B through an authenticated encrypted path
9. verify database integrity and ownership/permissions
10. start VM-B with the selected production feature gates
11. rerun payment and webhook E2E
12. only then route production checkout/API traffic to VM-B

Do not rsync/copy a live SQLite database file casually while authoritative
writers remain active.

## 11. DNS and routing cutover

Preferred public boundary after migration:

```text
https://pay.pepepow.net/
https://pay.pepepow.net/api/v1/payments
https://pay.pepepow.net/api/v1/webhook-endpoints
```

Maintain compatibility deliberately. Existing
`https://light.pepepow.net/pay/?payment_id=...` links must not simply break.

Before cutover, choose one compatibility strategy:

- keep `light.pepepow.net/pay/` serving a compatible frontend that reads the
  new API; or
- redirect only when capability-link semantics are preserved and tested.

Do not redirect API endpoints blindly if merchant clients depend on them.

## 12. Cutover validation

Minimum acceptance on VM-B:

- backend tests pass on the actual Python runtime
- public health endpoint passes
- Payment API unauthenticated create -> 401
- authenticated create -> 201
- read-only status GET works
- ElectrumX watcher reconnect/subscription works through the private path
- a new small real payment reaches `paid_unconfirmed` then `paid_confirmed`
- PepewPay shows the same live transition at `pay.pepepow.net`
- webhook production E2E passes again
- SSRF loopback/private target rejection still passes
- no secret appears in logs
- public scans cannot reach ElectrumX or PEPEPOWd RPC
- VM-A payment watcher/webhook writer is disabled after authority moves

## 13. Rollback

Keep VM-A's previous SQLite state and configuration intact during the initial
cutover window, but do not run it concurrently as a second writer.

If VM-B validation fails:

1. stop Payment Platform writers on VM-B
2. restore VM-A as the single authoritative writer
3. restore public routing to VM-A
4. reconcile any VM-B-created payment/event rows before attempting another
   migration

Rollback must preserve one-writer authority.

## 14. Phase F completion criteria

Phase F is complete when:

- `pay.pepepow.net` is deployed on the dedicated host
- Payment API, watcher, SQLite and webhook worker are authoritative on VM-B
- ElectrumX remains reachable only through an approved private path
- PepewPay uses the VM-B Payment API
- payment and webhook production E2E pass after migration
- VM-A no longer runs competing Payment Platform writers
- rollback and backup procedures are documented and tested
