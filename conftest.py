"""
Pytest configuration and fixtures for auto_memory testing.

Provides mock objects for Open WebUI dependencies to enable isolated testing.
"""

import pytest
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, Mock


@pytest.fixture
def test_db_engine():
    """Create in-memory SQLite engine for testing."""
    from sqlalchemy import create_engine

    engine = create_engine("sqlite:///:memory:")
    return engine


@pytest.fixture
def mock_db_session(test_db_engine):
    """Create mock database session."""
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=test_db_engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def mock_vector_db_client():
    """Mock VECTOR_DB_CLIENT with standard vector database operations."""
    client = MagicMock()

    # Mock upsert operation (no return value)
    client.upsert = MagicMock(return_value=None)

    # Mock get operation (returns empty result by default)
    get_result = Mock()
    get_result.ids = [[]]
    get_result.documents = [[]]
    get_result.metadatas = [[]]
    get_result.distances = None
    client.get = MagicMock(return_value=get_result)

    # Mock delete operation (no return value)
    client.delete = MagicMock(return_value=None)

    # Mock search operation (returns empty result by default)
    search_result = Mock()
    search_result.ids = [[]]
    search_result.documents = [[]]
    search_result.metadatas = [[]]
    search_result.distances = [[]]
    client.search = MagicMock(return_value=search_result)

    return client


@pytest.fixture
def mock_user_model():
    """Mock UserModel with standard user fields."""
    user = Mock()
    user.id = "test-user-id"
    user.name = "Test User"
    user.email = "test@example.com"
    return user


@pytest.fixture
def mock_webui_app():
    """Mock webui_app with embedding function."""
    app = MagicMock()

    # Mock embedding function that returns a 384-dimensional vector
    def mock_embedding_fn(text: str, user: Any = None) -> list[float]:
        return [0.1] * 384

    app.state.EMBEDDING_FUNCTION = MagicMock(side_effect=mock_embedding_fn)
    return app


@pytest.fixture
def mock_users():
    """Mock Users class for user operations."""
    users = MagicMock()

    # Mock get_user_by_id method
    def mock_get_user(user_id: str):
        user = Mock()
        user.id = user_id
        user.name = "Test User"
        user.email = "test@example.com"
        return user

    users.get_user_by_id = MagicMock(side_effect=mock_get_user)
    return users


@pytest.fixture
def sample_memory_metadata():
    """Sample memory metadata for testing."""
    now = datetime.now().timestamp()
    return {
        "created_at": now,
        "updated_at": now,
        "clarity": 1.0,
    }


@pytest.fixture
def sample_search_result():
    """Sample SearchResult for testing memory retrieval."""
    result = Mock()
    now = datetime.now().timestamp()

    result.ids = [["mem-1", "mem-2"]]
    result.documents = [["User likes Python", "User works at Tesla"]]
    result.metadatas = [
        [
            {"created_at": now, "updated_at": now, "clarity": 1.0},
            {"created_at": now, "updated_at": now, "clarity": 0.9},
        ]
    ]
    result.distances = [[0.1, 0.2]]

    return result
