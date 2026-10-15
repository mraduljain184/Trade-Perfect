"""Auth schemas - mirror `User` in PROJECT_CONTEXT section 8.2."""
from pydantic import Field

from app.schemas.base import CamelModel


class UserOut(CamelModel):
    id: int
    username: str
    full_name: str
    email: str
    role: str = Field(description="Role code: TRADER / OPS_USER / OPS_MANAGER / ML_ADMIN / SYS_ADMIN")
    role_name: str
    permissions: list[str]

    @classmethod
    def from_user(cls, user) -> "UserOut":
        return cls(
            id=user.id, username=user.username, full_name=user.full_name, email=user.email,
            role=user.role.code, role_name=user.role.name, permissions=user.permission_codes,
        )


class LoginRequest(CamelModel):
    username: str = Field(min_length=1, max_length=50, examples=["priya.sharma"])
    password: str = Field(min_length=1, max_length=200, examples=["<SEED_DEFAULT_PASSWORD from your .env>"])


class LoginResponse(CamelModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Seconds until the token expires")
    user: UserOut
