"""Minimal Open WebUI models.users module."""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict
class UserSettings(BaseModel):
    ui: dict[str, object] | None = {}
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")


class UserModel(BaseModel):
    """User model."""

    id: str
    name: str
    email: str
    role: str = "user"
    settings: UserSettings | None = None


class UsersTable:
    async def get_user_by_id(
        self, id: str, db: object | None = None
    ) -> UserModel | None:
        """Get user by ID."""
        _ = db
        return UserModel(
            id=id,
            name="Test User",
            email="test@example.com",
            role="user",
        )


Users = UsersTable()
