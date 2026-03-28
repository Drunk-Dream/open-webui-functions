"""
Unit tests for tool-calling planner and mutation guardrails in auto_memory.py.

Tests cover:
1. Valid mixed actions trigger deterministic delete->update->add execution order
2. Invalid update/delete ID rejected and no mutations
3. Provider response without tool_calls is treated as no-op and no mutations
4. Tool args with extra keys rejected by strict schema and no mutations
"""

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
        mock_expiry_table.delete_by_mem_id.return_value = None

        deleted_count = await filter_instance.cleanup_expired_memories(user=mock_user)

    assert deleted_count == 2
    assert mock_expiry_table.delete_by_mem_id.call_count == 2


@pytest.mark.asyncio
async def test_boost_memories_caps_to_absolute_hard_expiry(mock_user):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False
    filter_instance.valves.max_expiry_days = 30
    filter_instance.valves.extension_days = 14

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
async def test_boost_memories_backfills_legacy_record_fields(mock_user):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False
    filter_instance.valves.max_expiry_days = 30

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
    assert mock_update.call_args.args == ("mem-legacy-001", now_ts + (19 * 86400))


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

        deleted_count = await filter_instance.cleanup_expired_memories(user=mock_user)

    assert deleted_count == 1
    mock_expiry_table.delete_by_mem_id.assert_called_once_with("mem-fail-001")


@pytest.mark.asyncio
async def test_boost_memories_missing_record_uses_memory_created_at_for_hard_cap(
    mock_user,
):
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False
    filter_instance.valves.max_expiry_days = 30
    filter_instance.valves.initial_expiry_days = 10

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
    expected_hard_expire_at = created_ts + (30 * 86400)
    expected_soft_expire_at = min(
        now_ts + (filter_instance.valves.initial_expiry_days * 86400),
        expected_hard_expire_at,
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


def test_import_bootstrap_keeps_memoryexpiries_before_init_calls():
    source = inspect.getsource(auto_memory_module)
    module = ast.parse(source)

    memory_expiries_line = None
    ensure_table_def_line = None
    ensure_table_call_line = None
    ensure_lifecycle_def_line = None
    ensure_lifecycle_call_line = None

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
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "_ensure_table_exists"
        ):
            ensure_table_call_line = node.lineno
        elif (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "_ensure_lifecycle_columns"
        ):
            ensure_lifecycle_call_line = node.lineno

    assert memory_expiries_line is not None
    assert ensure_table_def_line is not None
    assert ensure_table_call_line is not None
    assert ensure_lifecycle_def_line is not None
    assert ensure_lifecycle_call_line is not None
    assert memory_expiries_line < ensure_table_def_line < ensure_table_call_line
    assert memory_expiries_line < ensure_lifecycle_def_line < ensure_lifecycle_call_line
    assert ensure_table_call_line < ensure_lifecycle_call_line


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
