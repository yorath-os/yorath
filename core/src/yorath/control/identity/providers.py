"""Identity source contract: ``users.provider`` picks the implementation; only local in the MVP."""

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.identity.passwords import verify_password
from yorath.control.model import User


class IdentityProvider(Protocol):
    name: str

    async def authenticate(self, session: AsyncSession, user: User, password: str) -> bool: ...


class LocalProvider:
    name = "local"

    async def authenticate(self, session: AsyncSession, user: User, password: str) -> bool:
        if not user.password_hash:
            return False
        ok, new_hash = verify_password(password, user.password_hash)
        if ok and new_hash:
            user.password_hash = new_hash  # transparent rehash when parameters change
        return ok


PROVIDERS: dict[str, IdentityProvider] = {"local": LocalProvider()}
