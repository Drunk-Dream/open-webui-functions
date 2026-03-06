"""
Unit tests for tool-calling planner and mutation guardrails in auto_memory.py.

Tests cover:
1. Valid mixed actions trigger deterministic delete->update->add execution order
2. Invalid update/delete ID rejected and no mutations
3. Provider response without tool_calls is treated as no-op and no mutations
4. Tool args with extra keys rejected by strict schema and no mutations
"""

import json
from datetime import datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)
from pydantic import ValidationError

from auto_memory import (
    Filter,
    INLET_MEMORY_CONTEXT_PREFIX,
    MemoryActionRequestStub,
    MemoryAddAction,
    MemoryDeleteAction,
    Memory,
    MemoryUpdateAction,
    build_memory_action_tools,
)


@pytest.fixture
def mock_user():
    """Mock UserModel."""
    user = MagicMock()
    user.id = "test-user-123"
    user.email = "test@example.com"
    return user


@pytest.fixture
def mock_emitter():
    """Mock emitter callable."""
    return AsyncMock()


@pytest.fixture
def sample_memories():
    """Sample related memories for testing."""
    from datetime import datetime

    return [
        Memory(
            mem_id="mem-001",
            created_at=datetime(2026, 1, 1, 12, 0, 0),
            update_at=datetime(2026, 1, 1, 12, 0, 0),
            content="User likes Python",
            similarity_score=0.95,
        ),
        Memory(
            mem_id="mem-002",
            created_at=datetime(2026, 1, 2, 12, 0, 0),
            update_at=datetime(2026, 1, 2, 12, 0, 0),
            content="User works at Acme Corp",
            similarity_score=0.88,
        ),
    ]


def make_tool_calls_response(
    tool_calls: list[tuple[str, dict[str, Any]]],
) -> ChatCompletion:
    """Helper to construct a ChatCompletion with one or more tool_calls."""
    message_tool_calls = [
        ChatCompletionMessageToolCall(
            id=f"call_{index}",
            type="function",
            function=Function(
                name=tool_name,
                arguments=json.dumps(arguments_dict),
            ),
        )
        for index, (tool_name, arguments_dict) in enumerate(tool_calls, start=1)
    ]

    return ChatCompletion(
        id="chatcmpl-test",
        object="chat.completion",
        created=1234567890,
        model="gpt-4o",
        choices=[
            Choice(
                index=0,
                message=ChatCompletionMessage(
                    role="assistant",
                    content=None,
                    tool_calls=cast(Any, message_tool_calls),
                ),
                finish_reason="tool_calls",
            )
        ],
    )


def make_no_tool_call_response() -> ChatCompletion:
    """Helper to construct a ChatCompletion without tool_calls."""
    return ChatCompletion(
        id="chatcmpl-test",
        object="chat.completion",
        created=1234567890,
        model="gpt-4o",
        choices=[
            Choice(
                index=0,
                message=ChatCompletionMessage(
                    role="assistant",
                    content="I cannot help with that.",
                    tool_calls=None,
                ),
                finish_reason="stop",
            )
        ],
    )


