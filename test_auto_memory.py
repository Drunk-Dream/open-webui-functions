"""
Unit tests for auto_memory expired_at mechanism.

Tests cover:
- MemoryExpiry model definition
- Initial expiry time calculation
- Expiry extension calculation
- Expired memories query logic
- Boost memories logic
- Cleanup expired memories logic
"""

import time
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest


# Mock open_webui dependencies before importing auto_memory
@pytest.fixture(autouse=True)
def mock_open_webui_imports():
    """Mock all open_webui imports to allow testing without the full environment."""
    mock_base = MagicMock()
    mock_engine = MagicMock()
    mock_get_db_context = MagicMock()
    mock_webui_app = MagicMock()
    mock_users = MagicMock()
    mock_user_model = MagicMock()
    mock_search_result = MagicMock()
    mock_vector_db = MagicMock()

    # Create a context manager mock for get_db_context
    mock_db_session = MagicMock()
    mock_get_db_context.return_value.__enter__ = Mock(return_value=mock_db_session)
    mock_get_db_context.return_value.__exit__ = Mock(return_value=False)

    with patch.dict(
        "sys.modules",
        {
            "open_webui": MagicMock(),
            "open_webui.internal": MagicMock(),
            "open_webui.internal.db": MagicMock(
                Base=mock_base,
                engine=mock_engine,
                get_db_context=mock_get_db_context,
            ),
            "open_webui.main": MagicMock(app=mock_webui_app),
            "open_webui.models": MagicMock(),
            "open_webui.models.users": MagicMock(
                UserModel=mock_user_model,
                Users=mock_users,
            ),
            "open_webui.retrieval": MagicMock(),
            "open_webui.retrieval.vector": MagicMock(),
            "open_webui.retrieval.vector.main": MagicMock(
                SearchResult=mock_search_result,
            ),
            "open_webui.retrieval.vector.factory": MagicMock(
                VECTOR_DB_CLIENT=mock_vector_db,
            ),
            "open_webui.routers": MagicMock(),
            "open_webui.routers.memories": MagicMock(
                AddMemoryForm=MagicMock(),
                MemoryUpdateModel=MagicMock(),
                QueryMemoryForm=MagicMock(),
                add_memory=AsyncMock(),
                delete_memory_by_id=AsyncMock(),
                query_memory=AsyncMock(),
                update_memory_by_id=AsyncMock(),
            ),
        },
    ):
        yield


def test_memory_expiry_model(test_db_engine):
    """Test MemoryExpiry model definition and CRUD operations."""
    from sqlalchemy import BigInteger, Column, String
    from sqlalchemy.orm import Session, sessionmaker

    # Import after mocking
    from auto_memory import Base, MemoryExpiry

    # Create table
    MemoryExpiry.metadata.create_all(test_db_engine)

    # Create session
    SessionLocal = sessionmaker(bind=test_db_engine)
    session: Session = SessionLocal()

    try:
        # Test: Insert record
        now = int(time.time())
        expiry = MemoryExpiry(
            mem_id="test-mem-id",
            user_id="test-user-id",
            expired_at=now + 86400,  # 1 day from now
            created_at=now,
            updated_at=now,
        )
        session.add(expiry)
        session.commit()

        # Test: Query record
        result = session.query(MemoryExpiry).filter_by(mem_id="test-mem-id").first()
        assert result is not None
        assert result.mem_id == "test-mem-id"
        assert result.user_id == "test-user-id"
        assert result.expired_at == now + 86400
        assert result.created_at == now
        assert result.updated_at == now

        # Test: Update record
        result.expired_at = now + 172800  # 2 days from now
        result.updated_at = now + 100
        session.commit()

        updated = session.query(MemoryExpiry).filter_by(mem_id="test-mem-id").first()
        assert updated is not None
        assert updated.expired_at == now + 172800
        assert updated.updated_at == now + 100

        # Test: Delete record
        session.delete(updated)
        session.commit()

        deleted = session.query(MemoryExpiry).filter_by(mem_id="test-mem-id").first()
        assert deleted is None

    finally:
        session.close()


def test_calculate_expiry_initial():
    """Test initial expiry time calculation based on initial_expiry_days valve."""
    from auto_memory import Filter

    filter_instance = Filter()
    now = int(time.time())

    # Default initial_expiry_days = 30
    assert filter_instance.valves.initial_expiry_days == 30

    # Calculate expected expiry
    expected_expired_at = now + (30 * 86400)
    calculated = now + (filter_instance.valves.initial_expiry_days * 86400)

    # Allow 2 second tolerance for timing
    assert abs(calculated - expected_expired_at) < 2


