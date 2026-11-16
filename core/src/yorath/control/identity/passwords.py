"""Argon2id via pwdlib (OWASP recommendation; the same choice as the FastAPI docs)."""

from pwdlib import PasswordHash

_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> tuple[bool, str | None]:
    """(is the password right, a new hash if the parameters are outdated and it needs resaving)."""
    return _hasher.verify_and_update(password, password_hash)
