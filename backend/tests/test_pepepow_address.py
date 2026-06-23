import pytest

from app.pepepow.address import (
    InvalidAddressError,
    InvalidAddressVersionError,
    InvalidChecksumError,
    base58check_decode,
    decode_pepew_p2pkh_address,
    is_valid_pepew_address,
    validate_pepew_address,
)

KNOWN_P2PKH_FIXTURE = {
    "address": "PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
    "version_byte_hex": "37",
    "hash160": "bb4fe75115c3ebc7d0b0d18b3a1564d5aef1a89d",
    "script_pubkey": "76a914bb4fe75115c3ebc7d0b0d18b3a1564d5aef1a89d88ac",
    "scripthash": "3c5cab2c9f663ed292a0fdff3e681569c3f5d6741ca4b2ca4ea6281b9d4d4298",
}


def test_base58check_decode_known_address():
    version, hash160 = base58check_decode(KNOWN_P2PKH_FIXTURE["address"])

    assert version == int(KNOWN_P2PKH_FIXTURE["version_byte_hex"], 16)
    assert hash160.hex() == KNOWN_P2PKH_FIXTURE["hash160"]


def test_decode_pepew_p2pkh_address_known_address():
    version, hash160 = decode_pepew_p2pkh_address(KNOWN_P2PKH_FIXTURE["address"])

    assert version == 0x37
    assert hash160.hex() == KNOWN_P2PKH_FIXTURE["hash160"]
    assert validate_pepew_address(KNOWN_P2PKH_FIXTURE["address"]) is True
    assert is_valid_pepew_address(KNOWN_P2PKH_FIXTURE["address"]) is True


@pytest.mark.parametrize("address", ["", "   ", None])
def test_invalid_empty_or_non_string_address(address):
    assert is_valid_pepew_address(address) is False
    with pytest.raises(InvalidAddressError):
        decode_pepew_p2pkh_address(address)


def test_invalid_wrong_prefix_address():
    address = "1J5R5ftKGQ7o7f9Dn1BnEjt3KhzATHxgmJ"

    assert is_valid_pepew_address(address) is False
    with pytest.raises(InvalidAddressError):
        decode_pepew_p2pkh_address(address)


@pytest.mark.parametrize(
    "address",
    [
        "PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHun0",
        "PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunO",
        "PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunI",
        "PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunl",
    ],
)
def test_invalid_base58_characters(address):
    assert is_valid_pepew_address(address) is False
    with pytest.raises(InvalidAddressError):
        decode_pepew_p2pkh_address(address)


def test_invalid_too_short_address():
    assert is_valid_pepew_address("P123") is False
    with pytest.raises(InvalidAddressError):
        decode_pepew_p2pkh_address("P123")


def test_invalid_checksum_address():
    address = "PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunc"

    assert is_valid_pepew_address(address) is False
    with pytest.raises(InvalidChecksumError):
        decode_pepew_p2pkh_address(address)


def test_invalid_wrong_version_byte_address():
    address = "Pq1CDkaT2W4ruvx59VqdPm86ZxQzLmoVkn"

    assert is_valid_pepew_address(address) is False
    with pytest.raises(InvalidAddressVersionError):
        decode_pepew_p2pkh_address(address)
