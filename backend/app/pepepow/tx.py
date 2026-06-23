def normalize_txid(txid: str) -> str:
    value = txid.strip().lower()
    if len(value) != 64:
        raise ValueError("Transaction id must be 64 hex characters.")
    int(value, 16)
    return value
