"""Minimal Open WebUI retrieval.vector.main module."""

from typing import Any, List, Optional

from pydantic import BaseModel


class GetResult(BaseModel):
    ids: Optional[List[List[str]]] = None
    documents: Optional[List[List[str]]] = None
    metadatas: Optional[List[List[Any]]] = None


class SearchResult(GetResult):
    distances: Optional[List[List[float | int]]] = None
