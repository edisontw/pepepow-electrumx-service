import hashlib

from app.pepepow.script import p2pkh_script_pubkey
from app.pepepow.scripthash import script_pubkey_to_scripthash


def test_p2pkh_script_pubkey():
    hash160 = bytes.fromhex("00" * 20)
    script = p2pkh_script_pubkey(hash160)
    assert script.hex() == "76a914" + "00" * 20 + "88ac"


def test_script_pubkey_to_scripthash_reverses_sha256():
    script = bytes.fromhex("76a914" + "00" * 20 + "88ac")
    expected = hashlib.sha256(script).digest()[::-1].hex()
    assert script_pubkey_to_scripthash(script) == expected
