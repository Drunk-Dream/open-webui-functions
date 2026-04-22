import json
import ast
import inspect
import importlib
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import auto_memory as auto_memory_module
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)
from pydantic import ValidationError

from auto_memory import (
    ACCESS_GAIN,
    BASE_DECAY_PER_DAY,
    BURST_GAIN_MULTIPLIER,
    BURST_WINDOW_MINUTES,
    Filter,
    GAIN_DAMPING,
    INLET_MEMORY_CONTEXT_PREFIX,
    INITIAL_STRENGTH,
    MAINTENANCE_BATCH_SIZE,
    MAX_LIFETIME_DAYS,
    MemoryActionRequestStub,
    MemoryAddAction,
    MemoryDeleteAction,
    Memory,
    MemoryUpdateAction,
    MemoryExpiryTable,
    build_memory_action_tools,
)


def _expected_soft_expire_at(
    strength: float,
    now_timestamp: int,
    hard_expire_at: int,
) -> int:
    return min(now_timestamp + (max(1, round(strength / 10)) * 86400), hard_expire_at)


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


def make_single_tool_call_response(
    tool_name: str, raw_arguments: str
) -> ChatCompletion:
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
                    tool_calls=cast(
                        Any,
                        [
                            ChatCompletionMessageToolCall(
                                id="call_1",
                                type="function",
                                function=Function(
                                    name=tool_name,
                                    arguments=raw_arguments,
                                ),
                            )
                        ],
                    ),
                ),
                finish_reason="tool_calls",
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
@pytest.mark.parametrize(
    "tool_name, raw_args",
    [
        ("delete_memory", json.dumps({"id": "mem-999"})),
        ("update_memory", json.dumps({"id": "mem-999", "content": "updated"})),
    ],
)
async def test_invalid_id_rejected_no_mutations(
    tool_name, raw_args, mock_user, mock_emitter, sample_memories
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
        with pytest.raises(ValidationError) as exc_info:
            tool_models[tool_name].model_validate_json(raw_args)

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
@pytest.mark.parametrize(
    "tool_name, raw_args",
    [
        (
            "add_memory",
            json.dumps({"content": "User likes coffee", "extra_field": "should_fail"}),
        ),
        (
            "update_memory",
            json.dumps(
                {
                    "id": "mem-001",
                    "content": "User likes coffee",
                    "extra_field": "should_fail",
                }
            ),
        ),
        (
            "delete_memory",
            json.dumps({"id": "mem-001", "extra_field": "should_fail"}),
        ),
    ],
)
async def test_extra_keys_rejected_strict_schema_no_mutations(
    tool_name, raw_args, mock_user, mock_emitter, sample_memories
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
        with pytest.raises(ValidationError) as exc_info:
            tool_models[tool_name].model_validate_json(raw_args)

        # Verify validation error mentions extra field
        assert "extra_field" in str(
            exc_info.value
        ) or "Extra inputs are not permitted" in str(exc_info.value)

    # No mutations should have occurred
    assert len(mutation_calls) == 0


@pytest.mark.asyncio
async def test_query_openai_sdk_no_tool_calls_mode_semantics_difference(
    sample_memories,
):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False
    filter_instance.user_valves = filter_instance.UserValves()

    existing_ids = [m.mem_id for m in sample_memories]
    tool_models, tool_definitions, tool_choice = build_memory_action_tools(existing_ids)
    mock_response = make_no_tool_call_response()

    with patch("auto_memory.OpenAI") as mock_openai_class:
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_client.chat.completions.create.return_value = mock_response

        dict_mode_plan = await filter_instance.query_openai_sdk(
            system_prompt="test",
            user_message="test",
            response_model=tool_models,
            tools=tool_definitions,
            tool_choice=tool_choice,
        )

        assert isinstance(dict_mode_plan, MemoryActionRequestStub)
        assert dict_mode_plan.actions == []

        with pytest.raises(
            ValueError, match="expected exactly one tool call but got zero"
        ):
            await filter_instance.query_openai_sdk(
                system_prompt="test",
                user_message="test",
                response_model=tool_models["add_memory"],
                tools=tool_definitions,
                tool_choice=tool_choice,
            )


@pytest.mark.asyncio
async def test_query_openai_sdk_malformed_tool_call_mode_semantics_difference(
    sample_memories,
):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False
    filter_instance.user_valves = filter_instance.UserValves()

    existing_ids = [m.mem_id for m in sample_memories]
    tool_models, tool_definitions, tool_choice = build_memory_action_tools(existing_ids)
    malformed_response = make_single_tool_call_response(
        tool_name="add_memory",
        raw_arguments="{not-json}",
    )

    with patch("auto_memory.OpenAI") as mock_openai_class:
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_client.chat.completions.create.return_value = malformed_response

        dict_mode_plan = await filter_instance.query_openai_sdk(
            system_prompt="test",
            user_message="test",
            response_model=tool_models,
            tools=tool_definitions,
            tool_choice=tool_choice,
        )

        assert isinstance(dict_mode_plan, MemoryActionRequestStub)
        assert dict_mode_plan.actions == []

        with pytest.raises(ValidationError):
            await filter_instance.query_openai_sdk(
                system_prompt="test",
                user_message="test",
                response_model=tool_models["add_memory"],
                tools=tool_definitions,
                tool_choice=tool_choice,
            )


@pytest.mark.asyncio
async def test_cleanup_expired_memories_with_no_expired_records_returns_zero(mock_user):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False

    with patch("auto_memory.MemoryExpiries") as mock_expiry_table:
        mock_expiry_table.get_expired.return_value = []

        deleted_count = await filter_instance.cleanup_expired_memories(user=mock_user)

    assert deleted_count == 0
    mock_expiry_table.delete_by_mem_id.assert_not_called()


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
    expected_expired_at = filter_instance._calculate_initial_expired_at(1000)
    mock_insert.assert_called_once_with(
        mem_id="mem-new-001",
        user_id=mock_user.id,
        expired_at=expected_expired_at,
        created_at=1000,
        hard_expire_at=1000 + (MAX_LIFETIME_DAYS * 86400),
        access_count=0,
        last_accessed_at=1000,
        last_decay_at=1000,
        strength=INITIAL_STRENGTH,
        cleanup_fail_count=0,
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


@pytest.mark.asyncio
async def test_delete_action_removes_expiry_tracking_on_success(
    mock_user, mock_emitter
):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False
    filter_instance.user_valves = filter_instance.UserValves()
    filter_instance.user_valves.show_status = False

    action_plan = MemoryActionRequestStub(
        actions=[MemoryDeleteAction(action="delete", id="mem-delete-001")]
    )

    with (
        patch.object(filter_instance, "_delete_memory_with_db", return_value=None),
        patch("auto_memory.MemoryExpiries.delete_by_mem_id") as mock_delete_tracking,
    ):
        await filter_instance.apply_memory_actions(
            action_plan=action_plan,
            user=mock_user,
            emitter=mock_emitter,
        )

    mock_delete_tracking.assert_called_once_with("mem-delete-001")


@pytest.mark.asyncio
async def test_delete_action_retains_tracking_and_counts_failures_on_error(
    mock_user, mock_emitter
):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False
    filter_instance.user_valves = filter_instance.UserValves()
    filter_instance.user_valves.show_status = False

    action_plan = MemoryActionRequestStub(
        actions=[MemoryDeleteAction(action="delete", id="mem-delete-002")]
    )

    tracked_record = SimpleNamespace(
        mem_id="mem-delete-002",
        cleanup_fail_count=0,
        expired_at=123,
    )

    with (
        patch.object(
            filter_instance,
            "_delete_memory_with_db",
            side_effect=RuntimeError("delete failed"),
        ),
        patch("auto_memory.MemoryExpiries.get_by_mem_id", return_value=tracked_record),
        patch("auto_memory.MemoryExpiries.update_expired_at") as mock_update,
        patch("auto_memory.MemoryExpiries.delete_by_mem_id") as mock_delete_tracking,
    ):
        await filter_instance.apply_memory_actions(
            action_plan=action_plan,
            user=mock_user,
            emitter=mock_emitter,
        )

    mock_update.assert_called_once_with(
        "mem-delete-002",
        123,
        cleanup_fail_count=1,
    )
    mock_delete_tracking.assert_not_called()


@pytest.mark.asyncio
async def test_delete_action_drops_tracking_after_retry_threshold(
    mock_user, mock_emitter
):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False
    filter_instance.user_valves = filter_instance.UserValves()
    filter_instance.user_valves.show_status = False

    action_plan = MemoryActionRequestStub(
        actions=[MemoryDeleteAction(action="delete", id="mem-delete-003")]
    )

    tracked_record = SimpleNamespace(
        mem_id="mem-delete-003",
        cleanup_fail_count=auto_memory_module.CLEANUP_DELETE_AFTER_FAILURES - 1,
        expired_at=123,
    )

    with (
        patch.object(
            filter_instance,
            "_delete_memory_with_db",
            side_effect=RuntimeError("delete failed"),
        ),
        patch("auto_memory.MemoryExpiries.get_by_mem_id", return_value=tracked_record),
        patch("auto_memory.MemoryExpiries.update_expired_at") as mock_update,
        patch("auto_memory.MemoryExpiries.delete_by_mem_id") as mock_delete_tracking,
    ):
        await filter_instance.apply_memory_actions(
            action_plan=action_plan,
            user=mock_user,
            emitter=mock_emitter,
        )

    mock_update.assert_not_called()
    mock_delete_tracking.assert_called_once_with("mem-delete-003")


@pytest.mark.asyncio
async def test_update_action_touches_expiry_tracking_when_row_exists(
    mock_user, mock_emitter
):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False
    filter_instance.user_valves = filter_instance.UserValves()
    filter_instance.user_valves.show_status = False

    action_plan = MemoryActionRequestStub(
        actions=[
            MemoryUpdateAction(
                action="update", id="mem-update-001", content="updated text"
            )
        ]
    )

    tracked_record = SimpleNamespace(mem_id="mem-update-001", expired_at=456)

    with (
        patch("auto_memory.update_memory_by_id", return_value=None),
        patch("auto_memory.MemoryExpiries.get_by_mem_id", return_value=tracked_record),
        patch("auto_memory.MemoryExpiries.update_expired_at") as mock_update,
    ):
        await filter_instance.apply_memory_actions(
            action_plan=action_plan,
            user=mock_user,
            emitter=mock_emitter,
        )

    mock_update.assert_called_once_with("mem-update-001", 456)


def test_inlet_injects_related_memories_into_messages(mock_emitter):
    filter_instance = Filter()
    memory = Memory(
        mem_id="mem-101",
        created_at=datetime(2026, 1, 1, 12, 0, 0),
        update_at=datetime(2026, 1, 1, 12, 0, 0),
        content="User prefers concise answers",
        similarity_score=0.91,
    )
    body = cast(
        dict[str, object],
        {
            "messages": [
                {"role": "system", "content": "Core system prompt"},
                {"role": "user", "content": "hi"},
            ],
        },
    )

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

    messages = cast(list[dict[str, Any]], updated["messages"])
    system_messages = [m for m in messages if m.get("role") == "system"]
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
    body = cast(
        dict[str, object],
        {"messages": [{"role": "user", "content": "test"}]},
    )

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

    messages = cast(list[dict[str, Any]], updated["messages"])
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[0]["content"].startswith(INLET_MEMORY_CONTEXT_PREFIX)
    assert messages[1] == {"role": "user", "content": "test"}


def test_inlet_supports_async_user_lookup(mock_emitter):
    filter_instance = Filter()
    body = cast(dict[str, object], {"messages": [{"role": "user", "content": "test"}]})

    async def mock_get_user(_user_id: str):
        return MagicMock(id="user-1")

    with (
        patch("auto_memory.Users.get_user_by_id", new=mock_get_user),
        patch(
            "auto_memory._run_async_in_thread",
            side_effect=lambda coro: (coro.close(), [])[1],
        ),
    ):
        updated = filter_instance.inlet(
            body=body,
            __event_emitter__=mock_emitter,
            __user__={"id": "user-1"},
        )

    assert updated is body


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
    """Test that cleanup_expired_memories returns count of deleted memories."""
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False

    # Mock expired records
    mock_record_1 = MagicMock()
    mock_record_1.mem_id = "mem-expired-001"
    mock_record_2 = MagicMock()
    mock_record_2.mem_id = "mem-expired-002"

    with (
        patch("auto_memory.MemoryExpiries") as mock_expiry_table,
        patch.object(
            filter_instance,
            "_delete_memory_sync",
            side_effect=[None, Exception("Vector DB error")],
        ),
        patch("time.time", return_value=2000),
    ):
        mock_expiry_table.get_expired.return_value = [mock_record_1, mock_record_2]
        mock_expiry_table.get_by_mem_id.return_value = SimpleNamespace(
            mem_id="mem-expired-002",
            cleanup_fail_count=1,
            expired_at=123,
        )
        mock_expiry_table.delete_by_mem_id.return_value = None

        deleted_count = await filter_instance.cleanup_expired_memories(user=mock_user)

    assert deleted_count == 1
    assert mock_expiry_table.update_expired_at.call_count == 1
    assert mock_expiry_table.delete_by_mem_id.call_count == 1


@pytest.mark.asyncio
async def test_cleanup_expired_memories_retains_cleanup_fail_count_before_threshold(
    mock_user,
):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False

    record = SimpleNamespace(
        mem_id="mem-cleanup-001",
        cleanup_fail_count=0,
        expired_at=123,
    )
    tracked_record = SimpleNamespace(
        mem_id="mem-cleanup-001",
        cleanup_fail_count=0,
        expired_at=123,
    )

    with (
        patch("auto_memory.MemoryExpiries") as mock_expiry_table,
        patch.object(
            filter_instance,
            "_delete_memory_sync",
            side_effect=Exception("Vector DB error"),
        ),
        patch("time.time", return_value=2000),
    ):
        mock_expiry_table.get_expired.return_value = [record]
        mock_expiry_table.get_by_mem_id.return_value = tracked_record
        mock_expiry_table.delete_by_mem_id.return_value = None

        deleted_count = await filter_instance.cleanup_expired_memories(user=mock_user)

    assert deleted_count == 0
    mock_expiry_table.update_expired_at.assert_called_once_with(
        "mem-cleanup-001",
        123,
        cleanup_fail_count=1,
    )
    mock_expiry_table.delete_by_mem_id.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_expired_memories_drops_cleanup_tracking_after_threshold(
    mock_user,
):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False

    threshold_fail_count = auto_memory_module.CLEANUP_DELETE_AFTER_FAILURES - 1
    record = SimpleNamespace(
        mem_id="mem-cleanup-002",
        cleanup_fail_count=threshold_fail_count,
        expired_at=123,
    )
    tracked_record = SimpleNamespace(
        mem_id="mem-cleanup-002",
        cleanup_fail_count=threshold_fail_count,
        expired_at=123,
    )

    with (
        patch("auto_memory.MemoryExpiries") as mock_expiry_table,
        patch.object(
            filter_instance,
            "_delete_memory_sync",
            side_effect=Exception("Vector DB error"),
        ),
        patch("time.time", return_value=2000),
    ):
        mock_expiry_table.get_expired.return_value = [record]
        mock_expiry_table.get_by_mem_id.return_value = tracked_record

        deleted_count = await filter_instance.cleanup_expired_memories(user=mock_user)

    assert deleted_count == 1
    mock_expiry_table.update_expired_at.assert_not_called()
    mock_expiry_table.delete_by_mem_id.assert_called_once_with("mem-cleanup-002")


@pytest.mark.asyncio
async def test_boost_memories_caps_to_absolute_hard_expiry(mock_user):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False

    memory = Memory(
        mem_id="mem-cap-001",
        created_at=datetime(2026, 1, 1, 12, 0, 0),
        update_at=datetime(2026, 1, 1, 12, 0, 0),
        content="cap test",
        similarity_score=0.9,
    )
    created_at_ts = 1000
    hard_expire_at_ts = created_at_ts + (30 * 86400)

    existing_record = SimpleNamespace(
        expired_at=created_at_ts + (29 * 86400),
        created_at=created_at_ts,
        hard_expire_at=hard_expire_at_ts,
        access_count=10,
        last_decay_at=created_at_ts + (29 * 86400),
        last_accessed_at=created_at_ts + (28 * 86400),
        strength=80.0,
        pinned=False,
    )

    with (
        patch(
            "auto_memory.MemoryExpiries.get_by_mem_id",
            return_value=existing_record,
        ),
        patch("auto_memory.MemoryExpiries.update_expired_at") as mock_update,
        patch("time.time", return_value=created_at_ts + (29 * 86400)),
    ):
        stats = await filter_instance.boost_memories([memory], mock_user)

    assert stats == {"total": 1, "boosted": 1, "created": 0}
    mock_update.assert_called_once()
    _mem_id, new_expired_at = mock_update.call_args.args
    assert _mem_id == "mem-cap-001"
    assert new_expired_at == hard_expire_at_ts


@pytest.mark.asyncio
async def test_boost_memories_keeps_hard_expiry_immutable_and_applies_decay(mock_user):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False

    created_at = datetime(2026, 1, 1, 0, 0, 0)
    memory = Memory(
        mem_id="mem-decay-001",
        created_at=created_at,
        update_at=created_at,
        content="decay test",
        similarity_score=0.95,
    )
    created_at_ts = int(created_at.timestamp())
    last_decay_at = created_at_ts + (10 * 86400)
    now_ts = last_decay_at + (5 * 86400)
    hard_expire_at = created_at_ts + (MAX_LIFETIME_DAYS * 86400)
    existing_record = SimpleNamespace(
        expired_at=now_ts + 86400,
        created_at=created_at_ts,
        hard_expire_at=hard_expire_at,
        access_count=4,
        last_accessed_at=last_decay_at - 3600,
        last_decay_at=last_decay_at,
        strength=30.0,
        pinned=False,
        cleanup_fail_count=1,
    )
    expected_strength = (30.0 - (5 * BASE_DECAY_PER_DAY)) + (
        ACCESS_GAIN / (1 + (4 * GAIN_DAMPING))
    )
    expected_expired_at = _expected_soft_expire_at(
        strength=expected_strength,
        now_timestamp=now_ts,
        hard_expire_at=hard_expire_at,
    )

    with (
        patch("auto_memory.MemoryExpiries.get_by_mem_id", return_value=existing_record),
        patch("auto_memory.MemoryExpiries.update_expired_at") as mock_update,
        patch("time.time", return_value=now_ts),
    ):
        stats = await filter_instance.boost_memories([memory], mock_user)

    assert stats == {"total": 1, "boosted": 1, "created": 0}
    assert mock_update.call_args.args == ("mem-decay-001", expected_expired_at)
    assert mock_update.call_args.kwargs == {
        "strength": expected_strength,
        "access_count": 5,
        "last_accessed_at": now_ts,
        "last_decay_at": now_ts,
        "cleanup_fail_count": 0,
    }


@pytest.mark.asyncio
async def test_boost_memories_applies_burst_damping_for_rapid_repeat_access(mock_user):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False

    created_at = datetime(2026, 1, 1, 0, 0, 0)
    memory = Memory(
        mem_id="mem-burst-001",
        created_at=created_at,
        update_at=created_at,
        content="burst test",
        similarity_score=0.91,
    )
    created_at_ts = int(created_at.timestamp())
    now_ts = created_at_ts + 86400
    recent_access_ts = now_ts - (BURST_WINDOW_MINUTES * 60) + 60
    existing_record = SimpleNamespace(
        expired_at=now_ts + 86400,
        created_at=created_at_ts,
        hard_expire_at=created_at_ts + (MAX_LIFETIME_DAYS * 86400),
        access_count=0,
        last_accessed_at=recent_access_ts,
        last_decay_at=now_ts,
        strength=INITIAL_STRENGTH,
        pinned=False,
        cleanup_fail_count=0,
    )
    expected_strength = INITIAL_STRENGTH + (ACCESS_GAIN * BURST_GAIN_MULTIPLIER)
    expected_expired_at = _expected_soft_expire_at(
        strength=expected_strength,
        now_timestamp=now_ts,
        hard_expire_at=created_at_ts + (MAX_LIFETIME_DAYS * 86400),
    )

    with (
        patch("auto_memory.MemoryExpiries.get_by_mem_id", return_value=existing_record),
        patch("auto_memory.MemoryExpiries.update_expired_at") as mock_update,
        patch("time.time", return_value=now_ts),
    ):
        stats = await filter_instance.boost_memories([memory], mock_user)

    assert stats == {"total": 1, "boosted": 1, "created": 0}
    assert mock_update.call_args.kwargs["strength"] == expected_strength
    assert mock_update.call_args.args == ("mem-burst-001", expected_expired_at)


def test_memory_expiry_table_get_expired_pushes_limit_order_and_filters_to_sql():
    table = MemoryExpiryTable()
    mock_db = MagicMock()
    query = mock_db.query.return_value
    query.filter.return_value = query
    query.order_by.return_value = query
    query.limit.return_value = query
    query.all.return_value = ["candidate"]

    with patch("auto_memory.get_db_context") as mock_context:
        mock_context.return_value.__enter__.return_value = mock_db
        result = table.get_expired(
            user_id="user-1",
            now_timestamp=1234,
            limit=7,
        )

    assert result == ["candidate"]
    query.filter.assert_called_once()
    query.order_by.assert_called_once()
    query.limit.assert_called_once_with(7)
    query.all.assert_called_once_with()


@pytest.mark.asyncio
async def test_cleanup_expired_memories_no_candidates_is_noop(mock_user):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False

    with patch.object(
        filter_instance,
        "_build_memory_maintenance_candidates",
        return_value=[],
    ) as mock_candidates:
        deleted_count = await filter_instance.cleanup_expired_memories(mock_user)

    assert deleted_count == 0
    mock_candidates.assert_called_once_with(
        user_id=mock_user.id,
        now_timestamp=mock_candidates.call_args.kwargs["now_timestamp"],
        limit=MAINTENANCE_BATCH_SIZE,
    )


@pytest.mark.asyncio
async def test_boost_memories_backfills_legacy_record_fields(mock_user):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False

    memory = Memory(
        mem_id="mem-legacy-001",
        created_at=datetime(2026, 1, 1, 12, 0, 0),
        update_at=datetime(2026, 1, 1, 12, 0, 0),
        content="legacy test",
        similarity_score=0.88,
    )
    created_at_ts = 1000
    now_ts = 2000
    existing_record = SimpleNamespace(
        expired_at=now_ts + (5 * 86400),
        created_at=created_at_ts,
    )

    with (
        patch(
            "auto_memory.MemoryExpiries.get_by_mem_id",
            return_value=existing_record,
        ),
        patch("auto_memory.MemoryExpiries.update_expired_at") as mock_update,
        patch("time.time", return_value=now_ts),
    ):
        stats = await filter_instance.boost_memories([memory], mock_user)

    assert stats == {"total": 1, "boosted": 1, "created": 0}
    expected_strength = INITIAL_STRENGTH + ACCESS_GAIN
    expected_expired_at = _expected_soft_expire_at(
        strength=expected_strength,
        now_timestamp=now_ts,
        hard_expire_at=created_at_ts + (MAX_LIFETIME_DAYS * 86400),
    )
    assert mock_update.call_args.args == ("mem-legacy-001", expected_expired_at)
    assert mock_update.call_args.kwargs == {
        "strength": expected_strength,
        "access_count": 1,
        "last_accessed_at": now_ts,
        "last_decay_at": now_ts,
        "cleanup_fail_count": 0,
    }


@pytest.mark.asyncio
async def test_boost_memories_reconstructs_missing_record_from_created_at(mock_user):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False

    memory_created_at = datetime(2020, 1, 1, 0, 0, 0)
    memory = Memory(
        mem_id="mem-rebuild-001",
        created_at=memory_created_at,
        update_at=memory_created_at,
        content="missing record",
        similarity_score=0.91,
    )

    created_at_ts = int(memory_created_at.timestamp())
    now_ts = int(datetime(2020, 1, 5, 0, 0, 0).timestamp())
    expected_hard_expire_at = created_at_ts + (MAX_LIFETIME_DAYS * 86400)
    expected_expired_at = _expected_soft_expire_at(
        strength=INITIAL_STRENGTH,
        now_timestamp=now_ts,
        hard_expire_at=expected_hard_expire_at,
    )

    with (
        patch("auto_memory.MemoryExpiries.get_by_mem_id", return_value=None),
        patch("auto_memory.MemoryExpiries.insert") as mock_insert,
        patch("time.time", return_value=now_ts),
    ):
        stats = await filter_instance.boost_memories([memory], mock_user)

    assert stats == {"total": 1, "boosted": 0, "created": 1}
    assert mock_insert.call_args.kwargs == {
        "mem_id": "mem-rebuild-001",
        "user_id": mock_user.id,
        "expired_at": expected_expired_at,
        "created_at": created_at_ts,
        "hard_expire_at": expected_hard_expire_at,
        "access_count": 1,
        "last_accessed_at": now_ts,
        "last_decay_at": now_ts,
        "strength": INITIAL_STRENGTH,
        "cleanup_fail_count": 0,
    }


@pytest.mark.asyncio
async def test_boost_memories_keeps_hard_cap_immutable_across_repeated_accesses(
    mock_user,
):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False

    memory = Memory(
        mem_id="mem-cap-repeat-001",
        created_at=datetime(2026, 1, 1, 12, 0, 0),
        update_at=datetime(2026, 1, 1, 12, 0, 0),
        content="cap test",
        similarity_score=0.9,
    )
    created_at_ts = 1000
    hard_expire_at_ts = created_at_ts + (30 * 86400)
    existing_record = SimpleNamespace(
        expired_at=created_at_ts + (29 * 86400),
        created_at=created_at_ts,
        hard_expire_at=hard_expire_at_ts,
    )

    with (
        patch("auto_memory.MemoryExpiries.get_by_mem_id", return_value=existing_record),
        patch("auto_memory.MemoryExpiries.update_expired_at") as mock_update,
        patch("time.time", return_value=created_at_ts + (29 * 86400)),
    ):
        first_stats = await filter_instance.boost_memories([memory], mock_user)
        second_stats = await filter_instance.boost_memories([memory], mock_user)

    assert first_stats == {"total": 1, "boosted": 1, "created": 0}
    assert second_stats == {"total": 1, "boosted": 1, "created": 0}
    assert mock_update.call_count == 2
    assert mock_update.call_args_list[0].args == (
        "mem-cap-repeat-001",
        hard_expire_at_ts,
    )
    assert mock_update.call_args_list[1].args == (
        "mem-cap-repeat-001",
        hard_expire_at_ts,
    )


@pytest.mark.asyncio
async def test_cleanup_expired_memories_returns_zero_and_skips_deletes_when_empty(
    mock_user,
):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False

    with patch("auto_memory.MemoryExpiries") as mock_expiry_table:
        mock_expiry_table.get_expired.return_value = []

        deleted_count = await filter_instance.cleanup_expired_memories(user=mock_user)

    assert deleted_count == 0
    mock_expiry_table.delete_by_mem_id.assert_not_called()


def test_burst_multiplier_only_applies_inside_burst_window():
    inside_window = auto_memory_module._calculate_burst_multiplier(
        last_accessed_at=1000,
        now_timestamp=1000 + (auto_memory_module.BURST_WINDOW_MINUTES * 60) - 1,
    )
    outside_window = auto_memory_module._calculate_burst_multiplier(
        last_accessed_at=1000,
        now_timestamp=1000 + (auto_memory_module.BURST_WINDOW_MINUTES * 60) + 1,
    )

    assert inside_window == auto_memory_module.BURST_GAIN_MULTIPLIER
    assert outside_window == 1.0


def test_searchresults_to_memories_accepts_updated_at_and_update_at():
    results = SimpleNamespace(
        ids=[["mem-1", "mem-2"]],
        documents=[["first", "second"]],
        metadatas=[
            [
                {"created_at": 100, "updated_at": 200},
                {"created_at": 300, "update_at": 400},
            ]
        ],
        distances=[[0.1234, 0.5678]],
    )

    memories = auto_memory_module.searchresults_to_memories(cast(Any, results))

    assert [memory.mem_id for memory in memories] == ["mem-1", "mem-2"]
    assert memories[0].created_at == datetime.fromtimestamp(100)
    assert memories[0].update_at == datetime.fromtimestamp(200)
    assert memories[1].created_at == datetime.fromtimestamp(300)
    assert memories[1].update_at == datetime.fromtimestamp(400)
    assert memories[0].similarity_score == 0.123
    assert memories[1].similarity_score == 0.568


def test_lifecycle_bootstrap_backfills_null_and_missing_columns():
    with (
        patch.object(auto_memory_module.engine, "begin") as mock_engine_begin,
        patch.object(auto_memory_module, "inspect") as mock_inspect,
    ):
        mock_connection = MagicMock()
        mock_engine_begin.return_value.__enter__.return_value = mock_connection
        mock_inspector = MagicMock()
        mock_inspector.get_columns.return_value = [
            {"name": name}
            for name in (
                "mem_id",
                "user_id",
                "expired_at",
                "created_at",
                "updated_at",
            )
        ]
        mock_inspect.return_value = mock_inspector

        auto_memory_module._ensure_lifecycle_columns()

    executed_sql = [
        call.args[0].text for call in mock_connection.execute.call_args_list
    ]
    assert any(
        "ALTER TABLE auto_memory_expiry ADD COLUMN hard_expire_at BIGINT" in sql
        for sql in executed_sql
    )
    assert any(
        f"UPDATE auto_memory_expiry SET hard_expire_at = created_at + {MAX_LIFETIME_DAYS * 86400} WHERE hard_expire_at IS NULL"
        in sql
        for sql in executed_sql
    )
    assert any(
        "UPDATE auto_memory_expiry SET access_count = 0 WHERE access_count IS NULL"
        in sql
        for sql in executed_sql
    )
    assert any(
        "UPDATE auto_memory_expiry SET last_accessed_at = updated_at WHERE last_accessed_at IS NULL"
        in sql
        for sql in executed_sql
    )
    assert any(
        "UPDATE auto_memory_expiry SET last_decay_at = updated_at WHERE last_decay_at IS NULL"
        in sql
        for sql in executed_sql
    )
    assert any(
        f"UPDATE auto_memory_expiry SET strength = {INITIAL_STRENGTH} WHERE strength IS NULL"
        in sql
        for sql in executed_sql
    )
    assert any(
        "UPDATE auto_memory_expiry SET pinned = 0 WHERE pinned IS NULL" in sql
        for sql in executed_sql
    )


def test_memory_expiry_get_expired_pushes_user_and_time_filter_down_to_sql():
    table = auto_memory_module.MemoryExpiryTable()
    mock_session = MagicMock()
    mock_query = MagicMock()
    mock_filter = MagicMock()
    mock_ordered = MagicMock()
    mock_limited = MagicMock()
    sentinel = object()
    mock_query.filter.return_value = mock_filter
    mock_filter.order_by.return_value = mock_ordered
    mock_ordered.limit.return_value = mock_limited
    mock_limited.all.return_value = [sentinel]
    mock_session.query.return_value = mock_query

    with patch("auto_memory.get_db_context") as mock_get_db_context:
        mock_get_db_context.return_value.__enter__.return_value = mock_session
        expired = table.get_expired(user_id="user-1", now_timestamp=1234)

    assert expired == [sentinel]
    mock_session.query.assert_called_once_with(auto_memory_module.MemoryExpiry)
    mock_query.filter.assert_called_once()
    mock_filter.order_by.assert_called_once()
    mock_ordered.limit.assert_called_once_with(
        auto_memory_module.MAINTENANCE_BATCH_SIZE
    )
    filter_args = mock_query.filter.call_args.args
    assert len(filter_args) == 3
    assert str(filter_args[0]) == str(
        auto_memory_module.MemoryExpiry.user_id == "user-1"
    )
    assert str(filter_args[1]) == str(auto_memory_module.MemoryExpiry.pinned.is_(False))


def test_memory_expiry_get_expired_pushes_limit_down_to_sql():
    table = auto_memory_module.MemoryExpiryTable()
    mock_session = MagicMock()
    mock_query = MagicMock()
    mock_filtered = MagicMock()
    mock_limited = MagicMock()
    mock_query.filter.return_value = mock_filtered
    mock_filtered.order_by.return_value = mock_limited
    mock_limited.limit.return_value = mock_limited
    mock_limited.all.return_value = []
    mock_session.query.return_value = mock_query

    with patch("auto_memory.get_db_context") as mock_get_db_context:
        mock_get_db_context.return_value.__enter__.return_value = mock_session
        table.get_expired(user_id="user-1", now_timestamp=1234, limit=7)

    mock_limited.limit.assert_called_once_with(7)


def test_cleanup_candidate_removal_trips_when_retry_threshold_reached():
    record = SimpleNamespace(
        hard_expire_at=9999999999,
        expired_at=0,
        strength=1.0,
        cleanup_fail_count=auto_memory_module.CLEANUP_DELETE_AFTER_FAILURES,
    )

    assert (
        auto_memory_module._should_delete_maintenance_candidate(
            record, now_timestamp=1234
        )
        is True
    )


@pytest.mark.asyncio
async def test_cleanup_expired_memories_respects_batch_size(mock_user):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False

    records = []
    for index in range(3):
        record = MagicMock()
        record.mem_id = f"mem-batch-{index}"
        records.append(record)

    with (
        patch("auto_memory.MemoryExpiries") as mock_expiry_table,
        patch.object(filter_instance, "_delete_memory_sync", return_value=None),
        patch("time.time", return_value=2000),
    ):
        mock_expiry_table.get_expired.return_value = records[:1]
        deleted_count = await filter_instance.cleanup_expired_memories(user=mock_user)

    assert deleted_count == 1
    assert mock_expiry_table.delete_by_mem_id.call_count == 1


@pytest.mark.asyncio
async def test_cleanup_expired_memories_continues_after_vector_delete_failure(
    mock_user,
):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False

    record = MagicMock()
    record.mem_id = "mem-fail-001"

    with (
        patch("auto_memory.MemoryExpiries") as mock_expiry_table,
        patch.object(
            filter_instance,
            "_delete_memory_sync",
            side_effect=Exception("Vector DB timeout"),
        ),
        patch("time.time", return_value=2000),
    ):
        mock_expiry_table.get_expired.return_value = [record]
        mock_expiry_table.get_by_mem_id.return_value = SimpleNamespace(
            mem_id="mem-fail-001",
            cleanup_fail_count=0,
            expired_at=123,
        )

        deleted_count = await filter_instance.cleanup_expired_memories(user=mock_user)

    assert deleted_count == 0
    mock_expiry_table.update_expired_at.assert_called_once_with(
        "mem-fail-001",
        123,
        cleanup_fail_count=1,
    )
    mock_expiry_table.delete_by_mem_id.assert_not_called()


@pytest.mark.asyncio
async def test_boost_memories_missing_record_uses_memory_created_at_for_hard_cap(
    mock_user,
):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False

    memory_created_at = datetime(2020, 1, 1, 0, 0, 0)
    memory = Memory(
        mem_id="mem-missing-001",
        created_at=memory_created_at,
        update_at=memory_created_at,
        content="missing record",
        similarity_score=0.9,
    )
    now_ts = int(datetime(2020, 1, 5, 0, 0, 0).timestamp())
    created_ts = int(memory_created_at.timestamp())
    expected_hard_expire_at = created_ts + (MAX_LIFETIME_DAYS * 86400)
    expected_soft_expire_at = _expected_soft_expire_at(
        strength=INITIAL_STRENGTH,
        now_timestamp=now_ts,
        hard_expire_at=expected_hard_expire_at,
    )

    with (
        patch("auto_memory.MemoryExpiries.get_by_mem_id", return_value=None),
        patch("auto_memory.MemoryExpiries.insert") as mock_insert,
        patch("time.time", return_value=now_ts),
    ):
        stats = await filter_instance.boost_memories([memory], mock_user)

    assert stats == {"total": 1, "boosted": 0, "created": 1}
    assert mock_insert.call_args.args == ()
    assert mock_insert.call_args.kwargs == {
        "mem_id": "mem-missing-001",
        "user_id": mock_user.id,
        "expired_at": expected_soft_expire_at,
        "created_at": created_ts,
        "hard_expire_at": expected_hard_expire_at,
        "access_count": 1,
        "last_accessed_at": now_ts,
        "last_decay_at": now_ts,
        "strength": INITIAL_STRENGTH,
        "cleanup_fail_count": 0,
    }


@pytest.mark.asyncio
async def test_boost_memories_empty_input_returns_zero_stats(mock_user):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False

    stats = await filter_instance.boost_memories([], mock_user)

    assert stats == {"total": 0, "boosted": 0, "created": 0}


@pytest.mark.asyncio
async def test_apply_memory_actions_continues_after_partial_failure(
    mock_user, mock_emitter
):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False
    filter_instance.user_valves = filter_instance.UserValves()
    filter_instance.user_valves.show_status = False

    action_plan = MemoryActionRequestStub(
        actions=[
            MemoryDeleteAction(action="delete", id="mem-001"),
            MemoryUpdateAction(action="update", id="mem-002", content="updated"),
            MemoryAddAction(action="add", content="new memory"),
        ]
    )

    mutation_calls = []

    async def mock_delete_with_db(action, user):
        raise RuntimeError("delete failed")

    async def mock_update(memory_id, request, form_data, user):
        mutation_calls.append(("update", memory_id, form_data.content))

    async def mock_add_with_expiry(action, user):
        mutation_calls.append(("add", action.content))

    with (
        patch.object(
            filter_instance, "_delete_memory_with_db", new=mock_delete_with_db
        ),
        patch("auto_memory.update_memory_by_id", new=mock_update),
        patch.object(
            filter_instance, "_add_memory_with_expiry", new=mock_add_with_expiry
        ),
    ):
        await filter_instance.apply_memory_actions(
            action_plan=action_plan,
            user=mock_user,
            emitter=mock_emitter,
        )

    assert mutation_calls == [
        ("update", "mem-002", "updated"),
        ("add", "new memory"),
    ]


@pytest.mark.asyncio
async def test_apply_memory_actions_skips_blank_update_and_add(mock_user, mock_emitter):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False
    filter_instance.user_valves = filter_instance.UserValves()
    filter_instance.user_valves.show_status = False

    action_plan = MemoryActionRequestStub(
        actions=[
            MemoryUpdateAction(action="update", id="mem-001", content="   \n\t  "),
            MemoryAddAction(action="add", content="  "),
            MemoryDeleteAction(action="delete", id="mem-002"),
        ]
    )

    mutation_calls = []

    async def mock_delete_with_db(action, user):
        mutation_calls.append(("delete", action.id))

    async def mock_update(memory_id, request, form_data, user):
        mutation_calls.append(("update", memory_id, form_data.content))

    async def mock_add_with_expiry(action, user):
        mutation_calls.append(("add", action.content))

    with (
        patch.object(
            filter_instance, "_delete_memory_with_db", new=mock_delete_with_db
        ),
        patch("auto_memory.update_memory_by_id", new=mock_update),
        patch.object(
            filter_instance, "_add_memory_with_expiry", new=mock_add_with_expiry
        ),
    ):
        await filter_instance.apply_memory_actions(
            action_plan=action_plan,
            user=mock_user,
            emitter=mock_emitter,
        )

    assert mutation_calls == [("delete", "mem-002")]


@pytest.mark.asyncio
async def test_auto_memory_truncates_existing_ids_at_tool_schema_limit(mock_user):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False
    filter_instance.user_valves = filter_instance.UserValves()
    filter_instance.user_valves.show_status = False

    memories = [
        Memory(
            mem_id=f"mem-{index:03d}",
            created_at=datetime(2026, 1, 1, 0, 0, 0),
            update_at=datetime(2026, 1, 1, 0, 0, 0),
            content=f"memory {index}",
            similarity_score=0.9,
        )
        for index in range(51)
    ]

    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]

    with (
        patch.object(
            filter_instance,
            "get_related_memories",
            new=AsyncMock(return_value=memories),
        ),
        patch.object(
            filter_instance,
            "boost_memories",
            new=AsyncMock(return_value={"total": 51, "boosted": 0, "created": 0}),
        ),
        patch.object(
            filter_instance, "cleanup_expired_memories", new=AsyncMock(return_value=0)
        ),
        patch("auto_memory.build_memory_action_tools") as mock_build_tools,
        patch.object(
            filter_instance,
            "query_openai_sdk",
            new=AsyncMock(return_value=MemoryActionRequestStub(actions=[])),
        ),
        patch.object(
            filter_instance, "apply_memory_actions", new=AsyncMock()
        ) as mock_apply,
    ):
        mock_build_tools.return_value = ({}, [], "auto")

        await filter_instance.auto_memory(
            messages=messages,
            user=mock_user,
            emitter=AsyncMock(),
        )

    assert mock_build_tools.call_count == 1
    truncated_ids = mock_build_tools.call_args.args[0]
    assert len(truncated_ids) == 50
    assert truncated_ids == [f"mem-{index:03d}" for index in range(50)]
    mock_apply.assert_not_called()


@pytest.mark.asyncio
async def test_auto_memory_runs_lifecycle_and_maintenance_before_planning_and_apply(
    mock_user,
):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False
    filter_instance.user_valves = filter_instance.UserValves()
    filter_instance.user_valves.show_status = False

    related_memories = [
        Memory(
            mem_id="mem-order-001",
            created_at=datetime(2026, 1, 1, 0, 0, 0),
            update_at=datetime(2026, 1, 1, 0, 0, 0),
            content="ordering test",
            similarity_score=0.9,
        )
    ]
    action_plan = MemoryActionRequestStub(
        actions=[MemoryAddAction(action="add", content="store new fact")]
    )
    call_order: list[str] = []

    async def mock_get_related_memories(*args, **kwargs):
        call_order.append("related")
        return related_memories

    async def mock_boost_memories(memories, user):
        assert memories == related_memories
        call_order.append("boost")
        return {"total": 1, "boosted": 1, "created": 0}

    async def mock_cleanup_expired_memories(user):
        call_order.append("cleanup")
        return 1

    async def mock_plan_memory_actions(messages, memories):
        assert memories == related_memories
        call_order.append("plan")
        return action_plan

    async def mock_apply_memory_actions(action_plan, user, emitter):
        assert action_plan.actions[0].action == "add"
        call_order.append("apply")

    with (
        patch.object(
            filter_instance,
            "get_related_memories",
            new=mock_get_related_memories,
        ),
        patch.object(filter_instance, "boost_memories", new=mock_boost_memories),
        patch.object(
            filter_instance,
            "cleanup_expired_memories",
            new=mock_cleanup_expired_memories,
        ),
        patch.object(
            filter_instance,
            "_plan_memory_actions",
            new=mock_plan_memory_actions,
        ),
        patch.object(
            filter_instance,
            "apply_memory_actions",
            new=mock_apply_memory_actions,
        ),
    ):
        await filter_instance.auto_memory(
            messages=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
            user=mock_user,
            emitter=AsyncMock(),
        )

    assert call_order == ["related", "boost", "cleanup", "plan", "apply"]


@pytest.mark.asyncio
async def test_auto_memory_zero_candidate_maintenance_is_noop_before_planning(
    mock_user,
):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False
    filter_instance.user_valves = filter_instance.UserValves()
    filter_instance.user_valves.show_status = False

    related_memories = [
        Memory(
            mem_id="mem-order-002",
            created_at=datetime(2026, 1, 1, 0, 0, 0),
            update_at=datetime(2026, 1, 1, 0, 0, 0),
            content="ordering test two",
            similarity_score=0.91,
        )
    ]
    call_order: list[str] = []
    mock_apply = AsyncMock()

    async def mock_get_related_memories(*args, **kwargs):
        call_order.append("related")
        return related_memories

    async def mock_boost_memories(memories, user):
        call_order.append("boost")
        return {"total": 1, "boosted": 1, "created": 0}

    async def mock_cleanup_expired_memories(user):
        call_order.append("cleanup")
        return 0

    async def mock_plan_memory_actions(messages, memories):
        call_order.append("plan")
        return MemoryActionRequestStub(actions=[])

    with (
        patch.object(
            filter_instance,
            "get_related_memories",
            new=mock_get_related_memories,
        ),
        patch.object(filter_instance, "boost_memories", new=mock_boost_memories),
        patch.object(
            filter_instance,
            "cleanup_expired_memories",
            new=mock_cleanup_expired_memories,
        ),
        patch.object(
            filter_instance,
            "_plan_memory_actions",
            new=mock_plan_memory_actions,
        ),
        patch.object(filter_instance, "apply_memory_actions", new=mock_apply),
    ):
        await filter_instance.auto_memory(
            messages=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
            user=mock_user,
            emitter=AsyncMock(),
        )

    assert call_order == ["related", "boost", "cleanup", "plan"]
    mock_apply.assert_not_called()


def test_import_initialization_calls_create_all_non_intrusive():
    with patch("open_webui.internal.db.Base.metadata.create_all") as mock_create_all:
        with (
            patch("sqlalchemy.inspect") as mock_inspect,
            patch("open_webui.internal.db.engine.begin") as mock_engine_begin,
        ):
            mock_connection = MagicMock()
            mock_engine_begin.return_value.__enter__.return_value = mock_connection
            mock_inspector = MagicMock()
            mock_inspector.get_columns.return_value = [
                {"name": name}
                for name in (
                    "mem_id",
                    "user_id",
                    "expired_at",
                    "created_at",
                    "updated_at",
                    "hard_expire_at",
                    "access_count",
                    "last_accessed_at",
                    "last_decay_at",
                    "strength",
                    "pinned",
                )
            ]
            mock_inspect.return_value = mock_inspector

            importlib.reload(auto_memory_module)

    mock_create_all.assert_called_once()
    assert mock_create_all.call_args.kwargs["checkfirst"] is True
    assert "tables" in mock_create_all.call_args.kwargs


def test_lifecycle_bootstrap_logs_and_fails_on_schema_errors():
    with (
        patch("auto_memory.Base.metadata.create_all", side_effect=RuntimeError("boom")),
        patch("auto_memory.logging.getLogger") as mock_get_logger,
    ):
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        from auto_memory import _ensure_table_exists

        result = _ensure_table_exists()

    assert result is False
    mock_logger.exception.assert_called_once()


def test_import_bootstrap_keeps_memoryexpiries_before_init_calls():
    source = inspect.getsource(auto_memory_module)
    module = ast.parse(source)

    memory_expiries_line = None
    ensure_table_def_line = None
    ensure_table_call_line = None
    ensure_lifecycle_def_line = None
    ensure_lifecycle_call_line = None
    ensure_bootstrap_def_line = None
    ensure_bootstrap_call_line = None

    for node in module.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "MemoryExpiries"
            for target in node.targets
        ):
            memory_expiries_line = node.lineno
        elif isinstance(node, ast.FunctionDef) and node.name == "_ensure_table_exists":
            ensure_table_def_line = node.lineno
        elif (
            isinstance(node, ast.FunctionDef)
            and node.name == "_ensure_lifecycle_columns"
        ):
            ensure_lifecycle_def_line = node.lineno
        elif (
            isinstance(node, ast.FunctionDef)
            and node.name == "_ensure_memory_expiry_lifecycle_bootstrap"
        ):
            ensure_bootstrap_def_line = node.lineno
        elif (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "_ensure_memory_expiry_lifecycle_bootstrap"
        ):
            ensure_bootstrap_call_line = node.lineno

    assert memory_expiries_line is not None
    assert ensure_table_def_line is not None
    assert ensure_lifecycle_def_line is not None
    assert ensure_bootstrap_def_line is not None
    assert ensure_bootstrap_call_line is not None
    assert memory_expiries_line < ensure_table_def_line
    assert ensure_table_def_line < ensure_lifecycle_def_line < ensure_bootstrap_def_line
    assert ensure_bootstrap_def_line < ensure_bootstrap_call_line


@pytest.mark.asyncio
async def test_boost_memories_backfills_runtime_defaults_for_legacy_records(mock_user):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False

    memory = Memory(
        mem_id="mem-legacy-002",
        created_at=datetime(2026, 1, 1, 12, 0, 0),
        update_at=datetime(2026, 1, 1, 12, 0, 0),
        content="legacy defaults",
        similarity_score=0.91,
    )
    now_ts = 2000
    legacy_record = SimpleNamespace(expired_at=now_ts + 86400, created_at=1000)

    with (
        patch(
            "auto_memory._ensure_memory_expiry_lifecycle_bootstrap", return_value=True
        ),
        patch("auto_memory.MemoryExpiries.get_by_mem_id", return_value=legacy_record),
        patch("auto_memory.MemoryExpiries.update_expired_at") as mock_update,
        patch("time.time", return_value=now_ts),
    ):
        stats = await filter_instance.boost_memories([memory], mock_user)

    assert stats == {"total": 1, "boosted": 1, "created": 0}
    expected_expired_at = _expected_soft_expire_at(
        strength=INITIAL_STRENGTH + ACCESS_GAIN,
        now_timestamp=now_ts,
        hard_expire_at=1000 + (MAX_LIFETIME_DAYS * 86400),
    )
    assert mock_update.call_args.args == ("mem-legacy-002", expected_expired_at)


@pytest.mark.asyncio
async def test_boost_memories_clamps_strength_before_soft_expiry(mock_user):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False

    created_at_ts = 1000
    now_ts = created_at_ts + 86400
    hard_expire_at = created_at_ts + (MAX_LIFETIME_DAYS * 86400)
    memory = Memory(
        mem_id="mem-clamp-001",
        created_at=datetime.fromtimestamp(created_at_ts),
        update_at=datetime.fromtimestamp(created_at_ts),
        content="clamp test",
        similarity_score=0.9,
    )
    existing_record = SimpleNamespace(
        expired_at=now_ts + 86400,
        created_at=created_at_ts,
        hard_expire_at=hard_expire_at,
        access_count=0,
        last_accessed_at=now_ts - 7200,
        last_decay_at=now_ts,
        strength=99.0,
        pinned=False,
        cleanup_fail_count=0,
    )

    with (
        patch("auto_memory.MemoryExpiries.get_by_mem_id", return_value=existing_record),
        patch("auto_memory.MemoryExpiries.update_expired_at") as mock_update,
        patch("time.time", return_value=now_ts),
    ):
        stats = await filter_instance.boost_memories([memory], mock_user)

    assert stats == {"total": 1, "boosted": 1, "created": 0}
    assert mock_update.call_args.kwargs["strength"] == 100.0
    assert mock_update.call_args.args == (
        "mem-clamp-001",
        _expected_soft_expire_at(100.0, now_ts, hard_expire_at),
    )


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
