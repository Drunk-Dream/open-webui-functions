"""Minimal Open WebUI models.users module."""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class UserSettings(BaseModel):
    ui: Optional[dict] = {}
    model_config = ConfigDict(extra="allow")


class UserModel(BaseModel):
    """User model."""

    id: str
    name: str
    email: str
    role: str = "user"
    settings: Optional[UserSettings] = None


class Users:
    """Users class for user operations."""

    @staticmethod
    def get_user_by_id(user_id: str) -> UserModel:
        """Get user by ID."""
        return UserModel(
            id=user_id,
            name="Test User",
            email="test@example.com",
            role="user",
        )