def test_calculate_expiry_initial_custom():
    """Test initial expiry calculation with custom valve value."""
    from auto_memory import Filter

    filter_instance = Filter()
    filter_instance.valves.initial_expiry_days = 7  # Custom: 7 days

    now = int(time.time())
    expected_expired_at = now + (7 * 86400)
    calculated = now + (filter_instance.valves.initial_expiry_days * 86400)

    assert abs(calculated - expected_expired_at) < 2


def test_calculate_expiry_extension():
    """Test expiry extension calculation based on extension_days valve."""
    from auto_memory import Filter

    filter_instance = Filter()
    now = int(time.time())

    # Default extension_days = 14
    assert filter_instance.valves.extension_days == 14

    # Calculate expected extension
    expected_expired_at = now + (14 * 86400)
    calculated = now + (filter_instance.valves.extension_days * 86400)

    # Allow 2 second tolerance
    assert abs(calculated - expected_expired_at) < 2


def test_calculate_expiry_extension_custom():
    """Test expiry extension calculation with custom valve value."""
    from auto_memory import Filter

    filter_instance = Filter()
    filter_instance.valves.extension_days = 7  # Custom: 7 days

    now = int(time.time())
    expected_expired_at = now + (7 * 86400)
    calculated = now + (filter_instance.valves.extension_days * 86400)

    assert abs(calculated - expected_expired_at) < 2


def test_expired_memories_query(test_db_engine):
    """Test querying expired memories from MemoryExpiryTable."""
    from sqlalchemy.orm import sessionmaker

    from auto_memory import MemoryExpiry, MemoryExpiryTable

    # Create table
    MemoryExpiry.metadata.create_all(test_db_engine)

    # Create session
    SessionLocal = sessionmaker(bind=test_db_engine)
    session = SessionLocal()

    try:
        now = int(time.time())
        user_id = "test-user-id"

        # Insert test records
        # Record 1: Expired (expired_at < now)
        expired1 = MemoryExpiry(
            mem_id="expired-mem-1",
            user_id=user_id,
            expired_at=now - 86400,  # Expired 1 day ago
            created_at=now - 172800,
            updated_at=now - 172800,
        )
        # Record 2: Expired (expired_at < now)
        expired2 = MemoryExpiry(
            mem_id="expired-mem-2",
            user_id=user_id,
            expired_at=now - 3600,  # Expired 1 hour ago
            created_at=now - 86400,
            updated_at=now - 86400,
        )
        # Record 3: Not expired (expired_at > now)
        not_expired = MemoryExpiry(
            mem_id="valid-mem-1",
            user_id=user_id,
            expired_at=now + 86400,  # Expires in 1 day
            created_at=now,
            updated_at=now,
        )
        # Record 4: Different user (should not be returned)
        other_user = MemoryExpiry(
            mem_id="other-user-mem",
            user_id="other-user-id",
            expired_at=now - 86400,  # Expired
            created_at=now - 172800,
            updated_at=now - 172800,
        )

        session.add_all([expired1, expired2, not_expired, other_user])
        session.commit()

        # Test: Query expired memories for user
        expired_records = (
            session.query(MemoryExpiry)
            .filter(
                MemoryExpiry.user_id == user_id,
                MemoryExpiry.expired_at < now,
            )
            .all()
        )

        # Should return only 2 expired records for the test user
        assert len(expired_records) == 2
        expired_ids = {r.mem_id for r in expired_records}
        assert expired_ids == {"expired-mem-1", "expired-mem-2"}

        # Test: Query with future timestamp (all should be expired)
        future_time = now + 172800  # 2 days from now
        all_expired = (
            session.query(MemoryExpiry)
            .filter(
                MemoryExpiry.user_id == user_id,
                MemoryExpiry.expired_at < future_time,
            )
            .all()
        )
        assert len(all_expired) == 3  # All 3 records for test user

    finally:
        session.close()


