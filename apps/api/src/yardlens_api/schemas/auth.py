from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

Username = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Password = Annotated[str, StringConstraints(min_length=1, max_length=256)]


class LoginRequest(BaseModel):
    username: Username
    password: Password


class UserRead(BaseModel):
    username: str
    last_login_at: datetime | None


class AuthState(BaseModel):
    user: UserRead
    access_expires_at: datetime = Field(
        description="Refresh before this; the refresh cookie is renewed on every refresh"
    )


class PasswordChange(BaseModel):
    current_password: Password
    new_password: Password
