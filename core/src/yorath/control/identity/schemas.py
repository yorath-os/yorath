"""Identity API input and output shapes. The password and hash never leave."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

LOGIN = r"^[a-z0-9][a-z0-9._-]{1,99}$"
ROLE = r"^[a-z][a-z0-9_-]{1,49}$"
MIN_PASSWORD = 8


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    login: str
    display_name: str
    role: str
    provider: str
    department: str | None
    manager_id: uuid.UUID | None
    attributes: dict[str, Any]
    is_active: bool
    created_at: datetime


class MeOut(UserOut):
    authenticated: Literal[True] = True


class AnonymousOut(BaseModel):
    """``GET /auth/me`` without a session; ``setup_required`` while there are no users at all."""

    authenticated: Literal[False] = False
    setup_required: bool


class SetupIn(BaseModel):
    code: str | None = Field(default=None, max_length=64)
    login: str = Field(pattern=LOGIN)
    password: str = Field(min_length=MIN_PASSWORD, max_length=256)
    display_name: str = Field(min_length=1, max_length=200)


class LoginIn(BaseModel):
    login: str = Field(max_length=100)
    password: str = Field(max_length=256)


class UserCreate(BaseModel):
    login: str = Field(pattern=LOGIN)
    password: str = Field(min_length=MIN_PASSWORD, max_length=256)
    display_name: str = Field(min_length=1, max_length=200)
    role: str = Field(default="member", pattern=ROLE)
    department: str | None = Field(default=None, max_length=100)
    manager_id: uuid.UUID | None = None
    attributes: dict[str, Any] = {}


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    role: str | None = Field(default=None, pattern=ROLE)
    department: str | None = Field(default=None, max_length=100)
    manager_id: uuid.UUID | None = None
    attributes: dict[str, Any] | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=MIN_PASSWORD, max_length=256)