def test_expired_memories_query_empty(test_db_engine):
    """Test querying expired memories when none exist."""
    from sqlalchemy.orm import sessionmaker

    from auto_memory import MemoryExpiry

    # Create table
    MemoryExpiry.metadata.create_all(test_db_engine)

    SessionLocal = sessionmaker(bind=test_db_engine)
    session = SessionLocal()

    try:
        now = int(time.time())
        user_id = "test-user-id"

        # Insert only non-expired records
        not_expired = MemoryExpiry(
            mem_id="valid-mem-1",
            user_id=user_id,
            expired_at=now + 86400,
            created_at=now,
            updated_at=now,
        )
        session.add(not_expired)
        session.commit()

        # Query expired - should return empty
        expired_records = (
            session.query(MemoryExpiry)
            .filter(
                MemoryExpiry.user_id == user_id,
                MemoryExpiry.expired_at < now,
            )
            .all()
        )

        assert len(expired_records) == 0

    finally:
        session.close()


@pytest.mark.asyncio
async def test_boost_memories_create_new(mock_user_model):
    """Test boost_memories creates new expiry records for memories without existing records."""
    from auto_memory import Filter, Memory, MemoryExpiryTable

    filter_instance = Filter()

    # Create mock memories
    now = int(time.time())
    mock_memories = [
        Memory(
            mem_id="mem-1",
            created_at=now,
            update_at=now,
            content="test memory 1",
        ),
        Memory(
            mem_id="mem-2",
            created_at=now,
            update_at=now,
            content="test memory 2",
        ),
    ]

    # Mock MemoryExpiryTable
    with patch("auto_memory.MemoryExpiryTable") as MockTable:
        mock_table = MockTable.return_value
        mock_table.get_by_mem_id.return_value = None  # No existing record
        mock_table.insert.return_value = MagicMock()

        # Call boost_memories
        stats = await filter_instance.boost_memories(mock_memories, mock_user_model)

        # Verify stats
        assert stats["total"] == 2
        assert stats["created"] == 2
        assert stats["boosted"] == 0

        # Verify insert was called for each memory
        assert mock_table.insert.call_count == 2


@pytest.mark.asyncio
async def test_boost_memories_update_existing(mock_user_model):
    """Test boost_memories updates expiry for memories with existing records."""
    from auto_memory import Filter, Memory, MemoryExpiry, MemoryExpiryTable

    filter_instance = Filter()

    now = int(time.time())
    mock_memories = [
        Memory(
            mem_id="mem-1",
            created_at=now,
            update_at=now,
            content="test memory 1",
        ),
    ]

    # Mock existing expiry record
    existing_expiry = MagicMock(spec=MemoryExpiry)
    existing_expiry.mem_id = "mem-1"
    existing_expiry.expired_at = now + 86400

    with patch("auto_memory.MemoryExpiryTable") as MockTable:
        mock_table = MockTable.return_value
        mock_table.get_by_mem_id.return_value = existing_expiry
        mock_table.update_expired_at.return_value = existing_expiry

        stats = await filter_instance.boost_memories(mock_memories, mock_user_model)

        assert stats["total"] == 1
        assert stats["boosted"] == 1
        assert stats["created"] == 0

        # Verify update was called
        mock_table.update_expired_at.assert_called_once()


@pytest.mark.asyncio
async def test_boost_memories_empty_list(mock_user_model):
    """Test boost_memories handles empty memory list."""
    from auto_memory import Filter

    filter_instance = Filter()

    stats = await filter_instance.boost_memories([], mock_user_model)

    assert stats["total"] == 0
    assert stats["boosted"] == 0
    assert stats["created"] == 0


@pytest.mark.asyncio
async def test_boost_memories_mixed(mock_user_model):
    """Test boost_memories with mix of new and existing memories."""
    from auto_memory import Filter, Memory, MemoryExpiry

    filter_instance = Filter()

    now = int(time.time())
    mock_memories = [
        Memory(mem_id="new-mem", created_at=now, update_at=now, content="new"),
        Memory(
            mem_id="existing-mem", created_at=now, update_at=now, content="existing"
        ),
    ]

    existing_expiry = MagicMock(spec=MemoryExpiry)
    existing_expiry.mem_id = "existing-mem"

    with patch("auto_memory.MemoryExpiryTable") as MockTable:
        mock_table = MockTable.return_value

        # Return None for new-mem, existing record for existing-mem
        def get_by_mem_id_side_effect(mem_id):
            if mem_id == "existing-mem":
                return existing_expiry
            return None

        mock_table.get_by_mem_id.side_effect = get_by_mem_id_side_effect
        mock_table.insert.return_value = MagicMock()
        mock_table.update_expired_at.return_value = existing_expiry

        stats = await filter_instance.boost_memories(mock_memories, mock_user_model)

        assert stats["total"] == 2
        assert stats["created"] == 1
        assert stats["boosted"] == 1


