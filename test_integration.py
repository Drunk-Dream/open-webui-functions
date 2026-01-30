"""
Integration tests for auto_memory expired_at mechanism.

Tests the complete memory lifecycle:
- Create memory with expiry
- Boost memory (extend expiry)
- Expire memory (time passes)
- Cleanup expired memory
"""

import time
from datetime import datetime
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


@pytest.mark.asyncio
async def test_end_to_end_memory_lifecycle(test_db_engine, mock_user_model):
    """Test complete memory lifecycle: create → boost → expire → cleanup."""
    from sqlalchemy.orm import sessionmaker

    from auto_memory import Filter, Memory, MemoryExpiry, MemoryExpiryTable

    # Setup
    MemoryExpiry.metadata.create_all(test_db_engine)
    filter_instance = Filter()

    SessionLocal = sessionmaker(bind=test_db_engine)
    session = SessionLocal()

    try:
        # Use a fixed "now" time for deterministic testing
        base_time = 1700000000  # Fixed timestamp

        with patch("auto_memory.get_db_context") as mock_get_db:
            mock_get_db.return_value.__enter__ = Mock(return_value=session)
            mock_get_db.return_value.__exit__ = Mock(return_value=False)

            expiry_table = MemoryExpiryTable()

            # ========================================
            # Step 1: Create memory with expiry
            # ========================================
            mem_id = "test-memory-1"
            initial_expiry = base_time + 86400  # Expires in 1 day

            expiry_table.insert(
                mem_id=mem_id,
                user_id=mock_user_model.id,
                expired_at=initial_expiry,
                db=session,
            )

            # Verify creation
            record = expiry_table.get_by_mem_id(mem_id, db=session)
            assert record is not None, "Memory expiry record should be created"
            assert record.expired_at == initial_expiry
            assert record.user_id == mock_user_model.id

            # ========================================
            # Step 2: Boost memory (extend expiry)
            # ========================================
            new_expired_at = base_time + (2 * 86400)  # Extend to 2 days
            expiry_table.update_expired_at(mem_id, new_expired_at, db=session)

            # Verify boost
            record = expiry_table.get_by_mem_id(mem_id, db=session)
            assert record is not None
            assert record.expired_at == new_expired_at, "Expiry should be extended"

            # ========================================
            # Step 3: Simulate time passing (memory expires)
            # ========================================
            future_time = base_time + (3 * 86400)  # 3 days later

            # Query expired memories
            expired = expiry_table.get_expired(
                mock_user_model.id, future_time, db=session
            )
            assert len(expired) == 1, "Should find 1 expired memory"
            assert expired[0].mem_id == mem_id

            # ========================================
            # Step 4: Cleanup expired memory
            # ========================================
            result = expiry_table.delete_by_mem_id(mem_id, db=session)
            assert result is True, "Delete should succeed"

            # Verify deletion
            record = expiry_table.get_by_mem_id(mem_id, db=session)
            assert record is None, "Memory expiry record should be deleted"

    finally:
        session.close()


@pytest.mark.asyncio
async def test_multiple_memories_lifecycle(test_db_engine, mock_user_model):
    """Test lifecycle with multiple memories at different expiry stages."""
    from sqlalchemy.orm import sessionmaker

    from auto_memory import MemoryExpiry, MemoryExpiryTable

    MemoryExpiry.metadata.create_all(test_db_engine)

    SessionLocal = sessionmaker(bind=test_db_engine)
    session = SessionLocal()

    try:
        base_time = 1700000000

        with patch("auto_memory.get_db_context") as mock_get_db:
            mock_get_db.return_value.__enter__ = Mock(return_value=session)
            mock_get_db.return_value.__exit__ = Mock(return_value=False)

            expiry_table = MemoryExpiryTable()

            # Create memories with different expiry times
            memories = [
                ("mem-1", base_time + 86400),  # Expires in 1 day
                ("mem-2", base_time + 172800),  # Expires in 2 days
                ("mem-3", base_time + 259200),  # Expires in 3 days
            ]

            for mem_id, expired_at in memories:
                expiry_table.insert(
                    mem_id=mem_id,
                    user_id=mock_user_model.id,
                    expired_at=expired_at,
                    db=session,
                )

            # At base_time + 1.5 days: only mem-1 should be expired
            time_1_5_days = base_time + int(1.5 * 86400)
            expired = expiry_table.get_expired(
                mock_user_model.id, time_1_5_days, db=session
            )
            assert len(expired) == 1
            assert expired[0].mem_id == "mem-1"

            # At base_time + 2.5 days: mem-1 and mem-2 should be expired
            time_2_5_days = base_time + int(2.5 * 86400)
            expired = expiry_table.get_expired(
                mock_user_model.id, time_2_5_days, db=session
            )
            assert len(expired) == 2
            expired_ids = {r.mem_id for r in expired}
            assert expired_ids == {"mem-1", "mem-2"}

            # Boost mem-2 to extend its expiry
            new_expiry = base_time + (4 * 86400)  # Extend to 4 days
            expiry_table.update_expired_at("mem-2", new_expiry, db=session)

            # At base_time + 2.5 days: now only mem-1 should be expired
            expired = expiry_table.get_expired(
                mock_user_model.id, time_2_5_days, db=session
            )
            assert len(expired) == 1
            assert expired[0].mem_id == "mem-1"

            # Cleanup mem-1
            expiry_table.delete_by_mem_id("mem-1", db=session)

            # Verify mem-1 is gone
            assert expiry_table.get_by_mem_id("mem-1", db=session) is None

            # At base_time + 3.5 days: only mem-3 should be expired
            time_3_5_days = base_time + int(3.5 * 86400)
            expired = expiry_table.get_expired(
                mock_user_model.id, time_3_5_days, db=session
            )
            assert len(expired) == 1
            assert expired[0].mem_id == "mem-3"

    finally:
        session.close()


