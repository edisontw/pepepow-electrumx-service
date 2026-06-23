import hashlib

from .address import base58check_decode
from .script import p2pkh_script_pubkey


def script_pubkey_to_scripthash(script_pubkey: bytes) -> str:
    digest = hashlib.sha256(script_pubkey).digest()
    return digest[::-1].hex()


def address_to_scripthash(address: str) -> str:
    _version, hash160 = base58check_decode(address)
    script_pubkey = p2pkh_script_pubkey(hash160)
    return script_pubkey_to_scripthash(script_pubkey)