@pytest.mark.asyncio
async def test_cleanup_expired_memories(mock_user_model):
    """Test cleanup_expired_memories deletes expired memories."""
    from auto_memory import Filter, MemoryExpiry

    filter_instance = Filter()

    # Create mock expired records
    mock_expired = [
        MagicMock(spec=MemoryExpiry, mem_id="expired-1"),
        MagicMock(spec=MemoryExpiry, mem_id="expired-2"),
    ]

    with (
        patch("auto_memory.MemoryExpiryTable") as MockTable,
        patch.object(filter_instance, "_delete_memory_sync") as mock_delete,
        patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread,
    ):
        mock_table = MockTable.return_value
        mock_table.get_expired.return_value = mock_expired
        mock_table.delete_by_mem_id.return_value = True

        # get_related_memories actually performs cleanup
        stats = await filter_instance.get_related_memories(user=mock_user_model)

        assert stats["total"] == 2
        assert stats["deleted"] == 2


@pytest.mark.asyncio
async def test_cleanup_expired_memories_empty(mock_user_model):
    """Test cleanup_expired_memories when no expired memories exist."""
    from auto_memory import Filter

    filter_instance = Filter()

    with patch("auto_memory.MemoryExpiryTable") as MockTable:
        mock_table = MockTable.return_value
        mock_table.get_expired.return_value = []

        stats = await filter_instance.get_related_memories(user=mock_user_model)

        assert stats["total"] == 0
        assert stats["deleted"] == 0


@pytest.mark.asyncio
async def test_cleanup_expired_memories_partial_failure(mock_user_model):
    """Test cleanup_expired_memories handles partial failures gracefully."""
    from auto_memory import Filter, MemoryExpiry

    filter_instance = Filter()

    mock_expired = [
        MagicMock(spec=MemoryExpiry, mem_id="expired-1"),
        MagicMock(spec=MemoryExpiry, mem_id="expired-2"),
        MagicMock(spec=MemoryExpiry, mem_id="expired-3"),
    ]

    with (
        patch("auto_memory.MemoryExpiryTable") as MockTable,
        patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread,
    ):
        mock_table = MockTable.return_value
        mock_table.get_expired.return_value = mock_expired

        # First delete succeeds, second fails, third succeeds
        call_count = [0]

        async def to_thread_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                raise Exception("Delete failed")
            return None

        mock_to_thread.side_effect = to_thread_side_effect
        mock_table.delete_by_mem_id.return_value = True

        stats = await filter_instance.get_related_memories(user=mock_user_model)

        # Should have 2 successful deletes (1st and 3rd)
        assert stats["total"] == 3
        assert stats["deleted"] == 2


def test_memory_expiry_table_insert(test_db_engine):
    """Test MemoryExpiryTable.insert method."""
    from sqlalchemy.orm import sessionmaker

    from auto_memory import MemoryExpiry, MemoryExpiryTable

    MemoryExpiry.metadata.create_all(test_db_engine)

    # Create a mock for get_db_context that uses our test engine
    SessionLocal = sessionmaker(bind=test_db_engine)

    with patch("auto_memory.get_db_context") as mock_get_db:
        session = SessionLocal()
        mock_get_db.return_value.__enter__ = Mock(return_value=session)
        mock_get_db.return_value.__exit__ = Mock(return_value=False)

        table = MemoryExpiryTable()
        now = int(time.time())

        result = table.insert(
            mem_id="test-mem",
            user_id="test-user",
            expired_at=now + 86400,
            db=session,
        )

        assert result is not None
        assert result.mem_id == "test-mem"
        assert result.user_id == "test-user"

        session.close()


def test_memory_expiry_table_get_by_mem_id(test_db_engine):
    """Test MemoryExpiryTable.get_by_mem_id method."""
    from sqlalchemy.orm import sessionmaker

    from auto_memory import MemoryExpiry, MemoryExpiryTable

    MemoryExpiry.metadata.create_all(test_db_engine)

    SessionLocal = sessionmaker(bind=test_db_engine)
    session = SessionLocal()

    try:
        # Insert test record
        now = int(time.time())
        expiry = MemoryExpiry(
            mem_id="test-mem",
            user_id="test-user",
            expired_at=now + 86400,
            created_at=now,
            updated_at=now,
        )
        session.add(expiry)
        session.commit()

        with patch("auto_memory.get_db_context") as mock_get_db:
            mock_get_db.return_value.__enter__ = Mock(return_value=session)
            mock_get_db.return_value.__exit__ = Mock(return_value=False)

            table = MemoryExpiryTable()

            # Test: Get existing record
            result = table.get_by_mem_id("test-mem", db=session)
            assert result is not None
            assert result.mem_id == "test-mem"

            # Test: Get non-existing record
            result = table.get_by_mem_id("non-existing", db=session)
            assert result is None

    finally:
        session.close()


