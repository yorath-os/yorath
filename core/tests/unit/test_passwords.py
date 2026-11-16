import pytest

from yorath.control.identity.passwords import hash_password, verify_password
from yorath.control.identity.service import _hash_token


@pytest.mark.covers("INV-ID-4")
def test_argon2_hash_roundtrip():
    h = hash_password("correct horse battery")
    assert h.startswith("$argon2id$") and "correct" not in h
    ok, rehash = verify_password("correct horse battery", h)
    assert ok and rehash is None
    assert verify_password("wrong", h)[0] is False


@pytest.mark.covers("INV-ID-4")
def test_hashes_are_salted():
    assert hash_password("same") != hash_password("same")


@pytest.mark.covers("INV-ID-1")
def test_session_token_is_hashed_with_sha256():
    assert _hash_token("abc") != "abc" and len(_hash_token("abc")) == 64
