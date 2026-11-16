"""Secret store contract (§6: built-in, Vault-compatible shape) and the Postgres implementation.

Values are encrypted with Fernet using ``YORATH_SECRET_KEY``; without the key the store is
unavailable
and nothing is stored in plain text. Vault / OpenBao is another implementation of the same contract.
"""

from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yorath.control.model import Secret
from yorath.db import utcnow


class SecretStoreUnavailable(RuntimeError):
    pass


class SecretStore(Protocol):
    async def put(self, name: str, value: str) -> None: ...

    async def get(self, name: str) -> str | None: ...

    async def delete(self, name: str) -> None: ...

    async def names(self) -> list[str]: ...


class DbSecretStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], key: str | None) -> None:
        self._sessions = sessions
        self._fernet = Fernet(key.encode()) if key else None

    @property
    def available(self) -> bool:
        return self._fernet is not None

    def _f(self) -> Fernet:
        if self._fernet is None:
            raise SecretStoreUnavailable(
                "YORATH_SECRET_KEY is not set: the secret store is unavailable"
            )
        return self._fernet

    async def put(self, name: str, value: str) -> None:
        token = self._f().encrypt(value.encode()).decode()
        async with self._sessions() as s:
            row = await s.scalar(select(Secret).where(Secret.name == name))
            if row is None:
                s.add(Secret(name=name, ciphertext=token))
            else:
                row.ciphertext, row.updated_at = token, utcnow()
            await s.commit()

    async def get(self, name: str) -> str | None:
        f = self._f()
        async with self._sessions() as s:
            row = await s.scalar(select(Secret).where(Secret.name == name))
        if row is None:
            return None
        try:
            return f.decrypt(row.ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise SecretStoreUnavailable(
                f"secret {name!r} is encrypted with a different key"
            ) from exc

    async def delete(self, name: str) -> None:
        async with self._sessions() as s:
            row = await s.scalar(select(Secret).where(Secret.name == name))
            if row is not None:
                await s.delete(row)
                await s.commit()

    async def names(self) -> list[str]:
        async with self._sessions() as s:
            return list((await s.scalars(select(Secret.name).order_by(Secret.name))).all())
