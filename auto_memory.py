"""
title: Auto Memory
author: @Drunk-Dream
description: automatically identify and store valuable information from chats as Memories.
author_email: dongmh3@outlook.com
author_url: https://github.com/Drunk-Dream
repository_url: https://github.com/Drunk-Dream/open-webui-functions
version: 1.4.9
required_open_webui_version: >= 0.8.1
license: see extension documentation file `auto_memory.md` (License section) for the licensing terms.

Forked from:
  Original Author: @nokodo
  Original Repository: https://nokodo.net/github/open-webui-extensions
  Original Funding: https://ko-fi.com/nokodo

Compatibility Note:
- Version 1.4.9: Completed behavior-preserving single-file refactor, restored import-time lifecycle column bootstrap, and aligned hard-cap compatibility in boost semantics
- Version 1.4.7: Optimized emit_status messages for mobile display by shortening each status text and splitting long updates into multiple emits
- Version 1.4.6: Improved error handling - skip invalid tool calls instead of failing all operations
- Version 1.4.5: Unified field naming - changed update_memory field from 'new_content' to 'content' for consistency with add_memory
- Version 1.4.3: Refactored code structure for readability and maintainability.
- Version 1.4.2: Split memory function calling into single-memory add/update/delete tools and initialize expiry immediately on add
- Version 1.4.0: Refactored function calling implementation for improved reliability and maintainability
- Version 1.3.7: Added ENABLE_MEMORIES global toggle and per-user features.memories permission checks
- Version 1.3.6: Fixed memory expiry extension logic bug, added max_expiry_days parameter
- Version 1.3.5: Compatible with Open WebUI 0.8.1+
  Memory API signatures updated (db parameter removed from query/add/update operations)
File Structure:
    1. Module Header & Imports (L1-75)
    2. System Prompts - Business Rules (L76-230)
    3. Data Model Layer - Pydantic Models & Utilities (L231-405)
    4. Infrastructure Layer - Async Tools & Database (L406-580)
    5. Core Business Layer - Filter Class (L581-end)
"""

# ============================================================================
# 1. MODULE HEADER & IMPORTS
# ============================================================================
import asyncio
import json
import logging
import threading
import time
from datetime import datetime
from typing import (
    Any,
    Awaitable,
    Callable,
    Literal,
    Optional,
    Type,
    TypeVar,
    Union,
    cast,
    overload,
)

from fastapi import HTTPException, Request
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, create_model
from sqlalchemy import BigInteger, Boolean, Column, Float, Index, String, inspect, text
from sqlalchemy.orm import Session

from open_webui.internal.db import Base, engine, get_db_context
from open_webui.main import app as webui_app
from open_webui.models.users import UserModel, Users
from open_webui.retrieval.vector.main import SearchResult
from open_webui.routers.memories import (
    AddMemoryForm,
    MemoryUpdateModel,
    QueryMemoryForm,
    add_memory,
    delete_memory_by_id,
    query_memory,
    update_memory_by_id,
)
from open_webui.utils.access_control import has_permission

# ============================================================================
# 2. TYPE DEFINITIONS & CONSTANTS
# ============================================================================
LogLevel = Literal["debug", "info", "warning", "error"]

EmitterType = Callable[[dict[str, Any]], Awaitable[None]]

SECONDS_PER_DAY = 86400
SECONDS_PER_MINUTE = 60
SHORT_MESSAGE_WORD_THRESHOLD = 8
MAX_MEMORY_IDS_FOR_TOOLS = 50
SIMILARITY_SCORE_PRECISION = 3
MAX_LIFETIME_DAYS = 90
INITIAL_STRENGTH = 40
BASE_DECAY_PER_DAY = 1.0
ACCESS_GAIN = 12
GAIN_DAMPING = 0.15
BURST_WINDOW_MINUTES = 30
BURST_GAIN_MULTIPLIER = 0.25
FORGET_THRESHOLD = 15
DELETE_GRACE_DAYS = 7
MAINTENANCE_BATCH_SIZE = 20
MAX_WRITES_PER_EVENT = 50
CLEANUP_DELETE_AFTER_FAILURES = 3
LIFECYCLE_DEFAULTS: dict[str, Any] = {
    "hard_expire_at": None,
    "access_count": 0,
    "last_accessed_at": None,
    "last_decay_at": None,
    "strength": INITIAL_STRENGTH,
    "pinned": False,
    "cleanup_fail_count": 0,
}

LIFECYCLE_COLUMN_TYPES: dict[str, str] = {
    "hard_expire_at": "BIGINT",
    "access_count": "BIGINT",
    "last_accessed_at": "BIGINT",
    "last_decay_at": "BIGINT",
    "strength": "FLOAT",
    "pinned": "BOOLEAN",
    "cleanup_fail_count": "BIGINT",
}

LIFECYCLE_BACKFILL_STATEMENTS: dict[str, str] = {
    "hard_expire_at": f"UPDATE auto_memory_expiry SET hard_expire_at = created_at + {MAX_LIFETIME_DAYS * SECONDS_PER_DAY} WHERE hard_expire_at IS NULL",
    "access_count": "UPDATE auto_memory_expiry SET access_count = 0 WHERE access_count IS NULL",
    "last_accessed_at": "UPDATE auto_memory_expiry SET last_accessed_at = updated_at WHERE last_accessed_at IS NULL",
    "last_decay_at": "UPDATE auto_memory_expiry SET last_decay_at = updated_at WHERE last_decay_at IS NULL",
    "strength": f"UPDATE auto_memory_expiry SET strength = {INITIAL_STRENGTH} WHERE strength IS NULL",
    "pinned": "UPDATE auto_memory_expiry SET pinned = 0 WHERE pinned IS NULL",
    "cleanup_fail_count": "UPDATE auto_memory_expiry SET cleanup_fail_count = 0 WHERE cleanup_fail_count IS NULL",
}


def _calculate_hard_expire_at(created_at_timestamp: int) -> int:
    return created_at_timestamp + (MAX_LIFETIME_DAYS * SECONDS_PER_DAY)


def _calculate_decayed_strength(
    current_strength: float,
    last_decay_at: int,
    now_timestamp: int,
) -> float:
    elapsed_seconds = max(0, now_timestamp - last_decay_at)
    elapsed_days = elapsed_seconds / SECONDS_PER_DAY
    return max(0.0, current_strength - (elapsed_days * BASE_DECAY_PER_DAY))


def _calculate_reinforcement_gain(access_count: int) -> float:
    normalized_access_count = max(0, access_count)
    return ACCESS_GAIN / (1 + (normalized_access_count * GAIN_DAMPING))


def _calculate_burst_multiplier(
    last_accessed_at: int | None,
    now_timestamp: int,
) -> float:
    if last_accessed_at is None:
        return 1.0

    if now_timestamp - last_accessed_at <= BURST_WINDOW_MINUTES * SECONDS_PER_MINUTE:
        return BURST_GAIN_MULTIPLIER

    return 1.0


def _calculate_soft_expire_at(
    strength: float,
    now_timestamp: int,
    hard_expire_at: int,
) -> int:
    soft_window_days = max(1, round(max(0.0, strength) / 10))
    return min(now_timestamp + (soft_window_days * SECONDS_PER_DAY), hard_expire_at)


def _should_delete_maintenance_candidate(record: Any, now_timestamp: int) -> bool:
    hard_expire_at = int(getattr(record, "hard_expire_at", 0) or 0)
    expired_at = int(getattr(record, "expired_at", 0) or 0)
    strength = float(getattr(record, "strength", 0.0) or 0.0)
    cleanup_fail_count = int(getattr(record, "cleanup_fail_count", 0) or 0)

    if hard_expire_at and hard_expire_at <= now_timestamp:
        return True

    if expired_at > now_timestamp:
        return False

    if cleanup_fail_count >= CLEANUP_DELETE_AFTER_FAILURES:
        return True

    if strength <= FORGET_THRESHOLD:
        return True

    return expired_at + (DELETE_GRACE_DAYS * SECONDS_PER_DAY) <= now_timestamp


_lifecycle_bootstrap_ready = False

STRINGIFIED_MESSAGE_TEMPLATE = "-{index}. {role}: ```{content}```"
INLET_MEMORY_CONTEXT_PREFIX = "[AUTO_MEMORY_RELATED_MEMORIES]"
TOOL_DESCRIPTIONS = {
    "add_memory": "Add exactly one memory.",
    "update_memory": "Update exactly one existing memory by ID.",
    "delete_memory": "Delete exactly one existing memory by ID.",
}
ACTION_ORDER: tuple[Literal["delete", "update", "add"], ...] = (
    "delete",
    "update",
    "add",
)

# ============================================================================
# 3. SYSTEM PROMPTS (Business Rules)
# ============================================================================

