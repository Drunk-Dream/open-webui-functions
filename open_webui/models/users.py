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


class Users:
    """Users class for user operations."""

    @staticmethod
    def get_user_by_id(user_id: str) -> UserModel | None:
        """Get user by ID."""
        return UserModel(
            id=user_id,
            name="Test User",
            email="test@example.com",
            role="user",
        )
