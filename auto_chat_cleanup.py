"""
title: Auto Chat Cleanup
author: @Drunk-Dream
description: Automatically clean up old chats based on idle time and retention count rules.
author_email: dongmh3@outlook.com
author_url: https://github.com/Drunk-Dream
repository_url: https://github.com/Drunk-Dream/open-webui-functions
version: 1.0.0
required_open_webui_version: >= 0.8.1
license: MIT

Compatibility Note:
- Version 1.0.0: Initial release with OR-based cleanup rules (idle time OR retention count)

Features:
- Automatic chat cleanup on every outlet trigger
- Dual cleanup rules: max idle days OR max retained chats (OR semantics)
- Protected chat support: skip folder chats, archived chats, pinned chats
- Per-user configuration via UserValves
- Status notifications for cleanup operations
- Debug mode for detailed logging

File Structure:
    1. Module Header & Imports (L1-30)
    2. Utility Functions (L31-60)
    3. Filter Class - Configuration & Core Logic (L61-end)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import inspect
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, Field
from sqlalchemy.orm import sessionmaker

from open_webui.internal.db import engine
from open_webui.models.chats import Chats
from open_webui.models.users import Users


LogLevel = Literal["debug", "info", "warning", "error"]
EmitterType = Callable[[object], Awaitable[None]] | None


class ChatListLike(Protocol):
    items: list[object]


async def _await_if_needed(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


@asynccontextmanager
async def _get_open_webui_db_context():
    try:
        from open_webui.internal.db import get_async_db_context
    except ImportError:
        session_factory = sessionmaker(bind=engine)
        session = session_factory()
        try:
            yield session
        finally:
            session.close()
    else:
        async with get_async_db_context() as db:
            yield db


async def emit_status(
    description: str,
    emitter: EmitterType,
    status: Literal["in_progress", "complete", "error"] = "complete",
    extra_data: dict[str, object] | None = None,
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


class Filter:
    valves: "Filter.Valves"
    user_valves: "Filter.UserValves"

    class Valves(BaseModel):
        max_idle_days: int | None = Field(
            default=None,
            description="maximum idle days before a chat becomes a cleanup candidate; None or 0 disables this rule.",
        )
        max_retained_chats: int | None = Field(
            default=None,
            description="maximum number of recent deletable chats to retain; None or 0 disables this rule.",
        )
        skip_folder_chats: bool = Field(
            default=True,
            description="skip chats that belong to a folder.",
        )
        skip_archived_chats: bool = Field(
            default=True,
            description="skip archived chats.",
        )
        skip_pinned_chats: bool = Field(
            default=True,
            description="skip pinned chats.",
        )
        debug_mode: bool = Field(
            default=False,
            description="enable debug logging.",
        )

    class UserValves(BaseModel):
        enabled: bool = Field(
            default=True,
            description="whether auto chat cleanup is enabled for the user.",
        )
        show_status: bool = Field(
            default=True,
            description="whether cleanup status should be shown to the user.",
        )

    def __init__(self) -> None:
        self.valves = self.Valves()
        self.user_valves = self.UserValves()

    def log(self, message: str, level: LogLevel = "info") -> None:
        if level == "debug" and not self.valves.debug_mode:
            return
        if level not in {"debug", "info", "warning", "error"}:
            level = "info"

        logger = logging.getLogger()
        log_method = getattr(logger, level, logger.info)
        _ = log_method(message)

    def _select_candidates(
        self,
        chats: Iterable[object],
        current_chat_id: str,
        now_ts: int,
    ) -> list[object]:
        deletable_pool: list[tuple[object, int]] = []

        for chat in chats:
            chat_id = getattr(chat, "id", None)
            if chat_id == current_chat_id:
                self.log(
                    f"skip chat {chat_id} because it is the current chat",
                    level="debug",
                )
                continue

            if self.valves.skip_folder_chats and getattr(chat, "folder_id", None):
                self.log(
                    f"skip chat {chat_id} because it belongs to a folder",
                    level="debug",
                )
                continue
            if self.valves.skip_archived_chats and bool(
                getattr(chat, "archived", False)
            ):
                self.log(
                    f"skip chat {chat_id} because it is archived",
                    level="debug",
                )
                continue
            if self.valves.skip_pinned_chats and bool(getattr(chat, "pinned", False)):
                self.log(
                    f"skip chat {chat_id} because it is pinned",
                    level="debug",
                )
                continue

            updated_at = getattr(chat, "updated_at", None)
            if not isinstance(updated_at, int):
                self.log(
                    f"skip chat {chat_id} because updated_at is invalid: {updated_at!r}",
                    level="warning",
                )
                continue

            deletable_pool.append((chat, updated_at))

        age_candidates: dict[str, object] = {}
        max_idle_days = self.valves.max_idle_days
        if max_idle_days not in (None, 0):
            cutoff_ts = now_ts - (max_idle_days * 86_400)
            for chat, updated_at in deletable_pool:
                if updated_at < cutoff_ts:
                    age_candidates[getattr(chat, "id")] = chat

        retained_candidates: dict[str, object] = {}
        max_retained_chats = self.valves.max_retained_chats
        if max_retained_chats not in (None, 0):
            sorted_pool = sorted(
                deletable_pool,
                key=lambda item: (-item[1], getattr(item[0], "id", "")),
            )
            for chat, _updated_at in sorted_pool[max_retained_chats:]:
                retained_candidates[getattr(chat, "id")] = chat

        selected: dict[str, object] = {}
        for candidate_map in (age_candidates, retained_candidates):
            for chat_id, chat in candidate_map.items():
                if chat_id not in selected:
                    selected[chat_id] = chat

        return list(selected.values())

    async def _load_user(self, user_id: str) -> object | None:
        return await _await_if_needed(Users.get_user_by_id(user_id))

    async def _load_user_chats(self, user_id: str) -> ChatListLike:
        response = await _await_if_needed(Chats.get_chats_by_user_id(user_id))
        return cast(ChatListLike, response)

    async def _delete_chat_by_id(self, chat_id: str, user_id: str) -> bool:
        async with _get_open_webui_db_context() as db:
            result = Chats.delete_chat_by_id_and_user_id(chat_id, user_id, db=db)
            return bool(await _await_if_needed(result))

    async def _cleanup_chats(
        self,
        chats: Iterable[object],
        user: object,
        emitter: EmitterType,
        current_chat_id: str,
        now_ts: int,
    ) -> list[object]:
        candidates = self._select_candidates(
            chats=chats,
            current_chat_id=current_chat_id,
            now_ts=now_ts,
        )
        user_id = getattr(user, "id", None)
        if user_id is None:
            return []
        user_id = cast(str, user_id)
        deleted_chats: list[object] = []

        self.log(
            f"starting cleanup for user {user_id} with {len(candidates)} candidate chats",
            level="info",
        )

        for chat in candidates:
            chat_id = cast(str, getattr(chat, "id"))
            try:
                success = await self._delete_chat_by_id(chat_id=chat_id, user_id=user_id)
                if not success:
                    self.log(f"failed to delete chat {chat_id}", level="warning")
                    continue
                deleted_chats.append(chat)
            except Exception as e:
                self.log(f"failed to delete chat {chat_id}: {e}", level="error")

        self.log(
            f"cleanup completed for user {user_id}: deleted {len(deleted_chats)} of {len(candidates)} candidate chats",
            level="info",
        )

        if self.user_valves.show_status and len(deleted_chats) > 0:
            await emit_status(
                f"已删除 {len(deleted_chats)} 个对话",
                emitter=emitter,
                status="complete",
            )

        return deleted_chats

    async def outlet(
        self,
        body: dict[str, object],
        __event_emitter__: Callable[[object], Awaitable[None]],
        __user__: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self.log("outlet invoked")
        if __user__ is None:
            raise ValueError("user information is required")

        chat_id_value = body.get("chat_id")
        if not isinstance(chat_id_value, str) or not chat_id_value:
            self.log("temporary chat, skipping", level="info")
            return body

        chat_id = chat_id_value
        if chat_id.startswith("local:"):
            self.log("temporary chat, skipping", level="info")
            return body

        user = await self._load_user(str(__user__["id"]))
        if user is None:
            raise ValueError("user not found")

        user_valves_data = __user__.get("valves", {})
        if isinstance(user_valves_data, self.UserValves):
            self.user_valves = user_valves_data
        elif isinstance(user_valves_data, dict):
            self.user_valves = self.UserValves(**user_valves_data)
        else:
            raise ValueError("invalid user valves")

        if not self.user_valves.enabled:
            self.log("component was disabled by user, skipping", level="info")
            return body

        chats_response = await self._load_user_chats(cast(str, getattr(user, "id")))
        chats = chats_response.items

        _ = await self._cleanup_chats(
            chats=chats,
            user=user,
            emitter=__event_emitter__,
            current_chat_id=chat_id,
            now_ts=int(time.time()),
        )

        return body