UNIFIED_SYSTEM_PROMPT = """\
You maintain a collection of Memories: individual facts or journal entries about a user, each automatically timestamped on creation or update.

Your only job here is to decide what memory actions to take. If memory changes are needed, respond with tool calls only. If no memory changes are needed, return no tool calls.

<output_rules>
- Use only these tools: `add_memory`, `update_memory`, `delete_memory`.
- Each tool call must handle exactly one memory.
- You may call tools multiple times when multiple memory changes are needed.
- If no changes are needed, return no tool calls.
- Never output plain text. The tool call is your entire response.
- In no-change cases, leave `tool_calls` empty and do not output any content text.
- Never store credentials, passwords, API keys, tokens, or any secrets.
</output_rules>

<key_instructions>
## Instructions
1. Focus ONLY on the **latest user message** (the most recent message with role=user). Older messages provide context but should not generate new memories unless explicitly referenced in the latest message.
2. Each Memory should represent **a single fact or statement**. Never combine multiple facts into one Memory.
3. When the latest user message contradicts existing memories, **update the existing memory** rather than creating a conflicting new one.
4. If memories are exact duplicates or direct conflicts about the same topic, **consolidate them by updating or deleting** as appropriate.
5. **Link related Memories** by including brief references when relevant to maintain semantic connections.
6. Capture anything valuable for **personalizing future interactions** with the User.
7. Always **honor memory requests**, whether direct from the User ("remember this", "forget that", "update X") or implicit through the Assistant's commitment ("I'll remember that", "I'll keep that in mind"). Treat these as strong signals to store, update, or delete the referenced information.
8. Each memory must be **self-contained and understandable without external context.** Avoid ambiguous references like "it", "that", or "there" - instead, include the specific subject being referenced. For example, prefer "User's new TV broke" over "It broke".
9. Be alert to **sarcasm, jokes, and non-literal language.** If the User's statement appears to be hyperbole, sarcasm, or non-literal rather than a factual claim, do not store it as a memory.
10. When determining which memory is "most recent" for conflict resolution, **refer to the `created_at` or `update_at` timestamps** from the existing memories.
</key_instructions>

<what_to_extract>
## What you WANT to extract
- Personal preferences, opinions, and feelings
- Long-term personal information (likely true for months/years)
- Future-oriented statements ("from now on", "going forward")
- Direct memory requests ("remember that", "note this", "forget that")
- Hobbies, interests, skills
- Important life details (job, education, relationships, location)
- Long term goals, plans, aspirations
- Recurring patterns or habits
- Strong likes/dislikes affecting future conversations
</what_to_extract>

<what_not_to_extract>
## What you do NOT want to extract
- User/assistant names (already in profile)
- User gender, age and birthdate (already in profile)
- ANY kind of short-term or ephemeral information that is unlikely to be relevant in future conversations
- Information the assistant confirms is already known
- Content from translation/rewrite/summarization/similar tasks ("Please help me write my essay about x")
- Trivial observations or fleeting thoughts
- Temporary activities
- Sarcastic remarks or obvious jokes
- Non-literal statements or hyperbole
- Credentials, passwords, API keys, tokens, or any secrets
</what_not_to_extract>

<actions_to_take>
Based on your analysis, call one or more tools (`add_memory`, `update_memory`, `delete_memory`):

**ADD**: Create new memory when:
- New information not covered by existing memories
- Distinct facts even if related to existing topics
- User explicitly requests to remember something

**UPDATE**: Modify existing memory when:
- User provides updated/corrected information about the same fact
- Consolidating small, inseparable or closely related facts into one memory
- User explicitly asks to update something
- New information refines but doesn't fundamentally change existing memory

**DELETE**: Remove existing memory when:
- User explicitly requests to forget something
- User's statement directly contradicts an existing memory
- Consolidating memories (update the oldest, delete the rest)
- Memory is completely obsolete due to new information
- Duplicate memories exist (keep oldest based on `created_at` timestamp)

When updating or deleting, ONLY use the memory ID from the related memories list.
</actions_to_take>

<consolidation_rules>
**Core Principle**: Default to keeping memories separate and granular for precise retrieval. Only consolidate when it meaningfully improves memory quality and coherence.

**When to CONSOLIDATE** (merge existing memories):

- **Exact Duplicates** - Same fact, different wording
    - Action: Delete the newer duplicate, keep the oldest (based on `created_at` timestamp)
    - Example: "User prefers Python for scripting" + "User likes Python for scripting tasks" → Keep oldest, delete duplicate

- **Direct Conflicts** - Contradictory facts about the same subject
    - Action: Update the older memory to reflect the latest information, or delete if completely obsolete
    - Example: "User lives in San Francisco" conflicts with "User moved to Mountain View" → Update or delete old info

- **Inseparable Facts** - Multiple facts about the same entity that would be incomplete or confusing if retrieved separately
    - Action: Merge into the oldest memory as a single self-contained statement, then delete the redundant memories
    - Test: Would retrieving one fact without the other create confusion or require additional context?
    - Example: "User's cat is named Luna" + "User's cat is a Siamese" → "User has a Siamese cat named Luna"
    - Counter-example: "User works at Google" + "User started at Google in 2023" → Keep separate (start date is distinct from employment)

- **Small, better retrieved together** - Closely related facts that enhance understanding when combined
    - Action: Merge into the oldest memory, delete the others
    - Test: Would I prefer to retrieve these facts together every time, rather than separately?
    - Example: "User loves Italian food" + "User loves Indian food" → "User loves Italian and Indian food"

**When to keep SEPARATE** (or split if wrongly combined):

Facts should remain separate when they represent distinct, independently-retrievable information:

- **Similar but distinct facts** - Related information representing different aspects or time periods
    - Example: "User works at Google" vs "User got promoted to team lead" (employment vs career progression)

- **Past events as journal entries** - Historical facts that provide temporal context
    - Example: "User bought a Samsung TV" and "User's Samsung TV broke" (separate events in time)

- **Related but separable facts** - Facts about the same topic that are meaningful independently
    - Example: "User loves dogs" vs "User has a golden retriever named Max" (general preference vs specific pet)

- **Too long or complex** - Merging would create an overly long memory that contains too many distinct facts

If an existing memory wrongly combines separable facts: UPDATE the existing memory to contain one fact (preserves timestamp), then ADD new memories for the other facts. Deleting the original would lose the timestamp.

**Guiding Question**: If vector search retrieves only one of these memories, would the user experience be degraded? If yes, consider merging. If no, keep separate.
</consolidation_rules>

<examples>
**Example 1 - Add new facts, no conflicts**
Input:
LATEST_USER_MESSAGE:
I work as a senior data scientist at Tesla and my favorite language is Rust

RECENT_CONVERSATION_SNIPPET:
user: I work as a senior data scientist at Tesla and my favorite language is Rust
assistant: That's impressive! Rust is a great choice.

RELATED_MEMORIES_JSON:
[{"mem_id": "1", "created_at": "2024-01-05T10:00:00", "update_at": "2024-01-05T10:00:00", "content": "User enjoys electric vehicles"}]

Tool calls:
1) add_memory({"content": "User works as a senior data scientist at Tesla"})
2) add_memory({"content": "User's favorite programming language is Rust"})

**Example 2 - No change needed (sarcasm/joke)**
Input:
LATEST_USER_MESSAGE:
I'm basically a human calculator!

RECENT_CONVERSATION_SNIPPET:
assistant: I can perform complex calculations in seconds.
user: I'm basically a human calculator!
assistant: 😂 Sure you can!

RELATED_MEMORIES_JSON:
[]

Tool call: (none)
</examples>\
"""


# ============================================================================
# 4. DATA MODEL LAYER
# ============================================================================
# --- Utility Functions ---
async def emit_status(
    description: str,
    emitter: EmitterType,
    status: Literal["in_progress", "complete", "error"] = "complete",
    extra_data: dict[str, Any] | None = None,
) -> None:
    if not emitter:
        raise ValueError("Emitter is required to emit status updates")

    await emitter(
        {
            "type": "status",
            "data": {
                "description": description,
                "status": status,
                "done": status in ("complete", "error"),
                "error": status == "error",
                **(extra_data or {}),
            },
        }
    )


# --- Pydantic Models ---
class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemoryAddAction(StrictBaseModel):
    action: Literal["add"] = Field(..., description="Action type (add)")
    content: str = Field(..., description="Content of the memory to add")


class MemoryUpdateAction(StrictBaseModel):
    action: Literal["update"] = Field(..., description="Action type (update)")
    id: str = Field(..., description="ID of the memory to update")
    content: str = Field(..., description="New content for the memory")


class MemoryDeleteAction(StrictBaseModel):
    action: Literal["delete"] = Field(..., description="Action type (delete)")
    id: str = Field(..., description="ID of the memory to delete")


class MemoryAddToolRequest(StrictBaseModel):
    content: str = Field(..., description="Content of the memory to add")


class MemoryUpdateToolRequest(StrictBaseModel):
    id: str = Field(..., description="ID of the memory to update")
    content: str = Field(..., description="New content for the memory")


class MemoryDeleteToolRequest(StrictBaseModel):
    id: str = Field(..., description="ID of the memory to delete")


class MemoryActionRequestStub(StrictBaseModel):
    """This is a stub model to correctly type parameters. Not used directly."""

    actions: list[Union[MemoryAddAction, MemoryUpdateAction, MemoryDeleteAction]] = (
        Field(
            default_factory=list,
            description="List of actions to perform on memories",
            max_length=20,
        )
    )


class Memory(BaseModel):
    """Single memory entry with metadata."""

    mem_id: str = Field(..., description="ID of the memory")
    created_at: datetime = Field(..., description="Creation timestamp")
    update_at: datetime = Field(..., description="Last update timestamp")
    content: str = Field(..., description="Content of the memory")
    similarity_score: Optional[float] = Field(
        None,
        description="Similarity score (0 to 1 - higher is **more similar** to user query) if available",
    )


# --- Model Utilities ---
MemoryActionType = Union[MemoryAddAction, MemoryUpdateAction, MemoryDeleteAction]


def build_memory_action_tools(
    existing_ids: list[str],
) -> tuple[dict[str, Type[BaseModel]], list[dict[str, Any]], Literal["auto"]]:
    """Build single-memory tool schemas for add/update/delete actions."""

    tool_models: dict[str, Type[BaseModel]] = {
        "add_memory": MemoryAddToolRequest,
    }

    if existing_ids:
        id_literal_type = Literal[tuple(existing_ids)]  # type: ignore[misc,valid-type]
        dynamic_update_model = create_model(
            "DynamicMemoryUpdateToolRequest",
            id=(id_literal_type, Field(..., description="ID of the memory to update")),  # type: ignore[valid-type]
            content=(str, Field(..., description="New content for the memory")),
            __base__=StrictBaseModel,
        )
        dynamic_delete_model = create_model(
            "DynamicMemoryDeleteToolRequest",
            id=(id_literal_type, Field(..., description="ID of the memory to delete")),  # type: ignore[valid-type]
            __base__=StrictBaseModel,
        )
        tool_models["update_memory"] = cast(Type[BaseModel], dynamic_update_model)
        tool_models["delete_memory"] = cast(Type[BaseModel], dynamic_delete_model)

    tool_definitions = [
        {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": TOOL_DESCRIPTIONS[tool_name],
                "parameters": model.model_json_schema(),
            },
        }
        for tool_name, model in tool_models.items()
    ]

    return tool_models, tool_definitions, "auto"


