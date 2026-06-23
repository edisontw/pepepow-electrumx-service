import hashlib

from .address import decode_pepew_p2pkh_address
from .script import p2pkh_script_pubkey


def script_to_electrumx_scripthash(script_pubkey: bytes) -> str:
    digest = hashlib.sha256(script_pubkey).digest()
    return digest[::-1].hex()


def address_to_electrumx_scripthash(address: str) -> str:
    _version, hash160 = decode_pepew_p2pkh_address(address)
    script_pubkey = p2pkh_script_pubkey(hash160)
    return script_to_electrumx_scripthash(script_pubkey)


# Backward-compatible aliases used by earlier Phase 0/1 code.
script_pubkey_to_scripthash = script_to_electrumx_scripthash
address_to_scripthash = address_to_electrumx_scripthash