@pytest.mark.asyncio
async def test_valid_mixed_actions_deterministic_order(
    mock_user, mock_emitter, sample_memories
):
    """
    Test 1: Valid mixed actions trigger deterministic delete->update->add execution order.
    """
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False
    filter_instance.user_valves = filter_instance.UserValves()
    filter_instance.user_valves.show_status = False

    # Build tools with existing IDs
    existing_ids = [m.mem_id for m in sample_memories]
    _, tool_definitions, tool_choice = build_memory_action_tools(existing_ids)

    # Mock parsed action plan
    action_plan = MemoryActionRequestStub(
        actions=[
            MemoryAddAction(action="add", content="User prefers dark mode"),
            MemoryDeleteAction(action="delete", id="mem-001"),
            MemoryUpdateAction(
                action="update",
                id="mem-002",
                content="User works at NewCo",
            ),
        ]
    )

    # Track mutation call order
    mutation_calls = []

    async def mock_delete(memory_id, request, user, db):
        mutation_calls.append(("delete", memory_id))

    async def mock_update(memory_id, request, form_data, user):
        mutation_calls.append(("update", memory_id, form_data.content))

    async def mock_add(request, form_data, user):
        mutation_calls.append(("add", form_data.content))

    with (
        patch.object(
            filter_instance,
            "query_openai_sdk",
            return_value=action_plan,
        ),
        patch("auto_memory.delete_memory_by_id", new=mock_delete),
        patch("auto_memory.update_memory_by_id", new=mock_update),
        patch("auto_memory.add_memory", new=mock_add),
        patch("open_webui.internal.db.get_db") as mock_get_db,
    ):
        mock_get_db.return_value.__enter__.return_value = MagicMock()

        # Build action plan
        action_plan = await filter_instance.query_openai_sdk(
            system_prompt="test",
            user_message="test",
            response_model={"add_memory": Memory},
            tools=tool_definitions,
            tool_choice=tool_choice,
        )

        # Execute mutations
        await filter_instance.apply_memory_actions(
            action_plan=action_plan, user=mock_user, emitter=mock_emitter
        )

    # Assert order: delete -> update -> add
    assert len(mutation_calls) == 3
    assert mutation_calls[0] == ("delete", "mem-001")
    assert mutation_calls[1] == ("update", "mem-002", "User works at NewCo")
    assert mutation_calls[2] == ("add", "User prefers dark mode")


@pytest.mark.asyncio
async def test_invalid_id_rejected_no_mutations(
    mock_user, mock_emitter, sample_memories
):
    """
    Test 2: Invalid update/delete ID rejected and no mutations.
    """
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False
    filter_instance.user_valves = filter_instance.UserValves()

    existing_ids = [m.mem_id for m in sample_memories]
    tool_models, _, _ = build_memory_action_tools(existing_ids)

    mutation_calls = []

    async def mock_delete(memory_id, request, user, db):
        mutation_calls.append(("delete", memory_id))

    with (
        patch("auto_memory.delete_memory_by_id", new=mock_delete),
        patch("open_webui.internal.db.get_db") as mock_get_db,
    ):
        mock_get_db.return_value.__enter__.return_value = MagicMock()

        # Attempt to parse with strict schema
        raw_args = json.dumps({"id": "mem-999"})
        with pytest.raises(ValidationError) as exc_info:
            tool_models["delete_memory"].model_validate_json(raw_args)

        # Verify validation error mentions invalid ID
        assert "mem-999" in str(exc_info.value) or "Input should be" in str(
            exc_info.value
        )

    # No mutations should have occurred
    assert len(mutation_calls) == 0


@pytest.mark.asyncio
async def test_no_tool_calls_noop_no_mutations(mock_user, mock_emitter):
    """
    Test 3: Provider response without tool_calls is treated as no-op and no mutations.
    """
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False
    filter_instance.user_valves = filter_instance.UserValves()

    existing_ids = ["mem-001"]
    tool_models, tool_definitions, tool_choice = build_memory_action_tools(existing_ids)

    # Mock response without tool_calls
    mock_response = make_no_tool_call_response()

    mutation_calls = []

    async def mock_delete(memory_id, request, user, db):
        mutation_calls.append(("delete", memory_id))

    with (
        patch("auto_memory.OpenAI") as mock_openai_class,
        patch("auto_memory.delete_memory_by_id", new=mock_delete),
    ):
        # Mock OpenAI client to return no-tool-calls response
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_client.chat.completions.create.return_value = mock_response

        action_plan = await filter_instance.query_openai_sdk(
            system_prompt="test",
            user_message="test",
            response_model=tool_models,
            tools=tool_definitions,
            tool_choice=tool_choice,
        )

        assert isinstance(action_plan, MemoryActionRequestStub)
        assert action_plan.actions == []

    # No mutations should have occurred
    assert len(mutation_calls) == 0