def _resolve_chat_completion_settings(model_name: str) -> tuple[float, dict[str, Any]]:
    if "gpt-5" in model_name:
        return 1.0, {"reasoning_effort": "medium"}
    if "gemini-3" in model_name:
        return 1.0, {}
    return 0.3, {}


def _build_chat_completion_request_args(
    model_name: str,
    system_prompt: str,
    user_message: str,
    temperature: float,
    extra_args: dict[str, Any],
    tools: Optional[list[dict[str, Any]]] = None,
    tool_choice: Optional[Union[dict[str, Any], str]] = None,
) -> dict[str, Any]:
    request_args: dict[str, Any] = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
        **extra_args,
    }
    if tools is not None:
        request_args["tools"] = cast(Any, tools)
    if tool_choice is not None:
        request_args["tool_choice"] = cast(Any, tool_choice)
    return request_args


def _describe_tool_calling_response_model(
    response_model: type[BaseModel] | dict[str, Type[BaseModel]],
) -> str:
    if isinstance(response_model, dict):
        return f"tool-map[{', '.join(sorted(response_model.keys()))}]"
    return cast(Any, response_model).__name__


def _require_tool_call_arguments(raw_args: Optional[str]) -> str:
    if not raw_args or not raw_args.strip():
        raise ValueError("tool call returned empty arguments")
    return raw_args


def _build_memory_action_from_parsed_args(
    tool_name: str, parsed_args: BaseModel
) -> MemoryActionType:
    if tool_name == "add_memory":
        parsed_add = cast(MemoryAddToolRequest, parsed_args)
        return MemoryAddAction(action="add", content=parsed_add.content)
    if tool_name == "update_memory":
        parsed_update = cast(MemoryUpdateToolRequest, parsed_args)
        return MemoryUpdateAction(
            action="update",
            id=parsed_update.id,
            content=parsed_update.content,
        )
    if tool_name == "delete_memory":
        parsed_delete = cast(MemoryDeleteToolRequest, parsed_args)
        return MemoryDeleteAction(action="delete", id=parsed_delete.id)
    raise ValueError(f"unsupported tool name: {tool_name!r}")


def _parse_memory_action_tool_call(
    tool_name: str,
    raw_args: str,
    tool_models: dict[str, Type[BaseModel]],
) -> MemoryActionType:
    parsed_args = tool_models[tool_name].model_validate_json(raw_args)
    return _build_memory_action_from_parsed_args(tool_name, parsed_args)


def _get_timestamp_field(source: Any, *field_names: str) -> Any | None:
    for field_name in field_names:
        if isinstance(source, dict):
            value = source.get(field_name)
        else:
            value = getattr(source, field_name, None)

        if value is not None:
            return value

    return None


def searchresults_to_memories(results: SearchResult) -> list[Memory]:
    memories = []

    if not results.ids or not results.documents or not results.metadatas:
        raise ValueError("SearchResult must contain ids, documents, and metadatas")

    for batch_idx, (ids_batch, docs_batch, metas_batch) in enumerate(
        zip(results.ids, results.documents, results.metadatas)
    ):
        distances_batch = results.distances[batch_idx] if results.distances else None

        for doc_idx, (mem_id, content, meta) in enumerate(
            zip(ids_batch, docs_batch, metas_batch)
        ):
            if not meta:
                raise ValueError(f"Missing metadata for memory id={mem_id}")
            created_at_value = _get_timestamp_field(meta, "created_at")
            if created_at_value is None:
                raise ValueError(
                    f"Missing 'created_at' in metadata for memory id={mem_id}"
                )

            updated_at_value = _get_timestamp_field(
                meta,
                "updated_at",
                "update_at",
            )

            created_at = datetime.fromtimestamp(int(created_at_value))
            updated_at = datetime.fromtimestamp(
                int(
                    updated_at_value
                    if updated_at_value is not None
                    else created_at_value
                )
            )

            similarity_score = None
            if distances_batch is not None and doc_idx < len(distances_batch):
                similarity_score = round(
                    distances_batch[doc_idx], SIMILARITY_SCORE_PRECISION
                )

            mem = Memory(
                mem_id=mem_id,
                created_at=created_at,
                update_at=updated_at,
                content=content,
                similarity_score=similarity_score,
            )
            memories.append(mem)

    return memories


# ============================================================================
# 5. INFRASTRUCTURE LAYER
# ============================================================================
# --- Async Utilities ---
T = TypeVar("T")


def _run_coro_in_new_loop(coro: Awaitable[T]) -> T:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _start_daemon_thread(target: Callable[[], None]) -> threading.Thread:
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread


def _run_async_in_thread(coro: Awaitable[T]) -> T:
    result_holder: dict[str, Any] = {}
    error_holder: dict[str, Exception] = {}

    def _runner() -> None:
        try:
            result_holder["result"] = _run_coro_in_new_loop(coro)
        except Exception as e:
            error_holder["error"] = e

    thread = _start_daemon_thread(_runner)
    thread.join()

    if "error" in error_holder:
        raise error_holder["error"]

    return result_holder["result"]


def _run_detached(coro: Awaitable[Any]) -> None:
    def _runner() -> None:
        try:
            _run_coro_in_new_loop(coro)
        except Exception as e:
            logging.getLogger(__name__).exception("Detached task failed: %s", e)

    _start_daemon_thread(_runner)


def _build_webui_request() -> Request:
    return Request(scope={"type": "http", "app": webui_app})


# --- Database Models ---


class MemoryExpiry(Base):
    """SQLAlchemy model for tracking memory expiration times."""

    __tablename__ = "auto_memory_expiry"

    mem_id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    expired_at = Column(BigInteger, nullable=False, index=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    hard_expire_at = Column(BigInteger, nullable=False, index=True)
    access_count = Column(BigInteger, nullable=False)
    last_accessed_at = Column(BigInteger, nullable=False)
    last_decay_at = Column(BigInteger, nullable=False)
    strength = Column(Float, nullable=False)
    pinned = Column(Boolean, nullable=False)
    cleanup_fail_count = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index("ix_auto_memory_expiry_user_expired", "user_id", "expired_at"),
        {"extend_existing": True},
    )


class MemoryExpiryTable:
    """CRUD operations for memory expiry tracking."""

    def insert(
        self,
        mem_id: str,
        user_id: str,
        expired_at: int,
        created_at: int | None = None,
        hard_expire_at: int | None = None,
        access_count: int = 0,
        last_accessed_at: int | None = None,
        last_decay_at: int | None = None,
        strength: float = INITIAL_STRENGTH,
        cleanup_fail_count: int = 0,
        db: Optional[Session] = None,
    ) -> Optional[MemoryExpiry]:
        """Insert a new memory expiry record."""
        with get_db_context(db) as db:
            now = int(time.time())
            created_at_value = created_at or now
            expiry = MemoryExpiry(
                mem_id=mem_id,
                user_id=user_id,
                expired_at=expired_at,
                created_at=created_at_value,
                updated_at=now,
                hard_expire_at=hard_expire_at
                or _calculate_hard_expire_at(created_at_value),
                access_count=access_count,
                last_accessed_at=last_accessed_at or now,
                last_decay_at=last_decay_at or now,
                strength=strength,
                pinned=False,
                cleanup_fail_count=cleanup_fail_count,
            )
            db.add(expiry)
            db.commit()
            db.refresh(expiry)
            return expiry

    def get_by_mem_id(
        self,
        mem_id: str,
        db: Optional[Session] = None,
    ) -> Optional[MemoryExpiry]:
        """Get expiry record by memory ID."""
        with get_db_context(db) as db:
            return db.get(MemoryExpiry, mem_id)

    def update_expired_at(
        self,
        mem_id: str,
        expired_at: int,
        created_at: int | None = None,
        hard_expire_at: int | None = None,
        strength: float | None = None,
        access_count: int | None = None,
        last_accessed_at: int | None = None,
        last_decay_at: int | None = None,
        cleanup_fail_count: int | None = None,
        db: Optional[Session] = None,
    ) -> Optional[MemoryExpiry]:
        with get_db_context(db) as db:
            expiry = db.get(MemoryExpiry, mem_id)
            if not expiry:
                return None
            expiry.expired_at = expired_at  # pyright: ignore
            if created_at is not None:
                expiry.created_at = created_at  # pyright: ignore
            if hard_expire_at is not None:
                expiry.hard_expire_at = hard_expire_at  # pyright: ignore
            if strength is not None:
                expiry.strength = strength  # pyright: ignore
            if access_count is not None:
                expiry.access_count = access_count  # pyright: ignore
            if last_accessed_at is not None:
                expiry.last_accessed_at = last_accessed_at  # pyright: ignore
            if last_decay_at is not None:
                expiry.last_decay_at = last_decay_at  # pyright: ignore
            if cleanup_fail_count is not None:
                expiry.cleanup_fail_count = cleanup_fail_count  # pyright: ignore
            expiry.updated_at = int(time.time())  # pyright: ignore[reportAttributeAccessIssue]
            db.commit()
            db.refresh(expiry)
            return expiry

    def delete_by_mem_id(
        self,
        mem_id: str,
        db: Optional[Session] = None,
    ) -> bool:
        """Delete expiry record by memory ID."""
        with get_db_context(db) as db:
            expiry = db.get(MemoryExpiry, mem_id)
            if not expiry:
                return False
            db.delete(expiry)
            db.commit()
            return True

    def get_expired(
        self,
        user_id: str,
        now_timestamp: int,
        limit: int = MAINTENANCE_BATCH_SIZE,
        db: Optional[Session] = None,
    ) -> list[MemoryExpiry]:
        with get_db_context(db) as db:
            return (
                db.query(MemoryExpiry)
                .filter(
                    MemoryExpiry.user_id == user_id,
                    MemoryExpiry.pinned.is_(False),
                    (
                        (MemoryExpiry.hard_expire_at <= now_timestamp)
                        | (MemoryExpiry.expired_at <= now_timestamp)
                        | (MemoryExpiry.strength <= FORGET_THRESHOLD)
                        | (
                            MemoryExpiry.cleanup_fail_count
                            >= CLEANUP_DELETE_AFTER_FAILURES
                        )
                    ),
                )
                .order_by(
                    MemoryExpiry.hard_expire_at.asc(),
                    MemoryExpiry.expired_at.asc(),
                    MemoryExpiry.last_accessed_at.asc(),
                    MemoryExpiry.mem_id.asc(),
                )
                .limit(limit)
                .all()
            )


