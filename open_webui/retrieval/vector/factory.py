"""Minimal Open WebUI retrieval.vector.factory module."""

from typing import Any


class MockVectorDBClient:
    """Mock vector database client."""

    def __init__(self):
        self._data: dict[str, list[dict]] = {}

    def upsert(self, collection_name: str, items: list[dict]) -> None:
        """Upsert items into collection."""
        if collection_name not in self._data:
            self._data[collection_name] = []
        self._data[collection_name].extend(items)

    def get(self, collection_name: str) -> Any:
        """Get all items from collection."""
        from open_webui.retrieval.vector.main import SearchResult

        result = SearchResult()
        if collection_name in self._data:
            items = self._data[collection_name]
            result.ids = [[item.get("id", "") for item in items]]
            result.documents = [[item.get("text", "") for item in items]]
            result.metadatas = [[item.get("metadata", {}) for item in items]]
        return result

    def delete(self, collection_name: str, ids: list[str]) -> None:
        """Delete items from collection."""
        if collection_name in self._data:
            self._data[collection_name] = [
                item
                for item in self._data[collection_name]
                if item.get("id") not in ids
            ]

    def search(self, collection_name: str, query: str, n_results: int = 5) -> Any:
        """Search collection."""
        return self.get(collection_name)


VECTOR_DB_CLIENT = MockVectorDBClient()