@pytest.mark.asyncio
async def test_extra_keys_rejected_strict_schema_no_mutations(
    mock_user, mock_emitter, sample_memories
):
    """
    Test 4: Tool args with extra keys rejected by strict schema and no mutations.
    """
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False
    filter_instance.user_valves = filter_instance.UserValves()

    existing_ids = [m.mem_id for m in sample_memories]
    tool_models, _, _ = build_memory_action_tools(existing_ids)

    mutation_calls = []

    async def mock_add(request, form_data, user):
        mutation_calls.append(("add", form_data.content))

    with patch("auto_memory.add_memory", new=mock_add):
        # Attempt to parse with strict schema
        raw_args = json.dumps(
            {
                "content": "User likes coffee",
                "extra_field": "should_fail",
            }
        )
        with pytest.raises(ValidationError) as exc_info:
            tool_models["add_memory"].model_validate_json(raw_args)

        # Verify validation error mentions extra field
        assert "extra_field" in str(
            exc_info.value
        ) or "Extra inputs are not permitted" in str(exc_info.value)

    # No mutations should have occurred
    assert len(mutation_calls) == 0


@pytest.mark.asyncio
async def test_add_action_initializes_expiry_immediately(mock_user, mock_emitter):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False
    filter_instance.user_valves = filter_instance.UserValves()
    filter_instance.user_valves.show_status = False

    action_plan = MemoryActionRequestStub(
        actions=[MemoryAddAction(action="add", content="User likes espresso")]
    )

    async def mock_add(request, form_data, user):
        return {"id": "mem-new-001", "content": form_data.content}

    with (
        patch("auto_memory.add_memory", new=mock_add),
        patch("auto_memory.MemoryExpiries.get_by_mem_id", return_value=None),
        patch("auto_memory.MemoryExpiries.insert") as mock_insert,
        patch("auto_memory.MemoryExpiries.update_expired_at") as mock_update,
        patch("time.time", return_value=1000),
    ):
        await filter_instance.apply_memory_actions(
            action_plan=action_plan,
            user=mock_user,
            emitter=mock_emitter,
        )

    mock_update.assert_not_called()
    mock_insert.assert_called_once_with(
        mem_id="mem-new-001",
        user_id=mock_user.id,
        expired_at=1000 + filter_instance.valves.initial_expiry_days * 86400,
    )


@pytest.mark.asyncio
async def test_add_action_without_memory_id_skips_expiry_init(mock_user, mock_emitter):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False
    filter_instance.user_valves = filter_instance.UserValves()
    filter_instance.user_valves.show_status = False

    action_plan = MemoryActionRequestStub(
        actions=[MemoryAddAction(action="add", content="User likes tea")]
    )

    async def mock_add(request, form_data, user):
        return {"content": form_data.content}

    with (
        patch("auto_memory.add_memory", new=mock_add),
        patch("auto_memory.MemoryExpiries.get_by_mem_id") as mock_get,
        patch("auto_memory.MemoryExpiries.insert") as mock_insert,
        patch("auto_memory.MemoryExpiries.update_expired_at") as mock_update,
    ):
        await filter_instance.apply_memory_actions(
            action_plan=action_plan,
            user=mock_user,
            emitter=mock_emitter,
        )

    mock_get.assert_not_called()
    mock_insert.assert_not_called()
    mock_update.assert_not_called()


def test_inlet_injects_related_memories_into_messages(mock_emitter):
    filter_instance = Filter()
    memory = Memory(
        mem_id="mem-101",
        created_at=datetime(2026, 1, 1, 12, 0, 0),
        update_at=datetime(2026, 1, 1, 12, 0, 0),
        content="User prefers concise answers",
        similarity_score=0.91,
    )
    body = {
        "messages": [
            {"role": "system", "content": "Core system prompt"},
            {"role": "user", "content": "hi"},
        ]
    }

    with (
        patch("auto_memory.Users.get_user_by_id") as mock_get_user,
        patch(
            "auto_memory._run_async_in_thread",
            side_effect=lambda coro: (coro.close(), [memory])[1],
        ),
    ):
        mock_get_user.return_value = MagicMock(id="user-1")
        updated = filter_instance.inlet(
            body=body,
            __event_emitter__=mock_emitter,
            __user__={"id": "user-1"},
        )

    system_messages = [m for m in updated["messages"] if m.get("role") == "system"]
    assert len(system_messages) == 2
    assert system_messages[1]["content"].startswith(INLET_MEMORY_CONTEXT_PREFIX)
    assert "User prefers concise answers" in system_messages[1]["content"]


