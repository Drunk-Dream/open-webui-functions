"""Basic test for auto_memory.py functionality."""

import sys

sys.path.insert(0, ".")

from auto_memory import (
    Filter,
    Memory,
    MemoryAddAction,
    MemoryDeleteAction,
    MemoryUpdateAction,
    build_actions_request_model,
    searchresults_to_memories,
)
from datetime import datetime


def test_imports():
    """Test that all main classes can be imported."""
    print("✓ 所有主要类导入成功")


def test_memory_model():
    """Test Memory model creation."""
    memory = Memory(
        mem_id="test-123",
        created_at=datetime.now(),
        update_at=datetime.now(),
        content="Test memory content",
        similarity_score=0.95,
    )
    assert memory.mem_id == "test-123"
    assert memory.content == "Test memory content"
    assert memory.similarity_score == 0.95
    print("✓ Memory 模型创建成功")


def test_action_models():
    """Test action models."""
    add_action = MemoryAddAction(action="add", content="New memory")
    assert add_action.action == "add"
    assert add_action.content == "New memory"

    update_action = MemoryUpdateAction(
        action="update", id="mem-123", new_content="Updated content"
    )
    assert update_action.action == "update"
    assert update_action.id == "mem-123"

    delete_action = MemoryDeleteAction(action="delete", id="mem-456")
    assert delete_action.action == "delete"
    assert delete_action.id == "mem-456"

    print("✓ Action 模型创建成功")


def test_build_actions_request_model():
    """Test dynamic model building."""
    model_empty = build_actions_request_model([])
    assert model_empty is not None
    print("✓ 空 ID 列表的动态模型创建成功")

    model_with_ids = build_actions_request_model(["id1", "id2", "id3"])
    assert model_with_ids is not None
    print("✓ 带 ID 列表的动态模型创建成功")


def test_filter_class():
    """Test Filter class initialization."""
    filter_instance = Filter()
    assert filter_instance.valves is not None
    assert hasattr(filter_instance.valves, "openai_api_url")
    assert hasattr(filter_instance.valves, "model")
    print("✓ Filter 类初始化成功")


def test_filter_valves():
    """Test Filter valves configuration."""
    filter_instance = Filter()

    assert filter_instance.valves.messages_to_consider == 4
    assert filter_instance.valves.related_memories_n == 5
    assert filter_instance.valves.initial_expiry_days == 30
    assert filter_instance.valves.extension_days == 14
    print("✓ Filter valves 配置正确")


def test_user_valves():
    """Test UserValves model."""
    user_valves = Filter.UserValves()
    assert user_valves.enabled is True
    assert user_valves.show_status is True
    print("✓ UserValves 模型创建成功")


def main():
    """Run all tests."""
    print("开始测试 auto_memory.py...\n")

    try:
        test_imports()
        test_memory_model()
        test_action_models()
        test_build_actions_request_model()
        test_filter_class()
        test_filter_valves()
        test_user_valves()

        print("\n" + "=" * 50)
        print("✅ 所有测试通过！")
        print("=" * 50)
        return 0

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
