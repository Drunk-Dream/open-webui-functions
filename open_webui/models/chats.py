from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class ChatListResponse(BaseModel):
    chats: list[object] = Field(default_factory=list)
    model_config: ClassVar[ConfigDict] = ConfigDict(arbitrary_types_allowed=True)


class Chats:
    """Mock implementation for testing only.

    This is a minimal mock of the real Chats model from open-webui.
    Do not use in production - it does not perform actual database operations.
    """

    @staticmethod
    def get_chats_by_user_id(user_id: str) -> ChatListResponse:
        _ = user_id
        return ChatListResponse(chats=[])

    @staticmethod
    def delete_chat_by_id_and_user_id(
        chat_id: str, user_id: str, db: object | None = None
    ) -> bool:
        """Mock delete operation - always returns True without actual deletion.

        In production, this should perform actual database deletion.
        """
        _ = (chat_id, user_id, db)
        return True