MemoryExpiries = MemoryExpiryTable()


# --- Database Initialization ---
def _ensure_table_exists() -> bool:
    """Ensure MemoryExpiry table exists in database."""
    try:
        Base.metadata.create_all(
            engine,
            tables=[MemoryExpiry.__table__],  # pyright: ignore[reportArgumentType]
            checkfirst=True,
        )
        return True
    except Exception:
        logging.getLogger(__name__).exception(
            "failed to create auto_memory_expiry table"
        )
        return False


def _ensure_lifecycle_columns() -> bool:
    logger = logging.getLogger(__name__)
    try:
        with engine.begin() as connection:
            inspector = inspect(connection)
            existing_columns = {
                column["name"]
                for column in inspector.get_columns(MemoryExpiry.__tablename__)
            }

            for column_name, column_type in LIFECYCLE_COLUMN_TYPES.items():
                if column_name not in existing_columns:
                    connection.execute(
                        text(
                            f"ALTER TABLE {MemoryExpiry.__tablename__} "
                            f"ADD COLUMN {column_name} {column_type}"
                        )
                    )
                    logger.info(
                        "added missing lifecycle column %s to auto_memory_expiry",
                        column_name,
                    )

                repair_result = connection.execute(
                    text(LIFECYCLE_BACKFILL_STATEMENTS[column_name])
                )
                logger.debug(
                    "backfilled lifecycle column %s on auto_memory_expiry (rowcount=%s)",
                    column_name,
                    getattr(repair_result, "rowcount", None),
                )
        return True
    except Exception:
        logger.exception("failed to ensure lifecycle columns on auto_memory_expiry")
        return False


def _ensure_memory_expiry_lifecycle_bootstrap() -> bool:
    global _lifecycle_bootstrap_ready

    if _lifecycle_bootstrap_ready:
        return True

    table_ready = _ensure_table_exists()
    columns_ready = _ensure_lifecycle_columns()
    _lifecycle_bootstrap_ready = table_ready and columns_ready
    return _lifecycle_bootstrap_ready


def _get_lifecycle_value(record: Any, field_name: str) -> Any:
    value = getattr(record, field_name, None)
    if value is None:
        return LIFECYCLE_DEFAULTS[field_name]
    return value


def _get_lifecycle_int(record: Any, field_name: str) -> int | None:
    value = _get_lifecycle_value(record, field_name)
    return None if value is None else int(value)


_ensure_memory_expiry_lifecycle_bootstrap()


R = TypeVar("R", bound=BaseModel)
ValveType = TypeVar("ValveType", str, int)


