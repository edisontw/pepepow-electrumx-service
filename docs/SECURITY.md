# Security

PEPEW Light is read-only during the first phases.

## Rules

1. Never receive, store, or log mnemonic, seed phrase, or private keys.
2. Keep wallet address derivation and transaction signing client-side.
3. ElectrumX must not be exposed directly to the public internet.
4. Public traffic should go through HTTPS, Nginx rate limit, and FastAPI validation.
5. Every ElectrumX call must use timeout.
6. Error messages must not expose internal file paths or sensitive configuration.
7. Avoid long-term storage of identifiable IP-address associations.
8. Future broadcast may only accept signed raw transactions.
