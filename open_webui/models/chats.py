from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field
class ChatListResponse(BaseModel):
    items: list[object] = Field(default_factory=list)
    total: int = Field(default=0)
    model_config: ClassVar[ConfigDict] = ConfigDict(arbitrary_types_allowed=True)


class ChatTable:
    async def get_chats_by_user_id(
        self,
        user_id: str,
        filter: dict[str, object] | None = None,
        skip: int | None = None,
        limit: int | None = None,
        db: object | None = None,
    ) -> ChatListResponse:
        _ = (filter, skip, limit, db)
        return ChatListResponse(items=[], total=0)

    async def delete_chat_by_id_and_user_id(
        self, id: str, user_id: str, db: object | None = None
    ) -> bool:
        _ = (id, user_id, db)
        return True


Chats = ChatTable()
