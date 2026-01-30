"""Advanced test for auto_memory.py async functionality."""

import asyncio
import sys

import pytest

sys.path.insert(0, ".")

from auto_memory import Filter, emit_status
from open_webui.models.users import UserModel


@pytest.mark.asyncio
async def test_emit_status():
    """Test emit_status function."""
    emitted_data = []

    async def mock_emitter(data):
        emitted_data.append(data)

    await emit_status(
        description="Test status",
        emitter=mock_emitter,
        status="in_progress",
    )

    assert len(emitted_data) == 1
    assert emitted_data[0]["type"] == "status"
    assert emitted_data[0]["data"]["description"] == "Test status"
    assert emitted_data[0]["data"]["status"] == "in_progress"
    assert emitted_data[0]["data"]["done"] is False

    print("✓ emit_status 函数测试成功")


@pytest.mark.asyncio
async def test_emit_status_complete():
    """Test emit_status with complete status."""
    emitted_data = []

    async def mock_emitter(data):
        emitted_data.append(data)

    await emit_status(
        description="Task complete",
        emitter=mock_emitter,
        status="complete",
    )

    assert emitted_data[0]["data"]["status"] == "complete"
    assert emitted_data[0]["data"]["done"] is True
    assert emitted_data[0]["data"]["error"] is False

    print("✓ emit_status complete 状态测试成功")


@pytest.mark.asyncio
async def test_emit_status_error():
    """Test emit_status with error status."""
    emitted_data = []

    async def mock_emitter(data):
        emitted_data.append(data)

    await emit_status(
        description="Task failed",
        emitter=mock_emitter,
        status="error",
    )

    assert emitted_data[0]["data"]["status"] == "error"
    assert emitted_data[0]["data"]["done"] is True
    assert emitted_data[0]["data"]["error"] is True

    print("✓ emit_status error 状态测试成功")


def test_filter_log():
    """Test Filter logging functionality."""
    filter_instance = Filter()

    filter_instance.log("Test info message", level="info")
    filter_instance.log("Test warning message", level="warning")
    filter_instance.log("Test error message", level="error")

    filter_instance.valves.debug_mode = True
    filter_instance.log("Test debug message", level="debug")

    print("✓ Filter 日志功能测试成功")


def test_filter_messages_to_string():
    """Test messages_to_string method."""
    filter_instance = Filter()
    filter_instance.user_valves = Filter.UserValves()

    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
        {"role": "user", "content": "How are you?"},
    ]

    result = filter_instance.messages_to_string(messages)

    assert "-1. user: ```How are you?```" in result
    assert "-2. assistant: ```Hi there!```" in result
    assert "-3. user: ```Hello```" in result

    print("✓ messages_to_string 方法测试成功")


def test_filter_get_restricted_user_valve():
    """Test get_restricted_user_valve method."""
    filter_instance = Filter()
    filter_instance.current_user = {"role": "user"}

    result = filter_instance.get_restricted_user_valve(
        user_valve_value="custom_value",
        admin_fallback="admin_value",
        authorization_check=True,
        valve_name="test_valve",
    )

    assert result == "custom_value"

    result = filter_instance.get_restricted_user_valve(
        user_valve_value="custom_value",
        admin_fallback="admin_value",
        authorization_check=False,
        valve_name="test_valve",
    )

    assert result == "admin_value"

    print("✓ get_restricted_user_valve 方法测试成功")


def test_filter_extract_memory_context():
    """Test extract_memory_context method."""
    filter_instance = Filter()

    content_with_memory = """
    Some system prompt text.
    <memory_user_context>
    [{"content": "User likes Python", "created_at": 1234567890, "updated_at": 1234567890}]
    </memory_user_context>
    More text.
    """

    result = filter_instance.extract_memory_context(content_with_memory)

    assert result is not None
    full_match, memories = result
    assert len(memories) == 1
    assert memories[0]["content"] == "User likes Python"

    print("✓ extract_memory_context 方法测试成功")


def test_filter_format_memory_context():
    """Test format_memory_context method."""
    filter_instance = Filter()

    memories = [
        {
            "content": "User likes Python",
            "created_at": 1234567890,
            "updated_at": 1234567890,
            "similarity_score": 0.95,
        }
    ]

    result = filter_instance.format_memory_context(memories)

    assert "<long_term_memory>" in result
    assert "</long_term_memory>" in result
    assert "User likes Python" in result
    assert "similarity_score" not in result

    print("✓ format_memory_context 方法测试成功")


async def main():
    """Run all async tests."""
    print("开始测试 auto_memory.py 异步功能...\n")

    try:
        await test_emit_status()
        await test_emit_status_complete()
        await test_emit_status_error()
        test_filter_log()
        test_filter_messages_to_string()
        test_filter_get_restricted_user_valve()
        test_filter_extract_memory_context()
        test_filter_format_memory_context()

        print("\n" + "=" * 50)
        print("✅ 所有异步测试通过！")
        print("=" * 50)
        return 0

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
