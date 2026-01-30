"""Minimal Open WebUI retrieval.vector.main module."""

from typing import Any


class SearchResult:
    """Search result from vector database."""

    def __init__(self):
        self.ids: list[list[str]] = [[]]
        self.documents: list[list[str]] = [[]]
        self.metadatas: list[list[dict[str, Any]]] = [[]]
        self.distances: list[list[float]] | None = None