@pytest.mark.asyncio
async def test_boost_memories_integration(test_db_engine, mock_user_model):
    """Test boost_memories method with real database operations."""
    from sqlalchemy.orm import sessionmaker

    from auto_memory import Filter, Memory, MemoryExpiry, MemoryExpiryTable

    MemoryExpiry.metadata.create_all(test_db_engine)
    filter_instance = Filter()

    SessionLocal = sessionmaker(bind=test_db_engine)
    session = SessionLocal()

    try:
        base_time = 1700000000

        with (
            patch("auto_memory.get_db_context") as mock_get_db,
            patch("time.time", return_value=base_time),
        ):
            mock_get_db.return_value.__enter__ = Mock(return_value=session)
            mock_get_db.return_value.__exit__ = Mock(return_value=False)

            expiry_table = MemoryExpiryTable()

            # Create an existing memory expiry record
            expiry_table.insert(
                mem_id="existing-mem",
                user_id=mock_user_model.id,
                expired_at=base_time + 86400,  # 1 day
                db=session,
            )

            # Create mock memories - one existing, one new
            mock_memories = [
                Memory(
                    mem_id="existing-mem",
                    created_at=datetime.fromtimestamp(base_time),
                    update_at=datetime.fromtimestamp(base_time),
                    content="existing memory",
                ),
                Memory(
                    mem_id="new-mem",
                    created_at=datetime.fromtimestamp(base_time),
                    update_at=datetime.fromtimestamp(base_time),
                    content="new memory",
                ),
            ]

            # Call boost_memories
            stats = await filter_instance.boost_memories(mock_memories, mock_user_model)

            # Verify stats
            assert stats["total"] == 2
            assert stats["boosted"] == 1  # existing-mem was boosted
            assert stats["created"] == 1  # new-mem was created

            # Verify existing memory was boosted
            existing = expiry_table.get_by_mem_id("existing-mem", db=session)
            assert existing is not None
            expected_boosted_expiry = base_time + (
                filter_instance.valves.extension_days * 86400
            )
            assert existing.expired_at == expected_boosted_expiry

            # Verify new memory was created
            new = expiry_table.get_by_mem_id("new-mem", db=session)
            assert new is not None
            expected_new_expiry = base_time + (
                filter_instance.valves.initial_expiry_days * 86400
            )
            assert new.expired_at == expected_new_expiry

    finally:
        session.close()


