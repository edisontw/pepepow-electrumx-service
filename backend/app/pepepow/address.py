import hashlib

BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class AddressValidationError(ValueError):
    pass


def b58decode(value: str) -> bytes:
    if not value:
        raise AddressValidationError("Address is empty.")

    number = 0
    for char in value:
        try:
            number = number * 58 + BASE58_ALPHABET.index(char)
        except ValueError as exc:
            raise AddressValidationError("Address contains invalid base58 characters.") from exc

    raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    leading_zeroes = len(value) - len(value.lstrip("1"))
    return b"\x00" * leading_zeroes + raw


def base58check_decode(address: str) -> tuple[int, bytes]:
    raw = b58decode(address)
    if len(raw) < 5:
        raise AddressValidationError("Address is too short.")

    payload, checksum = raw[:-4], raw[-4:]
    expected = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    if checksum != expected:
        raise AddressValidationError("Address checksum is invalid.")

    version = payload[0]
    body = payload[1:]
    return version, body


def validate_pepew_address(address: str, expected_prefix: str = "P") -> bool:
    if len(address) < 26 or len(address) > 64:
        raise AddressValidationError("Address length is invalid.")
    if not address.startswith(expected_prefix):
        raise AddressValidationError("Address prefix is invalid.")

    _, hash160 = base58check_decode(address)
    if len(hash160) != 20:
        raise AddressValidationError("Address payload length is invalid.")
    return True
