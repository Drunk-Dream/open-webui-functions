"""Minimal Open WebUI retrieval.vector.main module."""

from typing import Any

from pydantic import BaseModel


class GetResult(BaseModel):
    ids: list[list[str]] | None = None
    documents: list[list[str]] | None = None
    metadatas: list[list[Any]] | None = None


class SearchResult(GetResult):
    distances: list[list[float | int]] | None = None
