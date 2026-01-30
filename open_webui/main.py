"""Minimal Open WebUI main module."""

from unittest.mock import AsyncMock, MagicMock


class MockWebUIApp:
    """Mock WebUI application."""

    def __init__(self):
        self.state = MagicMock()
        self.state.EMBEDDING_FUNCTION = AsyncMock(return_value=[0.1] * 384)


app = MockWebUIApp()
