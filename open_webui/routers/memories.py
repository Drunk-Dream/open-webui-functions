"""Minimal Open WebUI routers.memories module."""

from typing import Any, Optional

from fastapi import Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from open_webui.models.users import UserModel
from open_webui.retrieval.vector.main import SearchResult


class AddMemoryForm(BaseModel):
    """Form for adding memory."""

    content: str


class MemoryUpdateModel(BaseModel):
    """Model for updating memory."""

    content: Optional[str] = None


class QueryMemoryForm(BaseModel):
    """Form for querying memory."""

    content: str
    k: Optional[int] = 1


async def add_memory(
    request: Request,
    form_data: AddMemoryForm,
    user: UserModel,
    db: Optional[Session] = None,
) -> dict[str, Any]:
    """Add a memory."""
    return {"id": "mock-mem-id", "content": form_data.content}


async def delete_memory_by_id(
    memory_id: str,
    request: Request,
    user: UserModel,
    db: Optional[Session] = None,
) -> bool:
    """Delete a memory by ID."""
    return True


async def query_memory(
    request: Request,
    form_data: QueryMemoryForm,
    user: UserModel,
    db: Optional[Session] = None,
) -> SearchResult:
    """Query memories."""
    return SearchResult()


async def update_memory_by_id(
    memory_id: str,
    request: Request,
    form_data: MemoryUpdateModel,
    user: UserModel,
    db: Optional[Session] = None,
) -> dict[str, Any]:
    """Update a memory by ID."""
    return {"id": memory_id, "content": form_data.content}
