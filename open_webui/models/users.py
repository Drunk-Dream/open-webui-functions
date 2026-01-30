"""Minimal Open WebUI models.users module."""

from pydantic import BaseModel


class UserModel(BaseModel):
    """User model."""

    id: str
    name: str
    email: str
    role: str = "user"


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
