from __future__ import annotations

import pytest

from hlcopy.addresses import (
    ValidationError,
    derive_address,
    normalize_address,
    normalize_private_key,
    same_address,
    verify_key_matches,
)

KEY = "4c0883a69102937d6231471b5dbb6204fe5129617082792ae468d01a3f362318"
ADDRESS = "0x2c7536E3605D9C16a7a3D7b1898e529396a65c23"


def test_private_key_normalizes_prefix_and_case():
    assert normalize_private_key("0x" + KEY.upper()) == KEY
    assert normalize_private_key("  " + KEY + "  ") == KEY


@pytest.mark.parametrize("raw", ["", "0x", "abcd", "zz" * 32, KEY + "00"])
def test_private_key_rejects_bad_input(raw):
    with pytest.raises(ValidationError) as excinfo:
        normalize_private_key(raw)
    assert excinfo.value.code == "invalid_private_key"


def test_derive_address_matches_known_key():
    assert derive_address(KEY) == ADDRESS


def test_address_normalizes_lowercase_to_checksum():
    assert normalize_address(ADDRESS.lower()) == ADDRESS
    assert normalize_address(ADDRESS[2:].upper()) == ADDRESS


def test_address_rejects_bad_checksum():
    broken = ADDRESS[:2] + ADDRESS[2:].swapcase()
    with pytest.raises(ValidationError) as excinfo:
        normalize_address(broken)
    assert excinfo.value.code == "invalid_address_checksum"


@pytest.mark.parametrize("raw", ["", "0x1234", "0x" + "g" * 40])
def test_address_rejects_non_address(raw):
    with pytest.raises(ValidationError) as excinfo:
        normalize_address(raw)
    assert excinfo.value.code == "invalid_address"


def test_same_address_is_case_insensitive():
    assert same_address(ADDRESS, ADDRESS.lower())
    assert not same_address(ADDRESS, "0x" + "1" * 40)


def test_verify_key_matches_derived_address():
    assert verify_key_matches(KEY, ADDRESS.lower()) == ADDRESS


def test_verify_key_rejects_wrong_address():
    with pytest.raises(ValidationError) as excinfo:
        verify_key_matches(KEY, "0x" + "2" * 40)
    assert excinfo.value.code == "private_key_address_mismatch"
