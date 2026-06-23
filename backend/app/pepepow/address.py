import hashlib
from typing import Final

from .constants import PEPEW_ADDRESS_PREFIX, PEPEW_P2PKH_VERSION

BASE58_ALPHABET: Final[str] = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
BASE58_INDEX: Final[dict[str, int]] = {char: index for index, char in enumerate(BASE58_ALPHABET)}


class PepepowAddressError(ValueError):
    """Base class for safe PEPEW address validation errors."""


class InvalidAddressError(PepepowAddressError):
    """Raised when an address is malformed or not PEPEW P2PKH."""


class InvalidChecksumError(PepepowAddressError):
    """Raised when Base58Check checksum validation fails."""


class InvalidAddressVersionError(PepepowAddressError):
    """Raised when the decoded address version byte is not PEPEW P2PKH."""


# Backward-compatible alias used by earlier code/tests.
AddressValidationError = PepepowAddressError


def _clean_address(address: str) -> str:
    if not isinstance(address, str):
        raise InvalidAddressError("Address must be a string.")

    value = address.strip()
    if not value:
        raise InvalidAddressError("Address is empty.")
    return value


def b58decode(value: str) -> bytes:
    value = _clean_address(value)

    number = 0
    for char in value:
        index = BASE58_INDEX.get(char)
        if index is None:
            raise InvalidAddressError("Address contains invalid base58 characters.")
        number = number * 58 + index

    raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    leading_zeroes = len(value) - len(value.lstrip("1"))
    return b"\x00" * leading_zeroes + raw


def base58check_decode(address: str) -> tuple[int, bytes]:
    value = _clean_address(address)
    raw = b58decode(value)
    if len(raw) < 5:
        raise InvalidAddressError("Address is too short.")

    payload, checksum = raw[:-4], raw[-4:]
    expected = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    if checksum != expected:
        raise InvalidChecksumError("Address checksum is invalid.")

    if not payload:
        raise InvalidAddressError("Address payload is empty.")

    version = payload[0]
    body = payload[1:]
    return version, body


def decode_pepew_p2pkh_address(address: str) -> tuple[int, bytes]:
    value = _clean_address(address)
    if not value.startswith(PEPEW_ADDRESS_PREFIX):
        raise InvalidAddressError("Address prefix is invalid.")

    version, hash160 = base58check_decode(value)
    if version != PEPEW_P2PKH_VERSION:
        raise InvalidAddressVersionError("Address version is invalid.")
    if len(hash160) != 20:
        raise InvalidAddressError("Address payload length is invalid.")
    return version, hash160


def validate_pepew_address(address: str, expected_prefix: str = PEPEW_ADDRESS_PREFIX) -> bool:
    value = _clean_address(address)
    if expected_prefix and not value.startswith(expected_prefix):
        raise InvalidAddressError("Address prefix is invalid.")
    decode_pepew_p2pkh_address(value)
    return True


def is_valid_pepew_address(address: str) -> bool:
    try:
        decode_pepew_p2pkh_address(address)
    except PepepowAddressError:
        return False
    return True
