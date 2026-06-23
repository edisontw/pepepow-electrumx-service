import pytest

from app.pepepow.address import AddressValidationError, b58decode


def test_b58decode_rejects_empty_string():
    with pytest.raises(AddressValidationError):
        b58decode("")


def test_b58decode_rejects_invalid_character():
    with pytest.raises(AddressValidationError):
        b58decode("0")
