from app.pepepow.script import p2pkh_script_pubkey
from app.pepepow.scripthash import (
    address_to_electrumx_scripthash,
    address_to_scripthash,
    script_pubkey_to_scripthash,
    script_to_electrumx_scripthash,
)

KNOWN_P2PKH_FIXTURE = {
    "address": "PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
    "version_byte_hex": "37",
    "hash160": "bb4fe75115c3ebc7d0b0d18b3a1564d5aef1a89d",
    "script_pubkey": "76a914bb4fe75115c3ebc7d0b0d18b3a1564d5aef1a89d88ac",
    "scripthash": "3c5cab2c9f663ed292a0fdff3e681569c3f5d6741ca4b2ca4ea6281b9d4d4298",
}


def test_p2pkh_script_pubkey_known_hash160():
    hash160 = bytes.fromhex(KNOWN_P2PKH_FIXTURE["hash160"])

    assert p2pkh_script_pubkey(hash160).hex() == KNOWN_P2PKH_FIXTURE["script_pubkey"]


def test_script_to_electrumx_scripthash_known_script():
    script_pubkey = bytes.fromhex(KNOWN_P2PKH_FIXTURE["script_pubkey"])

    assert script_to_electrumx_scripthash(script_pubkey) == KNOWN_P2PKH_FIXTURE["scripthash"]
    assert script_pubkey_to_scripthash(script_pubkey) == KNOWN_P2PKH_FIXTURE["scripthash"]


def test_address_to_electrumx_scripthash_known_address():
    address = KNOWN_P2PKH_FIXTURE["address"]

    assert address_to_electrumx_scripthash(address) == KNOWN_P2PKH_FIXTURE["scripthash"]
    assert address_to_scripthash(address) == KNOWN_P2PKH_FIXTURE["scripthash"]