@pytest.mark.asyncio
async def test_cleanup_expired_memories_integration(test_db_engine, mock_user_model):
    """Test cleanup_expired_memories with real database operations."""
    from sqlalchemy.orm import sessionmaker

    from auto_memory import Filter, MemoryExpiry, MemoryExpiryTable

    MemoryExpiry.metadata.create_all(test_db_engine)
    filter_instance = Filter()

    SessionLocal = sessionmaker(bind=test_db_engine)
    session = SessionLocal()

    try:
        base_time = 1700000000

        with (
            patch("auto_memory.get_db_context") as mock_get_db,
            patch("time.time", return_value=base_time),
            patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread,
        ):
            mock_get_db.return_value.__enter__ = Mock(return_value=session)
            mock_get_db.return_value.__exit__ = Mock(return_value=False)

            expiry_table = MemoryExpiryTable()

            # Create expired and non-expired memories
            expiry_table.insert(
                mem_id="expired-1",
                user_id=mock_user_model.id,
                expired_at=base_time - 86400,  # Expired 1 day ago
                db=session,
            )
            expiry_table.insert(
                mem_id="expired-2",
                user_id=mock_user_model.id,
                expired_at=base_time - 3600,  # Expired 1 hour ago
                db=session,
            )
            expiry_table.insert(
                mem_id="valid",
                user_id=mock_user_model.id,
                expired_at=base_time + 86400,  # Expires in 1 day
                db=session,
            )

            # Call get_related_memories (which performs cleanup)
            stats = await filter_instance.get_related_memories(user=mock_user_model)

            # Verify stats
            assert stats["total"] == 2
            assert stats["deleted"] == 2

            # Verify expired memories are deleted
            assert expiry_table.get_by_mem_id("expired-1", db=session) is None
            assert expiry_table.get_by_mem_id("expired-2", db=session) is None

            # Verify valid memory still exists
            valid = expiry_table.get_by_mem_id("valid", db=session)
            assert valid is not None

    finally:
        session.close()


@pytest.mark.asyncio
async def test_time_mock_simulation(test_db_engine, mock_user_model):
    """Test memory expiry with simulated time progression."""
    from sqlalchemy.orm import sessionmaker

    from auto_memory import Filter, Memory, MemoryExpiry, MemoryExpiryTable

    MemoryExpiry.metadata.create_all(test_db_engine)
    filter_instance = Filter()
    filter_instance.valves.initial_expiry_days = 7  # 7 days initial expiry
    filter_instance.valves.extension_days = 3  # 3 days extension

    SessionLocal = sessionmaker(bind=test_db_engine)
    session = SessionLocal()

    try:
        # Day 0: Create memory
        day_0 = 1700000000

        with patch("auto_memory.get_db_context") as mock_get_db:
            mock_get_db.return_value.__enter__ = Mock(return_value=session)
            mock_get_db.return_value.__exit__ = Mock(return_value=False)

            expiry_table = MemoryExpiryTable()

            # Create memory on Day 0
            with patch("time.time", return_value=day_0):
                expiry_table.insert(
                    mem_id="test-mem",
                    user_id=mock_user_model.id,
                    expired_at=day_0 + (7 * 86400),  # Expires on Day 7
                    db=session,
                )

            # Day 5: Memory is accessed, should be boosted
            day_5 = day_0 + (5 * 86400)
            with patch("time.time", return_value=day_5):
                mock_memory = Memory(
                    mem_id="test-mem",
                    created_at=datetime.fromtimestamp(day_0),
                    update_at=datetime.fromtimestamp(day_0),
                    content="test",
                )
                await filter_instance.boost_memories([mock_memory], mock_user_model)

                # Verify expiry was extended
                record = expiry_table.get_by_mem_id("test-mem", db=session)
                assert record is not None
                # New expiry should be Day 5 + 3 days = Day 8
                expected_expiry = day_5 + (3 * 86400)
                assert record.expired_at == expected_expiry

            # Day 6: Memory should NOT be expired
            day_6 = day_0 + (6 * 86400)
            expired = expiry_table.get_expired(mock_user_model.id, day_6, db=session)
            assert len(expired) == 0

            # Day 9: Memory SHOULD be expired (Day 8 < Day 9)
            day_9 = day_0 + (9 * 86400)
            expired = expiry_table.get_expired(mock_user_model.id, day_9, db=session)
            assert len(expired) == 1
            assert expired[0].mem_id == "test-mem"

    finally:
        session.close()


@pytest.mark.asyncio
async def test_multi_user_isolation(test_db_engine, mock_user_model):
    """Test that memory expiry is isolated per user."""
    from sqlalchemy.orm import sessionmaker

    from auto_memory import MemoryExpiry, MemoryExpiryTable

    MemoryExpiry.metadata.create_all(test_db_engine)

    SessionLocal = sessionmaker(bind=test_db_engine)
    session = SessionLocal()

    try:
        base_time = 1700000000
        user_1_id = "user-1"
        user_2_id = "user-2"

        with patch("auto_memory.get_db_context") as mock_get_db:
            mock_get_db.return_value.__enter__ = Mock(return_value=session)
            mock_get_db.return_value.__exit__ = Mock(return_value=False)

            expiry_table = MemoryExpiryTable()

            # Create expired memories for both users
            expiry_table.insert(
                mem_id="user1-expired",
                user_id=user_1_id,
                expired_at=base_time - 86400,
                db=session,
            )
            expiry_table.insert(
                mem_id="user2-expired",
                user_id=user_2_id,
                expired_at=base_time - 86400,
                db=session,
            )
            expiry_table.insert(
                mem_id="user1-valid",
                user_id=user_1_id,
                expired_at=base_time + 86400,
                db=session,
            )

            # Query expired for user 1 only
            user1_expired = expiry_table.get_expired(user_1_id, base_time, db=session)
            assert len(user1_expired) == 1
            assert user1_expired[0].mem_id == "user1-expired"

            # Query expired for user 2 only
            user2_expired = expiry_table.get_expired(user_2_id, base_time, db=session)
            assert len(user2_expired) == 1
            assert user2_expired[0].mem_id == "user2-expired"

            # Delete user 1's expired memory
            expiry_table.delete_by_mem_id("user1-expired", db=session)

            # User 2's expired memory should still exist
            user2_record = expiry_table.get_by_mem_id("user2-expired", db=session)
            assert user2_record is not None

    finally:
        session.close()


