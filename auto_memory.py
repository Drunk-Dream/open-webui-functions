"""
title: Auto Memory
author: @Drunk-Dream
description: automatically identify and store valuable information from chats as Memories.
author_email: dongmh3@outlook.com
author_url: https://github.com/Drunk-Dream
repository_url: https://github.com/Drunk-Dream/open-webui-functions
version: 1.4.4
required_open_webui_version: >= 0.8.1
license: see extension documentation file `auto_memory.md` (License section) for the licensing terms.

Forked from:
  Original Author: @nokodo
  Original Repository: https://nokodo.net/github/open-webui-extensions
  Original Funding: https://ko-fi.com/nokodo

Compatibility Note:
- Version 1.4.4: Added no-op memory planning support for cases with no add/update/delete actions.
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
from datetime import datetime
from typing import (
    Any,
    Awaitable,
    Callable,
    Literal,
    Optional,
    Protocol,
    Type,
    TypeVar,
    Union,
    cast,
    overload,
)

from fastapi import HTTPException, Request
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, create_model
from sqlalchemy import BigInteger, Column, Index, String
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

# Type aliases for better readability
EmitterType = Callable[[dict[str, Any]], Awaitable[None]]

# Configuration constants
SECONDS_PER_DAY = 86400
SHORT_MESSAGE_WORD_THRESHOLD = 8
MAX_MEMORY_IDS_FOR_TOOLS = 50
SIMILARITY_SCORE_PRECISION = 3

STRINGIFIED_MESSAGE_TEMPLATE = "-{index}. {role}: ```{content}```"
INLET_MEMORY_CONTEXT_PREFIX = "[AUTO_MEMORY_RELATED_MEMORIES]"

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
    new_content: str = Field(..., description="New content for the memory")


class MemoryDeleteAction(StrictBaseModel):
    action: Literal["delete"] = Field(..., description="Action type (delete)")
    id: str = Field(..., description="ID of the memory to delete")


class MemoryAddToolRequest(StrictBaseModel):
    content: str = Field(..., description="Content of the memory to add")


class MemoryUpdateToolRequest(StrictBaseModel):
    id: str = Field(..., description="ID of the memory to update")
    new_content: str = Field(..., description="New content for the memory")


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
            id=(id_literal_type, ...),  # type: ignore[valid-type]
            new_content=(str, ...),
            __base__=StrictBaseModel,
        )
        dynamic_delete_model = create_model(
            "DynamicMemoryDeleteToolRequest",
            id=(id_literal_type, ...),  # type: ignore[valid-type]
            __base__=StrictBaseModel,
        )
        tool_models["update_memory"] = cast(Type[BaseModel], dynamic_update_model)
        tool_models["delete_memory"] = cast(Type[BaseModel], dynamic_delete_model)

    tool_definitions: list[dict[str, Any]] = []
    for tool_name, model in tool_models.items():
        description = {
            "add_memory": "Add exactly one memory.",
            "update_memory": "Update exactly one existing memory by ID.",
            "delete_memory": "Delete exactly one existing memory by ID.",
        }[tool_name]
        tool_definitions.append(
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": description,
                    "parameters": model.model_json_schema(),
                },
            }
        )

    return tool_models, tool_definitions, "auto"


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
            if "created_at" not in meta:
                raise ValueError(
                    f"Missing 'created_at' in metadata for memory id={mem_id}"
                )
            if "updated_at" not in meta:
                # If updated_at is missing, default to created_at
                meta["updated_at"] = meta["created_at"]

            created_at = datetime.fromtimestamp(meta["created_at"])
            updated_at = datetime.fromtimestamp(meta["updated_at"])

            # Extract similarity score if available
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


def _run_async_in_thread(coro: Awaitable[T]) -> T:
    """Run async coroutine in dedicated thread with new event loop.

    Use cases:
        - Calling async code from sync context
        - Avoiding event loop conflicts

    Args:
        coro: Coroutine to execute

    Returns:
        Result from coroutine execution

    Raises:
        Exception: Re-raises any exception from coroutine
    """
    result_holder: dict[str, Any] = {}
    error_holder: dict[str, Exception] = {}

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result_holder["result"] = loop.run_until_complete(coro)
        except Exception as e:
            error_holder["error"] = e
        finally:
            loop.close()

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()

    if "error" in error_holder:
        raise error_holder["error"]

    return result_holder["result"]


def _run_detached(coro: Awaitable[Any]) -> None:
    """Run coroutine in detached background thread (fire-and-forget).

    Args:
        coro: Coroutine to execute in background
    """

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(coro)
        except Exception as e:
            logging.getLogger(__name__).exception("Detached task failed: %s", e)
        finally:
            loop.close()

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()


# --- Database Models ---


class MemoryExpiry(Base):
    """SQLAlchemy model for tracking memory expiration times."""

    __tablename__ = "auto_memory_expiry"

    mem_id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    expired_at = Column(BigInteger, nullable=False, index=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

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
        db: Optional[Session] = None,
    ) -> Optional[MemoryExpiry]:
        """Insert a new memory expiry record."""
        import time

        with get_db_context(db) as db:
            now = int(time.time())
            expiry = MemoryExpiry(
                mem_id=mem_id,
                user_id=user_id,
                expired_at=expired_at,
                created_at=now,
                updated_at=now,
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
        db: Optional[Session] = None,
    ) -> Optional[MemoryExpiry]:
        """Update the expiration time for a memory."""
        import time

        with get_db_context(db) as db:
            expiry = db.get(MemoryExpiry, mem_id)
            if not expiry:
                return None
            expiry.expired_at = expired_at  # pyright: ignore
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
        db: Optional[Session] = None,
    ) -> list[MemoryExpiry]:
        """Get all expired memories for a user."""
        with get_db_context(db) as db:
            return (
                db.query(MemoryExpiry)
                .filter(
                    MemoryExpiry.user_id == user_id,
                    MemoryExpiry.expired_at < now_timestamp,
                )
                .all()
            )


MemoryExpiries = MemoryExpiryTable()


# --- Database Initialization ---
def _ensure_table_exists():
    """Ensure MemoryExpiry table exists in database."""
    try:
        Base.metadata.create_all(
            engine,
            tables=[MemoryExpiry.__table__],  # pyright: ignore[reportArgumentType]
            checkfirst=True,
        )
    except Exception:
        pass


_ensure_table_exists()


# --- Vector Database ---
class MemoryRepository:
    """Centralized vector database operations for memory management."""

    def __init__(self, user_id: str):
        self.collection_name = f"user-memory-{user_id}"

    def get_all_memories(self):
        """Fetch all memories from vector database."""
        from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT

        return VECTOR_DB_CLIENT.get(collection_name=self.collection_name)

    async def upsert_with_vectors(self, items: list[dict[str, object]], user: object, embedding_function: "EmbeddingCallable") -> None:
        """Upsert items with vector generation."""
        from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT

        for item in items:
            vector = await embedding_function(str(item["text"]), user=user)
            item["vector"] = vector

        VECTOR_DB_CLIENT.upsert(collection_name=self.collection_name, items=items)


class EmbeddingCallable(Protocol):
    async def __call__(self, text: str, user: object) -> list[float]: ...
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

        # ===== Memory Expiry Configuration =====
        initial_expiry_days: int = Field(
            default=30,
            ge=1,
            description="initial expiry time for new memories (days). memories will expire after this many days if not accessed.",
        )
        extension_days: int = Field(
            default=14,
            ge=1,
            description="extension time when memory is accessed (days). accessed memories will have their expiry extended by this many days.",
        )
        max_expiry_days: int = Field(
            default=365,
            ge=1,
            description="maximum expiry time for memories (days). prevents memories from being extended indefinitely. accessed memories will not exceed this limit from current time.",
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

        for i in range(1, effective_messages_to_consider + 1):
            if i > len(messages):
                break
            try:
                message = messages[-i]
                stringified_messages.append(
                    STRINGIFIED_MESSAGE_TEMPLATE.format(
                        index=i,
                        role=message.get("role", "user"),
                        content=message.get("content", ""),
                    )
                )
            except Exception as e:
                self.log(f"error stringifying message {i}: {e}", level="warning")

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

        if "gpt-5" in model_name:
            temperature = 1.0
            extra_args = {"reasoning_effort": "medium"}
        elif "gemini-3" in model_name:
            temperature = 1.0
            extra_args = {}
        else:
            temperature = 0.3
            extra_args = {}

        # Note: OpenAI SDK v1.0+ supports context manager, but we use direct instantiation
        # for simplicity since the client is short-lived within this method scope.
        # For long-lived clients, consider: with OpenAI(...) as client:
        client = OpenAI(api_key=api_key, base_url=api_url)
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        if response_model is None:
            request_args: dict[str, Any] = {
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
                **extra_args,
            }
            if tools is not None:
                request_args["tools"] = cast(Any, tools)
            if tool_choice is not None:
                request_args["tool_choice"] = cast(Any, tool_choice)
            response = client.chat.completions.create(**request_args)
            self.log(f"sdk response: {response}", level="debug")

            text_response = response.choices[0].message.content
            if text_response is None:
                raise ValueError(f"no text response from LLM. message={text_response}")

            return text_response

        if isinstance(response_model, dict):
            model_label = f"tool-map[{', '.join(sorted(response_model.keys()))}]"
        else:
            model_label = cast(Any, response_model).__name__
        self.log(
            f"calling tool-calling path with {model_label}",
            level="debug",
        )

        if not tools:
            raise ValueError(
                "response_model requires tools to be provided for tool-calling path"
            )

        request_args: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "tools": cast(Any, tools),
            **extra_args,
        }
        if tool_choice is not None:
            request_args["tool_choice"] = cast(Any, tool_choice)

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
            actions: list[
                Union[MemoryAddAction, MemoryUpdateAction, MemoryDeleteAction]
            ] = []
            for tool_call in tool_calls:
                tool_name = tool_call.function.name
                if tool_name not in response_model:
                    expected = ", ".join(sorted(response_model.keys()))
                    raise ValueError(
                        f"unexpected tool name: {tool_name!r}; expected one of [{expected}]"
                    )

                raw_args = tool_call.function.arguments
                if not raw_args or not raw_args.strip():
                    raise ValueError(
                        f"tool call {tool_name!r} returned empty arguments"
                    )

                self.log(
                    f"tool call {tool_name} arguments: {raw_args[:500]}", level="debug"
                )
                parsed_args = response_model[tool_name].model_validate_json(raw_args)
                if tool_name == "add_memory":
                    parsed_add = cast(MemoryAddToolRequest, parsed_args)
                    actions.append(
                        MemoryAddAction(action="add", content=parsed_add.content)
                    )
                elif tool_name == "update_memory":
                    parsed_update = cast(MemoryUpdateToolRequest, parsed_args)
                    actions.append(
                        MemoryUpdateAction(
                            action="update",
                            id=parsed_update.id,
                            new_content=parsed_update.new_content,
                        )
                    )
                elif tool_name == "delete_memory":
                    parsed_delete = cast(MemoryDeleteToolRequest, parsed_args)
                    actions.append(
                        MemoryDeleteAction(action="delete", id=parsed_delete.id)
                    )

            return MemoryActionRequestStub(actions=actions)

        model_cls = cast(Any, response_model)
        if len(tool_calls) > 1:
            raise ValueError(
                f"expected exactly one tool call but got {len(tool_calls)}"
            )

        tool_call = tool_calls[0]
        raw_args = tool_call.function.arguments
        if not raw_args or not raw_args.strip():
            raise ValueError("tool call returned empty arguments")

        self.log(f"tool call arguments: {raw_args[:500]}", level="debug")
        plan = model_cls.model_validate_json(raw_args)
        return plan

    def __init__(self):
        self.valves = self.Valves()

    def _delete_memory_sync(self, mem_id: str, user: UserModel) -> None:
        """Synchronous helper for deleting memory in thread pool."""
        from open_webui.internal.db import get_db
        from open_webui.main import app as webui_app

        with get_db() as db:
            import asyncio

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    delete_memory_by_id(
                        memory_id=mem_id,
                        request=Request(scope={"type": "http", "app": webui_app}),
                        user=user,
                        db=db,
                    )
                )
            finally:
                loop.close()

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
        """Initialize expiry record for new memory.

        Args:
            mem_id: Memory ID to initialize expiry for
            user_id: User ID who owns the memory
        """
        import time

        now_timestamp = int(time.time())
        expired_at = now_timestamp + (self.valves.initial_expiry_days * SECONDS_PER_DAY)
        existing = MemoryExpiries.get_by_mem_id(mem_id)
        if existing:
            MemoryExpiries.update_expired_at(mem_id, expired_at)
            self.log(
                f"reset expiry for new memory {mem_id[:8]}... to {self.valves.initial_expiry_days} days",
                level="debug",
            )
            return
        MemoryExpiries.insert(
            mem_id=mem_id,
            user_id=user_id,
            expired_at=expired_at,
        )
        self.log(
            f"initialized expiry for new memory {mem_id[:8]}... to {self.valves.initial_expiry_days} days",
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
                request=Request(scope={"type": "http", "app": webui_app}),
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
            request=Request(scope={"type": "http", "app": webui_app}),
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
            return user_valve_value if user_valve_value is not None else admin_fallback

        # Allow admins to override without providing their own API key
        if hasattr(self, "current_user") and self.current_user is not None and self.current_user.get("role") == "admin":
            if user_valve_value is not None:
                self.log(
                    f"'{valve_name or 'unknown'}' override allowed for admin user",
                    level="info",
                )
            return user_valve_value if user_valve_value is not None else admin_fallback

        if self.valves.allow_unsafe_user_overrides:
            if user_valve_value is not None:
                self.log(
                    f"'{valve_name or 'unknown'}' override allowed (unsafe overrides enabled)",
                    level="warning",
                )
            return user_valve_value if user_valve_value is not None else admin_fallback

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
        query_parts = []

        # Find last user message and its index
        last_user_idx = None
        last_user_msg = None
        for idx in range(len(messages) - 1, -1, -1):
            if messages[idx].get("role") == "user":
                last_user_idx = idx
                last_user_msg = messages[idx].get("content", "")
                break

        if last_user_msg is None or last_user_idx is None:
            raise ValueError("no user message found in messages")

        # Count words in last user message
        user_word_count = len(last_user_msg.split())

        # Check if we should include extra context for short messages
        include_extra_context = user_word_count <= SHORT_MESSAGE_WORD_THRESHOLD

        # Build query from most recent to older messages
        # Add last assistant response (if exists)
        if last_user_idx + 1 < len(messages):
            last_assistant_msg = messages[last_user_idx + 1].get("content", "")
            if last_assistant_msg:
                query_parts.append(f"Assistant: {last_assistant_msg}")

        # Add last user message
        query_parts.append(f"User: {last_user_msg}")

        # If short message, add previous assistant context
        if include_extra_context and last_user_idx > 0:
            prev_assistant_msg = messages[last_user_idx - 1].get("content", "")
            if (
                prev_assistant_msg
                and messages[last_user_idx - 1].get("role") == "assistant"
            ):
                query_parts.append(f"Assistant: {prev_assistant_msg}")

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
                request=Request(scope={"type": "http", "app": webui_app}),
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
    def _run_async_blocking(self, coro: Awaitable[Any]) -> Any:
        """Run async coroutine synchronously (blocks until complete).

        Deprecated: Use _run_async_in_thread instead.
        Kept for backward compatibility.
        """
        return _run_async_in_thread(coro)

    def build_inlet_memory_context(self, memories: list[Memory]) -> str:
        memory_lines = []
        for idx, memory in enumerate(memories, start=1):
            score_text = (
                f" (similarity={memory.similarity_score:.3f})"
                if memory.similarity_score is not None
                else ""
            )
            memory_lines.append(f"{idx}. {memory.content}{score_text}")

        joined_memories = "\n".join(memory_lines)
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
        cleaned_messages = []
        for message in messages:
            is_old_memory_context = (
                message.get("role") == "system"
                and isinstance(message.get("content"), str)
                and message["content"].startswith(INLET_MEMORY_CONTEXT_PREFIX)
            )
            if not is_old_memory_context:
                cleaned_messages.append(message)

        insert_at = 0
        while (
            insert_at < len(cleaned_messages)
            and cleaned_messages[insert_at].get("role") == "system"
        ):
            insert_at += 1

        cleaned_messages.insert(
            insert_at,
            {"role": "system", "content": memory_context},
        )
        return cleaned_messages

    # ------------------------------------------------------------------------
    # Memory Lifecycle Management
    # ------------------------------------------------------------------------
    async def cleanup_expired_memories(
        self,
        user: UserModel,
    ) -> dict[str, int]:
        """
        Delete expired memories from both vector database and expiry table.

        Queries the MemoryExpiryTable for records where expired_at < now,
        then deletes each expired memory from the vector database and
        removes the corresponding expiry record.

        Args:
            user: User model for ownership verification

        Returns:
            Statistics dict: {"total": N, "deleted": M}
        """
        import time

        now_timestamp = int(time.time())
        expiry_table = MemoryExpiryTable()

        # Get expired records
        expired_records = expiry_table.get_expired(user.id, now_timestamp)

        stats = {"total": len(expired_records), "deleted": 0}

        if not expired_records:
            self.log("no expired memories found", level="debug")
            return stats

        self.log(f"found {len(expired_records)} expired memories", level="info")

        for record in expired_records:
            expiry_table_deleted = False

            try:
                await asyncio.to_thread(
                    self._delete_memory_sync,
                    mem_id=str(record.mem_id),
                    user=user,
                )
                self.log(
                    f"deleted memory from vector DB: {record.mem_id[:8]}...",
                    level="debug",
                )
            except Exception as e:
                self.log(
                    f"failed to delete memory from vector DB {record.mem_id}: {e}. "
                    f"Memory may have been manually deleted. Continuing to clean up expiry record.",
                    level="warning",
                )

            # Always try to delete from expiry table, even if vector DB deletion failed
            try:
                expiry_table.delete_by_mem_id(str(record.mem_id))
                expiry_table_deleted = True
                self.log(
                    f"deleted expiry record: {record.mem_id[:8]}...", level="debug"
                )
            except Exception as e:
                self.log(
                    f"failed to delete expiry record {record.mem_id}: {e}",
                    level="error",
                )

            # Count as deleted if at least expiry table was cleaned up
            # (vector DB deletion failure is acceptable if memory was already gone)
            if expiry_table_deleted:
                stats["deleted"] += 1

        self.log(
            f"cleanup complete: deleted {stats['deleted']}/{stats['total']} expired memories",
            level="info",
        )

        return stats

    async def boost_memories(
        self,
        related_memories: list[Memory],
        user: UserModel,
    ) -> dict[str, int]:
        """
        Boost (extend expiry time) for retrieved memories.

        For each memory in related_memories:
        - If expiry record exists:
          * Extend expired_at by extension_days from existing expiry
          * Ensure at least extension_days from now (handles already-expired memories)
          * Cap at max_expiry_days from now
        - If expiry record doesn't exist: create new record with expired_at = now + initial_expiry_days

        Args:
            related_memories: List of Memory objects that were retrieved
            user: User model

        Returns:
            Statistics dict: {"total": N, "boosted": M, "created": K}
        """
        import time

        if not related_memories:
            return {"total": 0, "boosted": 0, "created": 0}

        now_timestamp = int(time.time())
        expiry_table = MemoryExpiryTable()

        stats = {"total": len(related_memories), "boosted": 0, "created": 0}

        self.log(f"boosting {len(related_memories)} retrieved memories", level="debug")

        for memory in related_memories:
            try:
                # Check if expiry record exists
                existing = expiry_table.get_by_mem_id(memory.mem_id)

                if existing:
                    # Extend expiry from existing expired_at, not from now
                    # This ensures we truly "extend by N days" rather than "reset to N days from now"
                    extended_from_existing = int(existing.expired_at) + (  # pyright: ignore[reportArgumentType]
                        self.valves.extension_days * SECONDS_PER_DAY
                    )

                    # Ensure at least extension_days from now (handles already-expired memories)
                    extended_from_now = now_timestamp + (
                        self.valves.extension_days * SECONDS_PER_DAY
                    )

                    # Take the maximum to ensure we always give at least extension_days
                    extended_expired_at = max(extended_from_existing, extended_from_now)

                    # Apply maximum expiry limit to prevent indefinite extension
                    max_allowed_expired_at = now_timestamp + (
                        self.valves.max_expiry_days * SECONDS_PER_DAY
                    )
                    new_expired_at = min(extended_expired_at, max_allowed_expired_at)

                    expiry_table.update_expired_at(memory.mem_id, new_expired_at)
                    stats["boosted"] += 1

                    days_extended = (
                        new_expired_at - int(existing.expired_at)  # pyright: ignore[reportArgumentType]
                    ) / SECONDS_PER_DAY  # type: ignore
                    self.log(
                        f"boosted memory {memory.mem_id[:8]}... expiry extended by {days_extended:.1f} days "
                        f"(requested {self.valves.extension_days}, capped at {self.valves.max_expiry_days} days from now)",
                        level="debug",
                    )
                else:
                    # Create new record
                    new_expired_at = now_timestamp + (
                        self.valves.initial_expiry_days * SECONDS_PER_DAY
                    )
                    expiry_table.insert(
                        mem_id=memory.mem_id,
                        user_id=user.id,
                        expired_at=new_expired_at,
                    )
                    stats["created"] += 1
                    self.log(
                        f"created expiry for memory {memory.mem_id[:8]}... expires in {self.valves.initial_expiry_days} days",
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

        # === Get Related Memories ===
        related_memories = await self.get_related_memories(messages=messages, user=user)

        # === Boost Retrieved Memories (extend expiry) ===
        if related_memories:
            boost_stats = await self.boost_memories(related_memories, user)
            if (
                boost_stats["boosted"] > 0 or boost_stats["created"] > 0
            ) and self.user_valves.show_status:
                await emit_status(
                    f"boosted {boost_stats['boosted']} memories, created {boost_stats['created']} expiry records",
                    emitter=emitter,
                    status="complete",
                )

        # === Cleanup Expired Memories ===
        cleanup_stats = await self.cleanup_expired_memories(user)
        if cleanup_stats["deleted"] > 0 and self.user_valves.show_status:
            await emit_status(
                f"cleaned up {cleanup_stats['deleted']} expired memories",
                emitter=emitter,
                status="complete",
            )

        stringified_memories = json.dumps(
            [memory.model_dump(mode="json") for memory in related_memories]
        )
        conversation_str = self.messages_to_string(messages)
        latest_user_message = next(
            (
                m.get("content", "")
                for m in reversed(messages)
                if m.get("role") == "user"
            ),
            "",
        )
        planning_input = (
            f"LATEST_USER_MESSAGE:\n{latest_user_message}\n\n"
            f"RECENT_CONVERSATION_SNIPPET:\n{conversation_str}\n\n"
            f"RELATED_MEMORIES_JSON:\n{stringified_memories}"
        )

        try:
            existing_ids = [m.mem_id for m in related_memories]
            if len(existing_ids) > MAX_MEMORY_IDS_FOR_TOOLS:
                self.log(
                    f"truncating memory action ID constraints from {len(existing_ids)} to {MAX_MEMORY_IDS_FOR_TOOLS}",
                    level="warning",
                )
                existing_ids = existing_ids[:MAX_MEMORY_IDS_FOR_TOOLS]

            tool_models, tool_definitions, tool_choice = build_memory_action_tools(
                existing_ids
            )
            action_plan = await self.query_openai_sdk(
                system_prompt=UNIFIED_SYSTEM_PROMPT,
                user_message=planning_input,
                response_model=tool_models,
                tools=tool_definitions,
                tool_choice=tool_choice,
            )
            action_plan = cast(MemoryActionRequestStub, action_plan)
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
                    "memory planning failed", emitter=emitter, status="error"
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
        from open_webui.internal.db import get_db

        self.log("started apply_memory_actions", level="debug")
        actions = action_plan.actions

        # Show processing status
        if emitter is not None and len(actions) > 0:
            self.log(f"processing {len(actions)} memory actions", level="debug")
            await emit_status(
                f"processing {len(actions)} memory actions",
                emitter=emitter,
                status="in_progress",
            )
        if self.valves.debug_mode:
            self.log(f"memory actions to apply: {actions}", level="debug")

        # Group actions and define handlers
        operations: dict[str, dict[str, Any]] = {
            "delete": {
                "actions": [a for a in actions if a.action == "delete"],
                "handler": lambda a: self._delete_memory_with_db(a, user),
                "log_msg": lambda a: f"deleted memory. id={a.id}",
                "error_msg": lambda a, e: f"failed to delete memory {a.id}: {e}",
                "skip_empty": lambda a: False,
                "status_verb": "deleted",
            },
            "update": {
                "actions": [a for a in actions if a.action == "update"],
                "handler": lambda a: update_memory_by_id(
                    memory_id=a.id,
                    request=Request(scope={"type": "http", "app": webui_app}),
                    form_data=MemoryUpdateModel(content=a.new_content),
                    user=user,
                ),
                "log_msg": lambda a: f"updated memory. id={a.id}",
                "error_msg": lambda a, e: f"failed to update memory {a.id}: {e}",
                "skip_empty": lambda a: not a.new_content.strip(),
                "status_verb": "updated",
            },
            "add": {
                "actions": [a for a in actions if a.action == "add"],
                "handler": lambda a: self._add_memory_with_expiry(a, user),
                "log_msg": lambda a: f"added memory. content={a.content}",
                "error_msg": lambda a, e: f"failed to add memory: {e}",
                "skip_empty": lambda a: not a.content.strip(),
                "status_verb": "saved",
            },
        }

        # Process all operations in order
        counts = {}

        for op_name, op_config in operations.items():
            counts[op_name] = 0
            for action in op_config["actions"]:
                if op_config["skip_empty"](action):
                    continue
                try:
                    await op_config["handler"](action)
                    self.log(op_config["log_msg"](action))
                    counts[op_name] += 1

                except Exception as e:
                    raise RuntimeError(
                        f"memory action failed: {op_config['error_msg'](action, e)}"
                    )

        # Build status message
        status_parts = []
        for op_name, op_config in operations.items():
            count = counts[op_name]
            if count > 0:
                memory_word = "memory" if count == 1 else "memories"
                status_parts.append(f"{op_config['status_verb']} {count} {memory_word}")

        status_message = ", ".join(status_parts)
        self.log(status_message or "no changes", level="info")

        if status_message and self.user_valves.show_status:
            await emit_status(status_message, emitter=emitter, status="complete")

    # ------------------------------------------------------------------------
    # Plugin Lifecycle Hooks
    # ------------------------------------------------------------------------
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
        if __user__ is None:
            return body

        user_id = __user__.get("id")
        if not isinstance(user_id, str) or not user_id:
            self.log(
                "inlet context injection skipped: invalid user id", level="warning"
            )
            return body

        user = Users.get_user_by_id(user_id)
        if user is None:
            self.log("inlet context injection skipped: user not found", level="warning")
            return body

        messages = body.get("messages", [])
        if not isinstance(messages, list) or len(messages) < 1:
            return body

        inlet_top_k = (
            self.valves.inlet_related_memories_n or self.valves.related_memories_n
        )
        inlet_threshold = (
            self.valves.minimum_memory_similarity
            if self.valves.inlet_minimum_memory_similarity is None
            else self.valves.inlet_minimum_memory_similarity
        )

        try:
            related_memories = cast(
                list[Memory],
                self._run_async_blocking(
                    self.get_related_memories(
                        messages=messages,
                        user=user,
                        top_k=inlet_top_k,
                        minimum_similarity=inlet_threshold,
                    )
                ),
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
        if not chat_id or (isinstance(chat_id, str) and chat_id.startswith("local:")):
            self.log("temporary chat, skipping", level="info")
            return body

        user = Users.get_user_by_id(str(__user__["id"]))
        if user is None:
            raise ValueError("user not found")

        # Check global memories toggle (upstream: ENABLE_MEMORIES config flag)
        if not webui_app.state.config.ENABLE_MEMORIES:
            self.log(
                "memories are disabled globally (ENABLE_MEMORIES=False), skipping",
                level="info",
            )
            return body

        # Check per-user memories permission (upstream: features.memories permission)
        if not has_permission(
            user.id, "features.memories", webui_app.state.config.USER_PERMISSIONS
        ):
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
        if user.settings and not (user.settings.ui or {}).get("memory", True):  # type: ignore
            self.log(
                "memory is disabled in user's personalization settings, skipping",
                level="info",
            )
            return body

        self.user_valves = __user__.get("valves", self.UserValves())  # pyright: ignore[reportAttributeAccessIssue]
        if not isinstance(self.user_valves, self.UserValves):
            raise ValueError("invalid user valves")
        self.user_valves = cast(Filter.UserValves, self.user_valves)
        self.log(f"user valves = {self.user_valves}", level="debug")

        if not self.user_valves.enabled:
            self.log("component was disabled by user, skipping", level="info")
            return body

        _run_detached(
            self.auto_memory(
                messages=body.get("messages", []),  # type: ignore[arg-type]
                user=user,
                emitter=__event_emitter__,
            )
        )

        return body