def test_inlet_injects_with_single_user_message(mock_emitter):
    filter_instance = Filter()
    memory = Memory(
        mem_id="mem-102",
        created_at=datetime(2026, 1, 1, 12, 0, 0),
        update_at=datetime(2026, 1, 1, 12, 0, 0),
        content="User likes short replies",
        similarity_score=0.9,
    )
    body = {"messages": [{"role": "user", "content": "test"}]}

    with (
        patch("auto_memory.Users.get_user_by_id") as mock_get_user,
        patch(
            "auto_memory._run_async_in_thread",
            side_effect=lambda coro: (coro.close(), [memory])[1],
        ),
    ):
        mock_get_user.return_value = MagicMock(id="user-1")
        updated = filter_instance.inlet(
            body=body,
            __event_emitter__=mock_emitter,
            __user__={"id": "user-1"},
        )

    assert len(updated["messages"]) == 2
    assert updated["messages"][0]["role"] == "system"
    assert updated["messages"][0]["content"].startswith(INLET_MEMORY_CONTEXT_PREFIX)
    assert updated["messages"][1] == {"role": "user", "content": "test"}


def test_inject_memory_context_replaces_previous_memory_block():
    filter_instance = Filter()
    messages = [
        {"role": "system", "content": "Core system prompt"},
        {
            "role": "system",
            "content": f"{INLET_MEMORY_CONTEXT_PREFIX}\nold context",
        },
        {"role": "user", "content": "hello"},
    ]

    injected = filter_instance.inject_memory_context_into_messages(
        messages=messages,
        memory_context=f"{INLET_MEMORY_CONTEXT_PREFIX}\nnew context",
    )

    memory_blocks = [
        m
        for m in injected
        if m.get("role") == "system"
        and isinstance(m.get("content"), str)
        and m["content"].startswith(INLET_MEMORY_CONTEXT_PREFIX)
    ]
    assert len(memory_blocks) == 1
    assert "new context" in memory_blocks[0]["content"]


@pytest.mark.asyncio
async def test_cleanup_expired_memories_returns_detailed_stats(mock_user):
    """Test that cleanup_expired_memories returns detailed statistics with vector_deleted and expiry_deleted."""
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False

    # Mock expired records
    mock_record_1 = MagicMock()
    mock_record_1.mem_id = "mem-expired-001"
    mock_record_2 = MagicMock()
    mock_record_2.mem_id = "mem-expired-002"

    with (
        patch("auto_memory.MemoryExpiryTable") as mock_expiry_table_class,
        patch.object(
            filter_instance,
            "_delete_memory_sync",
            side_effect=[None, Exception("Vector DB error")],
        ),
        patch("time.time", return_value=2000),
    ):
        mock_expiry_table = mock_expiry_table_class.return_value
        mock_expiry_table.get_expired.return_value = [mock_record_1, mock_record_2]
        mock_expiry_table.delete_by_mem_id.return_value = None

        stats = await filter_instance.cleanup_expired_memories(user=mock_user)

    # Assert detailed statistics structure
    assert "total" in stats
    assert "vector_deleted" in stats
    assert "expiry_deleted" in stats
    assert stats["total"] == 2
    assert stats["vector_deleted"] == 1  # First succeeded, second failed
    assert stats["expiry_deleted"] == 2  # Both expiry records deleted


def test_run_coro_in_new_loop_executes_coroutine():
    """Test that _run_coro_in_new_loop correctly executes a coroutine."""
    from auto_memory import _run_coro_in_new_loop

    async def sample_coro():
        return "test_result"

    result = _run_coro_in_new_loop(sample_coro())
    assert result == "test_result"


def test_run_coro_in_new_loop_propagates_exception():
    """Test that _run_coro_in_new_loop propagates exceptions from coroutine."""
    from auto_memory import _run_coro_in_new_loop

    async def failing_coro():
        raise ValueError("test error")

    with pytest.raises(ValueError, match="test error"):
        _run_coro_in_new_loop(failing_coro())


def test_build_webui_request_creates_valid_request():
    """Test that _build_webui_request creates a valid Request object."""
    from auto_memory import _build_webui_request

    request = _build_webui_request()

    assert request is not None
    assert request.scope["type"] == "http"
    assert "app" in request.scope