@pytest.mark.asyncio
async def test_edge_case_exact_expiry_time(test_db_engine, mock_user_model):
    """Test edge case where current time equals expiry time."""
    from sqlalchemy.orm import sessionmaker

    from auto_memory import MemoryExpiry, MemoryExpiryTable

    MemoryExpiry.metadata.create_all(test_db_engine)

    SessionLocal = sessionmaker(bind=test_db_engine)
    session = SessionLocal()

    try:
        base_time = 1700000000

        with patch("auto_memory.get_db_context") as mock_get_db:
            mock_get_db.return_value.__enter__ = Mock(return_value=session)
            mock_get_db.return_value.__exit__ = Mock(return_value=False)

            expiry_table = MemoryExpiryTable()

            # Create memory that expires exactly at base_time
            expiry_table.insert(
                mem_id="exact-expiry",
                user_id=mock_user_model.id,
                expired_at=base_time,  # Expires exactly now
                db=session,
            )

            # Query at exact expiry time - should NOT be expired (expired_at < now)
            expired = expiry_table.get_expired(
                mock_user_model.id, base_time, db=session
            )
            assert len(expired) == 0

            # Query 1 second later - should be expired
            expired = expiry_table.get_expired(
                mock_user_model.id, base_time + 1, db=session
            )
            assert len(expired) == 1

    finally:
        session.close()


@pytest.mark.asyncio
async def test_repeated_boost_extends_expiry(test_db_engine, mock_user_model):
    """Test that repeated boosts keep extending the expiry time."""
    from sqlalchemy.orm import sessionmaker

    from auto_memory import Filter, Memory, MemoryExpiry, MemoryExpiryTable

    MemoryExpiry.metadata.create_all(test_db_engine)
    filter_instance = Filter()
    filter_instance.valves.extension_days = 7

    SessionLocal = sessionmaker(bind=test_db_engine)
    session = SessionLocal()

    try:
        base_time = 1700000000

        with patch("auto_memory.get_db_context") as mock_get_db:
            mock_get_db.return_value.__enter__ = Mock(return_value=session)
            mock_get_db.return_value.__exit__ = Mock(return_value=False)

            expiry_table = MemoryExpiryTable()

            # Create initial memory
            expiry_table.insert(
                mem_id="test-mem",
                user_id=mock_user_model.id,
                expired_at=base_time + (7 * 86400),  # Day 7
                db=session,
            )

            mock_memory = Memory(
                mem_id="test-mem",
                created_at=datetime.fromtimestamp(base_time),
                update_at=datetime.fromtimestamp(base_time),
                content="test",
            )

            # Boost 1: Day 5
            day_5 = base_time + (5 * 86400)
            with patch("time.time", return_value=day_5):
                await filter_instance.boost_memories([mock_memory], mock_user_model)
                record = expiry_table.get_by_mem_id("test-mem", db=session)
                assert record.expired_at == day_5 + (7 * 86400)  # Day 12

            # Boost 2: Day 10
            day_10 = base_time + (10 * 86400)
            with patch("time.time", return_value=day_10):
                await filter_instance.boost_memories([mock_memory], mock_user_model)
                record = expiry_table.get_by_mem_id("test-mem", db=session)
                assert record.expired_at == day_10 + (7 * 86400)  # Day 17

            # Boost 3: Day 15
            day_15 = base_time + (15 * 86400)
            with patch("time.time", return_value=day_15):
                await filter_instance.boost_memories([mock_memory], mock_user_model)
                record = expiry_table.get_by_mem_id("test-mem", db=session)
                assert record.expired_at == day_15 + (7 * 86400)  # Day 22

    finally:
        session.close()