def test_memory_expiry_table_update_expired_at(test_db_engine):
    """Test MemoryExpiryTable.update_expired_at method."""
    from sqlalchemy.orm import sessionmaker

    from auto_memory import MemoryExpiry, MemoryExpiryTable

    MemoryExpiry.metadata.create_all(test_db_engine)

    SessionLocal = sessionmaker(bind=test_db_engine)
    session = SessionLocal()

    try:
        now = int(time.time())
        expiry = MemoryExpiry(
            mem_id="test-mem",
            user_id="test-user",
            expired_at=now + 86400,
            created_at=now,
            updated_at=now,
        )
        session.add(expiry)
        session.commit()

        with patch("auto_memory.get_db_context") as mock_get_db:
            mock_get_db.return_value.__enter__ = Mock(return_value=session)
            mock_get_db.return_value.__exit__ = Mock(return_value=False)

            table = MemoryExpiryTable()
            new_expired_at = now + 172800  # 2 days

            result = table.update_expired_at("test-mem", new_expired_at, db=session)

            assert result is not None
            assert result.expired_at == new_expired_at

            # Test: Update non-existing record
            result = table.update_expired_at("non-existing", new_expired_at, db=session)
            assert result is None

    finally:
        session.close()


def test_memory_expiry_table_delete_by_mem_id(test_db_engine):
    """Test MemoryExpiryTable.delete_by_mem_id method."""
    from sqlalchemy.orm import sessionmaker

    from auto_memory import MemoryExpiry, MemoryExpiryTable

    MemoryExpiry.metadata.create_all(test_db_engine)

    SessionLocal = sessionmaker(bind=test_db_engine)
    session = SessionLocal()

    try:
        now = int(time.time())
        expiry = MemoryExpiry(
            mem_id="test-mem",
            user_id="test-user",
            expired_at=now + 86400,
            created_at=now,
            updated_at=now,
        )
        session.add(expiry)
        session.commit()

        with patch("auto_memory.get_db_context") as mock_get_db:
            mock_get_db.return_value.__enter__ = Mock(return_value=session)
            mock_get_db.return_value.__exit__ = Mock(return_value=False)

            table = MemoryExpiryTable()

            # Test: Delete existing record
            result = table.delete_by_mem_id("test-mem", db=session)
            assert result is True

            # Verify deletion
            deleted = session.query(MemoryExpiry).filter_by(mem_id="test-mem").first()
            assert deleted is None

            # Test: Delete non-existing record
            result = table.delete_by_mem_id("non-existing", db=session)
            assert result is False

    finally:
        session.close()


def test_memory_expiry_table_get_expired(test_db_engine):
    """Test MemoryExpiryTable.get_expired method."""
    from sqlalchemy.orm import sessionmaker

    from auto_memory import MemoryExpiry, MemoryExpiryTable

    MemoryExpiry.metadata.create_all(test_db_engine)

    SessionLocal = sessionmaker(bind=test_db_engine)
    session = SessionLocal()

    try:
        now = int(time.time())
        user_id = "test-user"

        # Insert expired and non-expired records
        records = [
            MemoryExpiry(
                mem_id="expired-1",
                user_id=user_id,
                expired_at=now - 86400,
                created_at=now - 172800,
                updated_at=now - 172800,
            ),
            MemoryExpiry(
                mem_id="expired-2",
                user_id=user_id,
                expired_at=now - 3600,
                created_at=now - 86400,
                updated_at=now - 86400,
            ),
            MemoryExpiry(
                mem_id="valid",
                user_id=user_id,
                expired_at=now + 86400,
                created_at=now,
                updated_at=now,
            ),
        ]
        session.add_all(records)
        session.commit()

        with patch("auto_memory.get_db_context") as mock_get_db:
            mock_get_db.return_value.__enter__ = Mock(return_value=session)
            mock_get_db.return_value.__exit__ = Mock(return_value=False)

            table = MemoryExpiryTable()

            expired = table.get_expired(user_id, now, db=session)

            assert len(expired) == 2
            expired_ids = {r.mem_id for r in expired}
            assert expired_ids == {"expired-1", "expired-2"}

    finally:
        session.close()