# ============================================================================
# 6. CORE BUSINESS LAYER
# ============================================================================
class Filter:
    """Main plugin class for Auto Memory functionality.

    Architecture:
        - Configuration: Valves, UserValves
        - LLM Integration: query_openai_sdk
        - Memory Management: CRUD operations
        - Lifecycle Hooks: inlet, outlet
    """

    current_user: Optional[dict[str, object]] = None
    user_valves: "Filter.UserValves"  # pyright: ignore[reportUninitializedInstanceVariable]

    # ------------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------------
    class Valves(BaseModel):
        openai_api_url: str = Field(
            default="https://api.openai.com/v1",
            description="openai compatible endpoint",
        )
        model: str = Field(
            default="gpt-5-mini",
            description="model to use to determine memory. an intelligent model is highly recommended, as it will be able to better understand the context of the conversation.",
        )
        api_key: str = Field(
            default="", description="API key for OpenAI compatible endpoint"
        )
        messages_to_consider: int = Field(
            default=4,
            description="global default number of recent messages to consider for memory extraction (user override can supply a different value).",
        )
        related_memories_n: int = Field(
            default=5,
            description="number of related memories to consider when updating memories",
        )
        enable_inlet_memory_context: bool = Field(
            default=True,
            description="inject high-similarity related memories into the request context during inlet",
        )
        inlet_related_memories_n: Optional[int] = Field(
            default=None,
            ge=1,
            description="number of related memories to retrieve for inlet injection. if not set, uses related_memories_n",
        )
        minimum_memory_similarity: Optional[float] = Field(
            default=None,
            ge=0.0,
            le=1.0,
            description="minimum similarity of memories to consider for updates. higher is more similar to user query. if not set, no filtering is applied.",
        )
        inlet_minimum_memory_similarity: Optional[float] = Field(
            default=None,
            ge=0.0,
            le=1.0,
            description="minimum similarity for memories injected in inlet. if not set, falls back to minimum_memory_similarity",
        )
        allow_unsafe_user_overrides: bool = Field(
            default=False,
            description="SECURITY WARNING: allow users to override API URL/model without providing their own API key. this could allow users to steal your API key or use expensive models at your expense. only enable if you trust all users.",
        )
        debug_mode: bool = Field(
            default=False,
            description="enable debug logging",
        )

    class UserValves(BaseModel):
        enabled: bool = Field(
            default=True,
            description="whether to enable Auto Memory for this user",
        )
        show_status: bool = Field(
            default=True, description="show status of the action."
        )
        openai_api_url: Optional[str] = Field(
            default=None,
            description="user-specific openai compatible endpoint (overrides global)",
        )
        model: Optional[str] = Field(
            default=None,
            description="user-specific model to use (overrides global). an intelligent model is highly recommended, as it will be able to better understand the context of the conversation.",
        )
        api_key: Optional[str] = Field(
            default=None, description="user-specific API key (overrides global)"
        )
        messages_to_consider: Optional[int] = Field(
            default=None,
            description="override for number of recent messages to consider (falls back to global if null). includes assistant responses.",
        )

    # ------------------------------------------------------------------------
    # Initialization & Utilities
    # ------------------------------------------------------------------------
    def log(self, message: str, level: LogLevel = "info"):
        if level == "debug" and not self.valves.debug_mode:
            return
        if level not in {"debug", "info", "warning", "error"}:
            level = "info"

        logger = logging.getLogger()
        getattr(logger, level, logger.info)(message)

    # ------------------------------------------------------------------------
    # LLM Integration
    # ------------------------------------------------------------------------
    def messages_to_string(self, messages: list[dict[str, Any]]) -> str:
        stringified_messages: list[str] = []

        effective_messages_to_consider = self.get_restricted_user_valve(
            user_valve_value=self.user_valves.messages_to_consider,
            admin_fallback=self.valves.messages_to_consider,
            authorization_check=bool(
                self.user_valves.api_key and self.user_valves.api_key.strip()
            ),
            valve_name="messages_to_consider",
        )

        self.log(
            f"using last {effective_messages_to_consider} messages",
            level="debug",
        )

        for index, message in self._iter_recent_messages_for_stringifying(
            messages=messages,
            limit=effective_messages_to_consider,
        ):
            try:
                stringified_messages.append(
                    self._format_stringified_message(index=index, message=message)
                )
            except Exception as e:
                self.log(f"error stringifying message {index}: {e}", level="warning")

        return "\n".join(stringified_messages)

    @overload
    async def query_openai_sdk(
        self,
        system_prompt: str,
        user_message: str,
        response_model: Type[R],
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[Union[dict[str, Any], str]] = None,
    ) -> R: ...

    @overload
    async def query_openai_sdk(
        self,
        system_prompt: str,
        user_message: str,
        response_model: dict[str, Type[BaseModel]],
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[Union[dict[str, Any], str]] = None,
    ) -> MemoryActionRequestStub: ...

    @overload
    async def query_openai_sdk(
        self,
        system_prompt: str,
        user_message: str,
        response_model: None = None,
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[Union[dict[str, Any], str]] = None,
    ) -> str: ...

    async def query_openai_sdk(
        self,
        system_prompt: str,
        user_message: str,
        response_model: Optional[
            Union[type[BaseModel], dict[str, Type[BaseModel]]]
        ] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[Union[dict[str, Any], str]] = None,
    ) -> Union[str, BaseModel, MemoryActionRequestStub]:
        """Generic wrapper around OpenAI chat completions.

        Behavior:
        - If `response_model` is None, calls chat.completions.create and returns raw text.
        - If `response_model` is provided, calls chat.completions.create with `tools` and
          optional `tool_choice`, then validates tool call arguments using strict schemas.
          Any provider rejection or malformed response raises immediately (no fallback).
        """

        user_has_own_key = bool(
            self.user_valves.api_key and self.user_valves.api_key.strip()
        )

        api_url = self.get_restricted_user_valve(
            user_valve_value=self.user_valves.openai_api_url,
            admin_fallback=self.valves.openai_api_url,
            authorization_check=user_has_own_key,
            valve_name="openai_api_url",
        ).rstrip("/")

        model_name = self.get_restricted_user_valve(
            user_valve_value=self.user_valves.model,
            admin_fallback=self.valves.model,
            authorization_check=user_has_own_key,
            valve_name="model",
        )
        api_key = self.user_valves.api_key or self.valves.api_key

        temperature, extra_args = _resolve_chat_completion_settings(model_name)

        # Note: OpenAI SDK v1.0+ supports context manager, but we use direct instantiation
        # for simplicity since the client is short-lived within this method scope.
        # For long-lived clients, consider: with OpenAI(...) as client:
        client = OpenAI(api_key=api_key, base_url=api_url)
        request_args = _build_chat_completion_request_args(
            model_name=model_name,
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=temperature,
            extra_args=extra_args,
            tools=tools,
            tool_choice=tool_choice,
        )

        if response_model is None:
            response = client.chat.completions.create(**request_args)
            self.log(f"sdk response: {response}", level="debug")

            text_response = response.choices[0].message.content
            if text_response is None:
                raise ValueError(f"no text response from LLM. message={text_response}")

            return text_response

        model_label = _describe_tool_calling_response_model(response_model)
        self.log(
            f"calling tool-calling path with {model_label}",
            level="debug",
        )

        if not tools:
            raise ValueError(
                "response_model requires tools to be provided for tool-calling path"
            )

        response = client.chat.completions.create(**request_args)
        self.log(f"tool-call sdk response: {response}", level="debug")
        message = response.choices[0].message

        if message.tool_calls and message.content:
            self.log(
                f"response has both tool_calls and content; ignoring content. content={message.content[:200]}",
                level="warning",
            )

        tool_calls = message.tool_calls or []
        if len(tool_calls) == 0:
            self.log(
                f"no tool calls returned (finish_reason={response.choices[0].finish_reason}); treating as no-op",
                level="debug",
            )
            if isinstance(response_model, dict):
                return MemoryActionRequestStub(actions=[])
            raise ValueError(
                f"expected exactly one tool call but got zero. finish_reason={response.choices[0].finish_reason}"
            )

        if isinstance(response_model, dict):
            actions: list[MemoryActionType] = []
            for tool_call in tool_calls:
                tool_name = tool_call.function.name
                if tool_name not in response_model:
                    expected = ", ".join(sorted(response_model.keys()))
                    self.log(
                        f"skipping unexpected tool name: {tool_name!r}; expected one of [{expected}]",
                        level="warning",
                    )
                    continue

                raw_args = tool_call.function.arguments
                if not raw_args or not raw_args.strip():
                    self.log(
                        f"skipping tool call {tool_name!r} with empty arguments",
                        level="warning",
                    )
                    continue

                self.log(
                    f"tool call {tool_name} arguments: {raw_args[:500]}", level="debug"
                )

                try:
                    actions.append(
                        _parse_memory_action_tool_call(
                            tool_name=tool_name,
                            raw_args=raw_args,
                            tool_models=response_model,
                        )
                    )
                except Exception as e:
                    self.log(
                        f"skipping tool call {tool_name!r} due to parameter validation error: {e}",
                        level="warning",
                    )
                    continue

            return MemoryActionRequestStub(actions=actions)

        # Single-model mode: expect exactly one tool call
        model_cls = cast(Any, response_model)
        if len(tool_calls) > 1:
            raise ValueError(
                f"expected exactly one tool call but got {len(tool_calls)}"
            )

        tool_call = tool_calls[0]
        raw_args = _require_tool_call_arguments(tool_call.function.arguments)

        self.log(f"tool call arguments: {raw_args[:500]}", level="debug")
        plan = model_cls.model_validate_json(raw_args)
        return plan

    def __init__(self):
        self.valves = self.Valves()

    def _delete_memory_sync(self, mem_id: str, user: UserModel) -> None:
        """Synchronous helper for deleting memory in thread pool."""
        from open_webui.internal.db import get_db

        with get_db() as db:
            _run_coro_in_new_loop(
                delete_memory_by_id(
                    memory_id=mem_id,
                    request=_build_webui_request(),
                    user=user,
                    db=db,
                )
            )

    # ------------------------------------------------------------------------
    # Memory CRUD Operations (Private)
    # ------------------------------------------------------------------------
    def _extract_memory_id(self, add_result: Any) -> str | None:
        """Extract memory ID from add_memory API result.

        Args:
            add_result: Result from add_memory API call

        Returns:
            Memory ID if found, None otherwise
        """
        if isinstance(add_result, dict):
            mem_id = add_result.get("id")
            if isinstance(mem_id, str) and mem_id.strip():
                return mem_id
            nested = add_result.get("memory")
            if isinstance(nested, dict):
                nested_id = nested.get("id")
                if isinstance(nested_id, str) and nested_id.strip():
                    return nested_id
        if hasattr(add_result, "id"):
            attr_id = getattr(add_result, "id")
            if isinstance(attr_id, str) and attr_id.strip():
                return attr_id
        return None

    def _initialize_memory_expiry(self, mem_id: str, user_id: str) -> None:
        now_timestamp = int(time.time())
        expired_at = self._calculate_initial_expired_at(now_timestamp)
        hard_expire_at = _calculate_hard_expire_at(now_timestamp)
        existing = MemoryExpiries.get_by_mem_id(mem_id)
        if existing:
            MemoryExpiries.update_expired_at(
                mem_id,
                expired_at,
                created_at=now_timestamp,
                hard_expire_at=hard_expire_at,
                access_count=0,
                last_accessed_at=now_timestamp,
                last_decay_at=now_timestamp,
                strength=INITIAL_STRENGTH,
                cleanup_fail_count=0,
            )
            self.log(
                f"reset lifecycle tracking for memory {mem_id[:8]}...",
                level="debug",
            )
            return
        MemoryExpiries.insert(
            mem_id=mem_id,
            user_id=user_id,
            expired_at=expired_at,
            created_at=now_timestamp,
            hard_expire_at=hard_expire_at,
            access_count=0,
            last_accessed_at=now_timestamp,
            last_decay_at=now_timestamp,
            strength=INITIAL_STRENGTH,
            cleanup_fail_count=0,
        )
        self.log(
            f"initialized lifecycle tracking for memory {mem_id[:8]}...",
            level="debug",
        )

    async def _delete_memory_with_db(
        self, action: MemoryDeleteAction, user: UserModel
    ) -> None:
        """Delete memory using database context.

        Args:
            action: Delete action containing memory ID
            user: User performing the deletion
        """
        from open_webui.internal.db import get_db

        with get_db() as db:
            await delete_memory_by_id(
                memory_id=action.id,
                request=_build_webui_request(),
                user=user,
                db=db,
            )

    async def _add_memory_with_expiry(
        self, action: MemoryAddAction, user: UserModel
    ) -> None:
        """Add memory and initialize its expiry record.

        Args:
            action: Add action containing memory content
            user: User adding the memory
        """
        add_result = await add_memory(
            request=_build_webui_request(),
            form_data=AddMemoryForm(content=action.content),
            user=user,
        )
        mem_id = self._extract_memory_id(add_result)
        if not mem_id:
            self.log(
                "memory add returned no id; skipped expiry initialization",
                level="warning",
            )
            return
        try:
            self._initialize_memory_expiry(mem_id, user.id)
        except Exception as expiry_error:
            self.log(
                f"failed to initialize expiry for memory {mem_id}: {expiry_error}",
                level="warning",
            )

    def get_restricted_user_valve(
        self,
        user_valve_value: Optional[ValveType],
        admin_fallback: ValveType,
        authorization_check: Optional[bool] = None,
        valve_name: Optional[str] = None,
    ) -> ValveType:
        """
        Get user valve value with security checks.

        Args:
            user_valve_value: The user's valve value to check
            admin_fallback: Admin's fallback value
            authorization_check: The valve value to check for authorization (e.g., user's API key)
            valve_name: Name of the valve being checked (for logging)

        Returns user's value only if:
        1. authorization_check is provided and non-empty, OR
        2. User is an admin, OR
        3. Admin allows unsafe overrides

        Otherwise returns admin fallback.
        """
        if authorization_check is None:
            authorization_check = False

        if authorization_check:
            if user_valve_value is not None:
                self.log(
                    f"'{valve_name or 'unknown'}' override authorized (user has own API key)",
                    level="debug",
                )
                return user_valve_value
            return admin_fallback

        # Allow admins to override without providing their own API key
        if (
            hasattr(self, "current_user")
            and self.current_user is not None
            and self.current_user.get("role") == "admin"
        ):
            if user_valve_value is not None:
                self.log(
                    f"'{valve_name or 'unknown'}' override allowed for admin user",
                    level="info",
                )
                return user_valve_value
            return admin_fallback

        if self.valves.allow_unsafe_user_overrides:
            if user_valve_value is not None:
                self.log(
                    f"'{valve_name or 'unknown'}' override allowed (unsafe overrides enabled)",
                    level="warning",
                )
                return user_valve_value
            return admin_fallback

        if user_valve_value is not None:
            self.log(
                f"'{valve_name or 'unknown'}' override blocked - user attempted override without authorization, using admin defaults for security",
                level="warning",
            )
        return admin_fallback

    # ------------------------------------------------------------------------
    # Memory Query & Retrieval
    # ------------------------------------------------------------------------
    def build_memory_query(self, messages: list[dict[str, Any]]) -> str:
        """Build context-aware query for memory retrieval.

        Algorithm:
            1. Extract last user message (required)
            2. Include last assistant response (if exists)
            3. If user message ≤ SHORT_MESSAGE_WORD_THRESHOLD words, add previous assistant context
            4. Reverse to chronological order

        Rationale:
            Short messages lack context for embedding similarity.
            Including assistant responses provides semantic anchors.

        Examples:
            >>> messages = [
            ...     {"role": "user", "content": "What's my name?"},
            ...     {"role": "assistant", "content": "Your name is Alice."}
            ... ]
            >>> query = self.build_memory_query(messages)
            >>> assert "Alice" in query  # Context preserved

        Args:
            messages: Conversation history (newest last)

        Returns:
            Query string for vector similarity search

        Raises:
            ValueError: If no user message found in messages
        """
        query_parts: list[str] = []

        last_user_idx, last_user_msg = self._find_last_message_by_role(
            messages=messages,
            role="user",
        )

        if last_user_msg is None or last_user_idx is None:
            raise ValueError("no user message found in messages")

        user_word_count = len(last_user_msg.split())
        include_extra_context = user_word_count <= SHORT_MESSAGE_WORD_THRESHOLD

        self._append_user_query_context(
            messages=messages,
            last_user_idx=last_user_idx,
            last_user_msg=last_user_msg,
            include_extra_context=include_extra_context,
            query_parts=query_parts,
        )

        # Reverse to get chronological order and join
        query_parts.reverse()
        query = "\n".join(query_parts)

        self.log(
            f"built memory query with {len(query_parts)} messages (user message: {user_word_count} words)",
            level="debug",
        )
        self.log(f"memory query: {query}", level="debug")

        return query

    async def get_related_memories(
        self,
        messages: list[dict[str, Any]],
        user: UserModel,
        top_k: Optional[int] = None,
        minimum_similarity: Optional[float] = None,
    ) -> list[Memory]:
        """
        Query and retrieve related memories based on conversation context.

        Args:
            messages: Conversation messages to build query from
            user: User model for ownership verification

        Returns:
            List of Memory objects filtered by minimum similarity threshold
        """
        memory_query = self.build_memory_query(messages)
        effective_top_k = top_k or self.valves.related_memories_n
        effective_minimum_similarity = (
            self.valves.minimum_memory_similarity
            if minimum_similarity is None
            else minimum_similarity
        )

        # Query related memories
        try:
            results = await query_memory(
                request=_build_webui_request(),
                form_data=QueryMemoryForm(content=memory_query, k=effective_top_k),
                user=user,
            )
        except HTTPException as e:
            if e.status_code == 404:
                self.log("no related memories found", level="info")
                results = None
            else:
                self.log(
                    f"failed to query memories due to HTTP error {e.status_code}: {e.detail}",
                    level="error",
                )
                raise RuntimeError("failed to query memories") from e
        except Exception as e:
            self.log(f"failed to query memories: {e}", level="error")
            raise RuntimeError("failed to query memories") from e

        related_memories = searchresults_to_memories(results) if results else []
        self.log(
            f"found {len(related_memories)} related memories before filtering",
            level="info",
        )

        # Filter by minimum similarity if configured
        if effective_minimum_similarity is not None:
            filtered_memories = [
                mem
                for mem in related_memories
                if mem.similarity_score is not None
                and mem.similarity_score >= effective_minimum_similarity
            ]
            filtered_count = len(related_memories) - len(filtered_memories)
            if filtered_count > 0:
                self.log(
                    f"filtered out {filtered_count} memories below similarity threshold {effective_minimum_similarity}",
                    level="info",
                )
            related_memories = filtered_memories

        self.log(f"using {len(related_memories)} related memories", level="info")
        self.log(f"related memories: {related_memories}", level="debug")

        return related_memories

    # ------------------------------------------------------------------------
    # Helper Methods
    # ------------------------------------------------------------------------
    def build_inlet_memory_context(self, memories: list[Memory]) -> str:
        joined_memories = "\n".join(
            self._format_inlet_memory_line(idx=idx, memory=memory)
            for idx, memory in enumerate(memories, start=1)
        )
        return (
            f"{INLET_MEMORY_CONTEXT_PREFIX}\n"
            "Use these user memories as high-priority personalization context when relevant. "
            "Do not mention this memory block unless the user asks.\n"
            f"{joined_memories}"
        )

    def inject_memory_context_into_messages(
        self,
        messages: list[dict[str, Any]],
        memory_context: str,
    ) -> list[dict[str, Any]]:
        cleaned_messages = [
            message
            for message in messages
            if not self._is_inlet_memory_context_message(message)
        ]

        insert_at = self._first_non_system_message_index(cleaned_messages)

        cleaned_messages.insert(
            insert_at,
            {"role": "system", "content": memory_context},
        )
        return cleaned_messages

    def _iter_recent_messages_for_stringifying(
        self,
        messages: list[dict[str, Any]],
        limit: int,
    ) -> list[tuple[int, dict[str, Any]]]:
        recent_messages: list[tuple[int, dict[str, Any]]] = []
        for index in range(1, limit + 1):
            if index > len(messages):
                break
            recent_messages.append((index, messages[-index]))
        return recent_messages

    def _format_stringified_message(
        self,
        index: int,
        message: dict[str, Any],
    ) -> str:
        return STRINGIFIED_MESSAGE_TEMPLATE.format(
            index=index,
            role=message.get("role", "user"),
            content=message.get("content", ""),
        )

    def _find_last_message_by_role(
        self,
        messages: list[dict[str, Any]],
        role: str,
    ) -> tuple[int | None, str | None]:
        for idx in range(len(messages) - 1, -1, -1):
            message = messages[idx]
            if message.get("role") == role:
                return idx, message.get("content", "")
        return None, None

    def _append_user_query_context(
        self,
        messages: list[dict[str, Any]],
        last_user_idx: int,
        last_user_msg: str,
        include_extra_context: bool,
        query_parts: list[str],
    ) -> None:
        if last_user_idx + 1 < len(messages):
            last_assistant_msg = messages[last_user_idx + 1].get("content", "")
            if last_assistant_msg:
                query_parts.append(f"Assistant: {last_assistant_msg}")

        query_parts.append(f"User: {last_user_msg}")

        if include_extra_context and last_user_idx > 0:
            previous_message = messages[last_user_idx - 1]
            previous_assistant_msg = previous_message.get("content", "")
            if previous_message.get("role") == "assistant" and previous_assistant_msg:
                query_parts.append(f"Assistant: {previous_assistant_msg}")

    def _format_inlet_memory_line(self, idx: int, memory: Memory) -> str:
        score_text = (
            f" (similarity={memory.similarity_score:.3f})"
            if memory.similarity_score is not None
            else ""
        )
        return f"{idx}. {memory.content}{score_text}"

    def _is_inlet_memory_context_message(self, message: dict[str, Any]) -> bool:
        return (
            message.get("role") == "system"
            and isinstance(message.get("content"), str)
            and message["content"].startswith(INLET_MEMORY_CONTEXT_PREFIX)
        )

    def _first_non_system_message_index(
        self,
        messages: list[dict[str, Any]],
    ) -> int:
        insert_at = 0
        while insert_at < len(messages) and messages[insert_at].get("role") == "system":
            insert_at += 1
        return insert_at

    def _calculate_initial_expired_at(self, now_timestamp: int) -> int:
        return _calculate_soft_expire_at(
            strength=INITIAL_STRENGTH,
            now_timestamp=now_timestamp,
            hard_expire_at=_calculate_hard_expire_at(now_timestamp),
        )

    def _calculate_boosted_expired_at(
        self,
        existing_record: Any,
        now_timestamp: int,
    ) -> dict[str, float | int]:
        created_at = _get_lifecycle_int(existing_record, "created_at") or now_timestamp
        hard_expire_at = _get_lifecycle_int(existing_record, "hard_expire_at")
        access_count = _get_lifecycle_int(existing_record, "access_count") or 0
        last_decay_at = (
            _get_lifecycle_int(existing_record, "last_decay_at") or now_timestamp
        )
        last_accessed_at = _get_lifecycle_int(existing_record, "last_accessed_at")
        current_strength = float(_get_lifecycle_value(existing_record, "strength"))
        decayed_strength = _calculate_decayed_strength(
            current_strength=current_strength,
            last_decay_at=last_decay_at,
            now_timestamp=now_timestamp,
        )
        gain = _calculate_reinforcement_gain(access_count)
        burst_multiplier = _calculate_burst_multiplier(last_accessed_at, now_timestamp)
        new_strength = min(100.0, decayed_strength + (gain * burst_multiplier))
        resolved_hard_expire_at = hard_expire_at or _calculate_hard_expire_at(
            created_at
        )
        return {
            "hard_expire_at": resolved_hard_expire_at,
            "expired_at": _calculate_soft_expire_at(
                strength=new_strength,
                now_timestamp=now_timestamp,
                hard_expire_at=resolved_hard_expire_at,
            ),
            "strength": new_strength,
            "access_count": access_count + 1,
            "last_accessed_at": now_timestamp,
            "last_decay_at": now_timestamp,
            "cleanup_fail_count": 0,
        }

    def _build_memory_maintenance_candidates(
        self,
        user_id: str,
        now_timestamp: int,
        limit: int = MAINTENANCE_BATCH_SIZE,
    ) -> list[MemoryExpiry]:
        return MemoryExpiries.get_expired(
            user_id=user_id,
            now_timestamp=now_timestamp,
            limit=limit,
        )

    def _advance_cleanup_tracking(
        self,
        mem_id: str,
        expiry_table: MemoryExpiryTable,
    ) -> bool:
        existing = expiry_table.get_by_mem_id(mem_id)
        if existing is None:
            return False

        next_fail_count = int(getattr(existing, "cleanup_fail_count", 0) or 0) + 1
        if next_fail_count >= CLEANUP_DELETE_AFTER_FAILURES:
            expiry_table.delete_by_mem_id(mem_id)
            return True

        expiry_table.update_expired_at(
            mem_id,
            int(getattr(existing, "expired_at", 0) or 0),
            cleanup_fail_count=next_fail_count,
        )
        return False

    async def _cleanup_expired_memory_record(
        self,
        mem_id: str,
        user: UserModel,
        expiry_table: MemoryExpiryTable,
    ) -> bool:
        try:
            await asyncio.to_thread(
                self._delete_memory_sync,
                mem_id=mem_id,
                user=user,
            )
            self.log(
                f"deleted memory from vector DB: {mem_id[:8]}...",
                level="debug",
            )
        except Exception as e:
            self.log(
                f"failed to delete memory from vector DB {mem_id}: {e}. "
                f"Memory may have been manually deleted. Continuing to clean up expiry record.",
                level="warning",
            )
            removed = self._advance_cleanup_tracking(mem_id, expiry_table)
            if removed:
                self.log(
                    f"removed expiry record after cleanup failures: {mem_id[:8]}...",
                    level="warning",
                )
                return True
            self.log(
                f"retained expiry record after cleanup failure: {mem_id[:8]}...",
                level="warning",
            )
            return False

        try:
            expiry_table.delete_by_mem_id(mem_id)
            self.log(f"deleted expiry record: {mem_id[:8]}...", level="debug")
            return True
        except Exception as e:
            self.log(
                f"failed to delete expiry record {mem_id}: {e}",
                level="error",
            )
            return False

    # ------------------------------------------------------------------------
    # Memory Lifecycle Management
    # ------------------------------------------------------------------------
    async def cleanup_expired_memories(
        self,
        user: UserModel,
    ) -> int:
        """
        Process one small maintenance batch of cleanup-needed lifecycle rows.

        Candidate selection stays batched via `MemoryExpiryTable.get_expired(...)`
        and `MAINTENANCE_BATCH_SIZE`. Each candidate is then evaluated against the
        lifecycle rules: immediate removal for hard expiry, terminal retry count,
        forgotten strength, or soft-expired rows that have exceeded the grace window.
        This maintenance pass runs before memory planning so overdue rows do not leak
        into the subsequent planning/action stage.

        Args:
            user: User model for ownership verification

        Returns:
            Number of memories cleaned up
        """
        _ensure_memory_expiry_lifecycle_bootstrap()
        now_timestamp = int(time.time())
        expiry_table = MemoryExpiries

        expired_records = self._build_memory_maintenance_candidates(
            user_id=user.id,
            now_timestamp=now_timestamp,
            limit=MAINTENANCE_BATCH_SIZE,
        )

        if not expired_records:
            self.log("no memory maintenance candidates found", level="debug")
            return 0

        self.log(
            f"found {len(expired_records)} memory maintenance candidates", level="info"
        )

        deleted_count = 0
        for record in expired_records:
            if not _should_delete_maintenance_candidate(record, now_timestamp):
                continue
            if await self._cleanup_expired_memory_record(
                mem_id=str(record.mem_id),
                user=user,
                expiry_table=expiry_table,
            ):
                deleted_count += 1

        self.log(
            f"cleanup complete: deleted {deleted_count} memories",
            level="info",
        )

        return deleted_count

    async def boost_memories(
        self,
        related_memories: list[Memory],
        user: UserModel,
    ) -> dict[str, int]:
        """
        Apply lifecycle updates to the current related-memory hits.

        Each hit goes through the same three-layer lifecycle model:
        - keep `hard_expire_at` frozen from the original creation time,
        - decay strength from `last_decay_at` to now,
        - reinforce the decayed strength for the current access,
        - derive a fresh soft `expired_at` window from the new strength.

        Missing lifecycle rows are reconstructed from the memory's original
        `created_at`, with the same immutable hard-expiry boundary.

        Args:
            related_memories: List of Memory objects that were retrieved
            user: User model

        Returns:
            Statistics dict: {"total": N, "boosted": M, "created": K}
        """
        if not related_memories:
            return {"total": 0, "boosted": 0, "created": 0}

        _ensure_memory_expiry_lifecycle_bootstrap()
        now_timestamp = int(time.time())
        expiry_table = MemoryExpiries

        limited_memories = related_memories[:MAX_WRITES_PER_EVENT]
        stats = {"total": len(limited_memories), "boosted": 0, "created": 0}

        self.log(f"boosting {len(limited_memories)} retrieved memories", level="debug")

        for memory in limited_memories:
            try:
                existing = expiry_table.get_by_mem_id(memory.mem_id)

                if existing:
                    previous_expired_at = int(existing.expired_at)  # pyright: ignore[reportArgumentType]
                    new_lifecycle = self._calculate_boosted_expired_at(
                        existing_record=existing,
                        now_timestamp=now_timestamp,
                    )

                    expiry_table.update_expired_at(
                        memory.mem_id,
                        int(new_lifecycle["expired_at"]),
                        strength=float(new_lifecycle["strength"]),
                        access_count=int(new_lifecycle["access_count"]),
                        last_accessed_at=int(new_lifecycle["last_accessed_at"]),
                        last_decay_at=int(new_lifecycle["last_decay_at"]),
                        cleanup_fail_count=int(new_lifecycle["cleanup_fail_count"]),
                    )
                    stats["boosted"] += 1

                    days_extended = (
                        int(new_lifecycle["expired_at"]) - previous_expired_at
                    ) / SECONDS_PER_DAY
                    self.log(
                        f"boosted memory {memory.mem_id[:8]}... expiry shifted by {days_extended:.1f} days while keeping hard expiry fixed",
                        level="debug",
                    )
                else:
                    created_at_timestamp = int(memory.created_at.timestamp())
                    hard_expire_at = _calculate_hard_expire_at(created_at_timestamp)
                    new_expired_at = _calculate_soft_expire_at(
                        strength=INITIAL_STRENGTH,
                        now_timestamp=now_timestamp,
                        hard_expire_at=hard_expire_at,
                    )
                    expiry_table.insert(
                        mem_id=memory.mem_id,
                        user_id=user.id,
                        expired_at=new_expired_at,
                        created_at=created_at_timestamp,
                        hard_expire_at=hard_expire_at,
                        access_count=1,
                        last_accessed_at=now_timestamp,
                        last_decay_at=now_timestamp,
                        strength=INITIAL_STRENGTH,
                        cleanup_fail_count=0,
                    )
                    stats["created"] += 1
                    self.log(
                        f"created lifecycle for memory {memory.mem_id[:8]}... with frozen hard expiry",
                        level="debug",
                    )

            except Exception as e:
                self.log(f"failed to boost memory {memory.mem_id}: {e}", level="error")

        self.log(
            f"boost complete: boosted {stats['boosted']}, created {stats['created']}",
            level="info",
        )

        return stats

    # ------------------------------------------------------------------------
    # Main Business Flow
    # ------------------------------------------------------------------------
    async def _emit_memory_lifecycle_statuses(
        self,
        boost_stats: dict[str, int],
        deleted_count: int,
        emitter: Callable[[Any], Awaitable[None]],
    ) -> None:
        if not self.user_valves.show_status:
            return

        if boost_stats["boosted"] > 0:
            await emit_status(
                f"延长{boost_stats['boosted']}个记忆",
                emitter=emitter,
                status="complete",
            )
        if boost_stats["created"] > 0:
            await emit_status(
                f"初始化{boost_stats['created']}个记忆",
                emitter=emitter,
                status="complete",
            )
        if deleted_count > 0:
            await emit_status(
                f"清理{deleted_count}个记忆",
                emitter=emitter,
                status="complete",
            )

    def _find_latest_user_message(self, messages: list[dict[str, Any]]) -> str:
        return next(
            (
                cast(str, message.get("content", ""))
                for message in reversed(messages)
                if message.get("role") == "user"
            ),
            "",
        )

    def _build_memory_planning_input(
        self,
        messages: list[dict[str, Any]],
        related_memories: list[Memory],
    ) -> str:
        stringified_memories = json.dumps(
            [memory.model_dump(mode="json") for memory in related_memories]
        )
        conversation_str = self.messages_to_string(messages)
        latest_user_message = self._find_latest_user_message(messages)
        return (
            f"LATEST_USER_MESSAGE:\n{latest_user_message}\n\n"
            f"RECENT_CONVERSATION_SNIPPET:\n{conversation_str}\n\n"
            f"RELATED_MEMORIES_JSON:\n{stringified_memories}"
        )

    def _limit_existing_memory_ids_for_tools(
        self,
        related_memories: list[Memory],
    ) -> list[str]:
        existing_ids = [memory.mem_id for memory in related_memories]
        if len(existing_ids) <= MAX_MEMORY_IDS_FOR_TOOLS:
            return existing_ids

        self.log(
            f"truncating memory action ID constraints from {len(existing_ids)} to {MAX_MEMORY_IDS_FOR_TOOLS}",
            level="warning",
        )
        return existing_ids[:MAX_MEMORY_IDS_FOR_TOOLS]

    async def _plan_memory_actions(
        self,
        messages: list[dict[str, Any]],
        related_memories: list[Memory],
    ) -> MemoryActionRequestStub:
        planning_input = self._build_memory_planning_input(messages, related_memories)
        tool_models, tool_definitions, tool_choice = build_memory_action_tools(
            self._limit_existing_memory_ids_for_tools(related_memories)
        )
        action_plan = await self.query_openai_sdk(
            system_prompt=UNIFIED_SYSTEM_PROMPT,
            user_message=planning_input,
            response_model=tool_models,
            tools=tool_definitions,
            tool_choice=tool_choice,
        )
        return cast(MemoryActionRequestStub, action_plan)

    async def auto_memory(
        self,
        messages: list[dict[str, Any]],
        user: UserModel,
        emitter: Callable[[Any], Awaitable[None]],
    ) -> None:
        """Execute the auto-memory extraction and update flow."""

        if len(messages) < 2:
            self.log("need at least 2 messages for context", level="debug")
            return
        self.log(f"flow started. user ID: {user.id}", level="debug")

        related_memories = await self.get_related_memories(messages=messages, user=user)

        boost_stats = {"boosted": 0, "created": 0}
        if related_memories:
            boost_stats = await self.boost_memories(related_memories, user)

        deleted_count = await self.cleanup_expired_memories(user)
        await self._emit_memory_lifecycle_statuses(
            boost_stats=boost_stats,
            deleted_count=deleted_count,
            emitter=emitter,
        )

        try:
            action_plan = await self._plan_memory_actions(messages, related_memories)
            self.log(f"action plan: {action_plan}", level="debug")

            if not action_plan.actions:
                self.log("no changes", level="info")
                return

            await self.apply_memory_actions(
                action_plan=action_plan,
                user=user,
                emitter=emitter,
            )

        except Exception as e:
            self.log(f"memory planning failed: {e}", level="error")
            if self.user_valves.show_status:
                await emit_status(
                    "processing memories failed", emitter=emitter, status="error"
                )
            return None

    async def apply_memory_actions(
        self,
        action_plan: MemoryActionRequestStub,
        user: UserModel,
        emitter: Callable[[Any], Awaitable[None]],
    ) -> None:
        """
        Execute memory actions from the plan.
        Order: delete -> update -> add (prevents conflicts)
        """

        self.log("started apply_memory_actions", level="debug")
        actions = action_plan.actions

        if self.valves.debug_mode:
            self.log(f"memory actions to apply: {actions}", level="debug")

        counts = self._initialize_action_counts()
        action_groups = self._group_memory_actions(actions)

        for action_name in ACTION_ORDER:
            await self._apply_memory_action_group(
                action_name=action_name,
                actions=action_groups[action_name],
                user=user,
                counts=counts,
            )

        await self._log_and_emit_memory_action_summary(counts=counts, emitter=emitter)

    def _initialize_action_counts(self) -> dict[str, int]:
        return {action_name: 0 for action_name in ACTION_ORDER}

    def _group_memory_actions(
        self,
        actions: list[Union[MemoryAddAction, MemoryUpdateAction, MemoryDeleteAction]],
    ) -> dict[
        str, list[Union[MemoryAddAction, MemoryUpdateAction, MemoryDeleteAction]]
    ]:
        return {
            action_name: [action for action in actions if action.action == action_name]
            for action_name in ACTION_ORDER
        }

    async def _apply_memory_action_group(
        self,
        action_name: Literal["delete", "update", "add"],
        actions: list[Union[MemoryAddAction, MemoryUpdateAction, MemoryDeleteAction]],
        user: UserModel,
        counts: dict[str, int],
    ) -> None:
        for action in actions:
            await self._apply_memory_action_with_isolation(
                action_name=action_name,
                action=action,
                user=user,
                counts=counts,
            )

    async def _apply_memory_action_with_isolation(
        self,
        action_name: Literal["delete", "update", "add"],
        action: Union[MemoryAddAction, MemoryUpdateAction, MemoryDeleteAction],
        user: UserModel,
        counts: dict[str, int],
    ) -> None:
        try:
            applied = await self._execute_memory_action(
                action_name=action_name,
                action=action,
                user=user,
            )
            if applied:
                counts[action_name] += 1
        except Exception as e:
            self.log(
                f"memory action failed: failed to {self._build_memory_action_hint(action_name, action)}: {e}",
                level="error",
            )
            # Continue with next action instead of raising

    async def _execute_memory_action(
        self,
        action_name: Literal["delete", "update", "add"],
        action: Union[MemoryAddAction, MemoryUpdateAction, MemoryDeleteAction],
        user: UserModel,
    ) -> bool:
        if action_name == "delete":
            delete_action = cast(MemoryDeleteAction, action)
            try:
                await self._delete_memory_with_db(delete_action, user)
            except Exception as e:
                self.log(
                    f"failed to delete memory. id={delete_action.id}: {e}",
                    level="warning",
                )
                if self._advance_cleanup_tracking(delete_action.id, MemoryExpiries):
                    self.log(
                        f"removed lifecycle tracking after delete failures: {delete_action.id[:8]}...",
                        level="warning",
                    )
                else:
                    self.log(
                        f"retained lifecycle tracking after delete failure: {delete_action.id[:8]}...",
                        level="warning",
                    )
                return False

            self.log(f"deleted memory. id={delete_action.id}")
            MemoryExpiries.delete_by_mem_id(delete_action.id)
            return True

        if action_name == "update":
            update_action = cast(MemoryUpdateAction, action)
            if not update_action.content.strip():
                return False
            await update_memory_by_id(
                memory_id=update_action.id,
                request=_build_webui_request(),
                form_data=MemoryUpdateModel(content=update_action.content),
                user=user,
            )
            existing_expiry = MemoryExpiries.get_by_mem_id(update_action.id)
            if existing_expiry is not None:
                MemoryExpiries.update_expired_at(
                    update_action.id,
                    int(getattr(existing_expiry, "expired_at", 0) or 0),
                )
            self.log(f"updated memory. id={update_action.id}")
            return True

        add_action = cast(MemoryAddAction, action)
        if not add_action.content.strip():
            return False
        await self._add_memory_with_expiry(add_action, user)
        self.log(f"added memory. content={add_action.content}")
        return True

    def _build_memory_action_hint(
        self,
        action_name: Literal["delete", "update", "add"],
        action: Union[MemoryAddAction, MemoryUpdateAction, MemoryDeleteAction],
    ) -> str:
        if action_name == "add":
            return f"{action_name} memory"
        return f"{action_name} memory {cast(Any, action).id}"

    def _memory_action_status_labels(self) -> dict[str, str]:
        return {"delete": "删除", "update": "更新", "add": "新增"}

    def _build_memory_action_summary_parts(self, counts: dict[str, int]) -> list[str]:
        status_labels = self._memory_action_status_labels()
        return [
            f"{status_labels[action_name]}{counts[action_name]}个记忆"
            for action_name in ACTION_ORDER
            if counts[action_name] > 0
        ]

    async def _log_and_emit_memory_action_summary(
        self,
        counts: dict[str, int],
        emitter: Callable[[Any], Awaitable[None]],
    ) -> None:
        summary_parts = self._build_memory_action_summary_parts(counts)

        if summary_parts:
            self.log(", ".join(summary_parts), level="info")
        else:
            self.log("no changes", level="info")

        if not self.user_valves.show_status:
            return

        for summary_part in summary_parts:
            await emit_status(summary_part, emitter=emitter, status="complete")

    # ------------------------------------------------------------------------
    # Plugin Lifecycle Hooks
    # ------------------------------------------------------------------------
    def _resolve_inlet_user(
        self, __user__: Optional[dict[str, object]]
    ) -> UserModel | None:
        if __user__ is None:
            return None

        user_id = __user__.get("id")
        if not isinstance(user_id, str) or not user_id:
            self.log(
                "inlet context injection skipped: invalid user id", level="warning"
            )
            return None

        user = Users.get_user_by_id(user_id)
        if user is None:
            self.log("inlet context injection skipped: user not found", level="warning")
            return None

        return user

    def _resolve_inlet_messages(
        self, body: dict[str, object]
    ) -> list[dict[str, Any]] | None:
        messages = body.get("messages", [])
        if not isinstance(messages, list) or len(messages) < 1:
            return None
        return cast(list[dict[str, Any]], messages)

    def _resolve_inlet_memory_query_settings(self) -> tuple[int, float | None]:
        inlet_top_k = (
            self.valves.inlet_related_memories_n or self.valves.related_memories_n
        )
        inlet_threshold = (
            self.valves.minimum_memory_similarity
            if self.valves.inlet_minimum_memory_similarity is None
            else self.valves.inlet_minimum_memory_similarity
        )
        return inlet_top_k, inlet_threshold

    def _fetch_inlet_related_memories(
        self,
        messages: list[dict[str, Any]],
        user: UserModel,
    ) -> list[Memory]:
        inlet_top_k, inlet_threshold = self._resolve_inlet_memory_query_settings()
        return cast(
            list[Memory],
            _run_async_in_thread(
                self.get_related_memories(
                    messages=messages,
                    user=user,
                    top_k=inlet_top_k,
                    minimum_similarity=inlet_threshold,
                )
            ),
        )

    def inlet(
        self,
        body: dict[str, object],
        __event_emitter__: Callable[[Any], Awaitable[None]],
        __user__: Optional[dict[str, object]] = None,
    ) -> dict[str, object]:
        self.log(f"inlet: {__name__}", level="info")
        self.log(
            f"inlet: user ID: {__user__.get('id') if __user__ else 'no user'}",
            level="debug",
        )

        if not self.valves.enable_inlet_memory_context:
            return body

        user = self._resolve_inlet_user(__user__)
        if user is None:
            return body

        messages = self._resolve_inlet_messages(body)
        if messages is None:
            return body

        try:
            related_memories = self._fetch_inlet_related_memories(
                messages=messages, user=user
            )
        except Exception as e:
            self.log(f"inlet memory context injection failed: {e}", level="warning")
            return body

        if not related_memories:
            return body

        memory_context = self.build_inlet_memory_context(related_memories)
        body["messages"] = self.inject_memory_context_into_messages(
            messages=messages,
            memory_context=memory_context,
        )
        self.log(
            f"inlet injected {len(related_memories)} related memories into context",
            level="info",
        )

        return body

    def _should_skip_outlet_for_chat(self, chat_id: object) -> bool:
        return not chat_id or (
            isinstance(chat_id, str) and chat_id.startswith("local:")
        )

    def _resolve_outlet_user(self, __user__: dict[str, object]) -> UserModel:
        user = Users.get_user_by_id(str(__user__["id"]))
        if user is None:
            raise ValueError("user not found")
        return user

    def _memories_globally_enabled(self) -> bool:
        return bool(webui_app.state.config.ENABLE_MEMORIES)

    def _user_has_memories_permission(self, user: UserModel) -> bool:
        return has_permission(
            user.id, "features.memories", webui_app.state.config.USER_PERMISSIONS
        )

    def _memory_enabled_in_user_settings(self, user: UserModel) -> bool:
        return not (
            user.settings and not (user.settings.ui or {}).get("memory", True)  # type: ignore
        )

    def _resolve_outlet_user_valves(
        self,
        __user__: dict[str, object],
    ) -> "Filter.UserValves":
        user_valves = __user__.get("valves", self.UserValves())  # pyright: ignore[reportAttributeAccessIssue]
        if not isinstance(user_valves, self.UserValves):
            raise ValueError("invalid user valves")
        return cast(Filter.UserValves, user_valves)

    def _schedule_auto_memory_outlet_task(
        self,
        body: dict[str, object],
        user: UserModel,
        emitter: Callable[[Any], Awaitable[None]],
    ) -> None:
        _run_detached(
            self.auto_memory(
                messages=cast(list[dict[str, Any]], body.get("messages", [])),
                user=user,
                emitter=emitter,
            )
        )

    async def outlet(
        self,
        body: dict[str, object],
        __event_emitter__: Callable[[Any], Awaitable[None]],
        __user__: Optional[dict[str, object]] = None,
    ) -> dict[str, object]:
        self.log("outlet invoked")
        if __user__ is None:
            raise ValueError("user information is required")

        chat_id = body.get("chat_id")
        if self._should_skip_outlet_for_chat(chat_id):
            self.log("temporary chat, skipping", level="info")
            return body

        user = self._resolve_outlet_user(__user__)

        # Check global memories toggle (upstream: ENABLE_MEMORIES config flag)
        if not self._memories_globally_enabled():
            self.log(
                "memories are disabled globally (ENABLE_MEMORIES=False), skipping",
                level="info",
            )
            return body

        # Check per-user memories permission (upstream: features.memories permission)
        if not self._user_has_memories_permission(user):
            self.log(
                f"user {user.id} does not have 'features.memories' permission, skipping",
                level="info",
            )
            return body

        self.current_user: Optional[dict[str, object]] = __user__

        self.log(f"input user type = {type(__user__)}", level="debug")
        self.log(
            f"user.id = {user.id} user.name = {user.name} user.email = {user.email}",
            level="debug",
        )

        # Check if memory is disabled in user settings
        # user.settings is Optional[UserSettings], where UserSettings.ui is Optional[dict]
        if not self._memory_enabled_in_user_settings(user):
            self.log(
                "memory is disabled in user's personalization settings, skipping",
                level="info",
            )
            return body

        self.user_valves = self._resolve_outlet_user_valves(__user__)
        self.log(f"user valves = {self.user_valves}", level="debug")

        if not self.user_valves.enabled:
            self.log("component was disabled by user, skipping", level="info")
            return body

        self._schedule_auto_memory_outlet_task(
            body=body,
            user=user,
            emitter=__event_emitter__,
        )

        return body
