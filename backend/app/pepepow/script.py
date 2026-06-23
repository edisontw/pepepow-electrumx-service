from .constants import P2PKH_SCRIPT_PREFIX, P2PKH_SCRIPT_SUFFIX


def p2pkh_script_pubkey(hash160: bytes) -> bytes:
    if len(hash160) != 20:
        raise ValueError("P2PKH hash160 must be 20 bytes.")
    return P2PKH_SCRIPT_PREFIX + hash160 + P2PKH_SCRIPT_SUFFIX
