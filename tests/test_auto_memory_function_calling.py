"""
Unit tests for tool-calling planner and mutation guardrails in auto_memory.py.

Tests cover:
1. Valid mixed actions trigger deterministic delete->update->add execution order
2. Invalid update/delete ID rejected and no mutations
3. Provider response without tool_calls hard-fails and no mutations
4. Tool args with extra keys rejected by strict schema and no mutations
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)
from pydantic import ValidationError

from auto_memory import Filter, Memory, build_memory_actions_tool


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


def make_tool_call_response(
    tool_name: str, arguments_dict: dict[str, Any]
) -> ChatCompletion:
    """Helper to construct a ChatCompletion with tool_calls."""
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
                    tool_calls=[
                        ChatCompletionMessageToolCall(
                            id="call_abc123",
                            type="function",
                            function=Function(
                                name=tool_name,
                                arguments=json.dumps(arguments_dict),
                            ),
                        )
                    ],
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

    # Build tool with existing IDs
    existing_ids = [m.mem_id for m in sample_memories]
    ActionsModel, tool_definition, tool_choice = build_memory_actions_tool(existing_ids)

    # Mock OpenAI response with mixed actions
    mock_response = make_tool_call_response(
        "memory_actions",
        {
            "actions": [
                {"action": "add", "content": "User prefers dark mode"},
                {"action": "delete", "id": "mem-001"},
                {
                    "action": "update",
                    "id": "mem-002",
                    "new_content": "User works at NewCo",
                },
            ]
        },
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
            return_value=ActionsModel.model_validate_json(
                mock_response.choices[0].message.tool_calls[0].function.arguments
            ),
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
            response_model=ActionsModel,
            tools=[tool_definition],
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
    ActionsModel, tool_definition, tool_choice = build_memory_actions_tool(existing_ids)

    # Mock response with invalid ID
    mock_response = make_tool_call_response(
        "memory_actions",
        {
            "actions": [
                {"action": "delete", "id": "mem-999"},  # Invalid ID
            ]
        },
    )

    mutation_calls = []

    async def mock_delete(memory_id, request, user, db):
        mutation_calls.append(("delete", memory_id))

    with (
        patch("auto_memory.delete_memory_by_id", new=mock_delete),
        patch("open_webui.internal.db.get_db") as mock_get_db,
    ):
        mock_get_db.return_value.__enter__.return_value = MagicMock()

        # Attempt to parse with strict schema
        raw_args = mock_response.choices[0].message.tool_calls[0].function.arguments
        with pytest.raises(ValidationError) as exc_info:
            ActionsModel.model_validate_json(raw_args)

        # Verify validation error mentions invalid ID
        assert "mem-999" in str(exc_info.value) or "Input should be" in str(
            exc_info.value
        )

    # No mutations should have occurred
    assert len(mutation_calls) == 0


@pytest.mark.asyncio
async def test_no_tool_calls_hard_fails_no_mutations(mock_user, mock_emitter):
    """
    Test 3: Provider response without tool_calls hard-fails and no mutations.
    """
    filter_instance = Filter()
    filter_instance.valves.debug_mode = False
    filter_instance.user_valves = filter_instance.UserValves()

    existing_ids = ["mem-001"]
    ActionsModel, tool_definition, tool_choice = build_memory_actions_tool(existing_ids)

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

        # Attempt to call query_openai_sdk - should raise ValueError
        with pytest.raises(ValueError) as exc_info:
            await filter_instance.query_openai_sdk(
                system_prompt="test",
                user_message="test",
                response_model=ActionsModel,
                tools=[tool_definition],
                tool_choice=tool_choice,
            )

        # Verify error message
        assert "expected exactly one tool call but got zero" in str(exc_info.value)

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
    ActionsModel, tool_definition, tool_choice = build_memory_actions_tool(existing_ids)

    # Mock response with extra keys
    mock_response = make_tool_call_response(
        "memory_actions",
        {
            "actions": [
                {
                    "action": "add",
                    "content": "User likes coffee",
                    "extra_field": "should_fail",  # Extra key
                }
            ]
        },
    )

    mutation_calls = []

    async def mock_add(request, form_data, user):
        mutation_calls.append(("add", form_data.content))

    with patch("auto_memory.add_memory", new=mock_add):
        # Attempt to parse with strict schema
        raw_args = mock_response.choices[0].message.tool_calls[0].function.arguments
        with pytest.raises(ValidationError) as exc_info:
            ActionsModel.model_validate_json(raw_args)

        # Verify validation error mentions extra field
        assert "extra_field" in str(
            exc_info.value
        ) or "Extra inputs are not permitted" in str(exc_info.value)

    # No mutations should have occurred
    assert len(mutation_calls) == 0
