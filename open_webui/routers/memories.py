"""Minimal Open WebUI routers.memories module."""

from typing import Any

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

    content: str


class QueryMemoryForm(BaseModel):
    """Form for querying memory."""

    query: str


async def add_memory(
    form: AddMemoryForm,
    request: Request,
    user: UserModel,
    db: Session,
) -> dict[str, Any]:
    """Add a memory."""
    return {"id": "mock-mem-id", "content": form.content}


async def delete_memory_by_id(
    memory_id: str,
    request: Request,
    user: UserModel,
    db: Session,
) -> bool:
    """Delete a memory by ID."""
    return True


async def query_memory(
    form: QueryMemoryForm,
    request: Request,
    user: UserModel,
    db: Session,
) -> SearchResult:
    """Query memories."""
    return SearchResult()


async def update_memory_by_id(
    memory_id: str,
    form: MemoryUpdateModel,
    request: Request,
    user: UserModel,
    db: Session,
) -> dict[str, Any]:
    """Update a memory by ID."""
    return {"id": memory_id, "content": form.content}
