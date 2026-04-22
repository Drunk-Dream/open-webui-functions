from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    import auto_chat_cleanup as auto_chat_cleanup_module
    from auto_chat_cleanup import Filter
except ImportError:
    auto_chat_cleanup_module = None
    Filter = None


CHAT_ACTIVE = "chat-active"
CHAT_FOLDER = "chat-folder"
CHAT_ARCHIVED = "chat-archived"
CHAT_PINNED = "chat-pinned"
CHAT_OLD_1 = "chat-old-1"
CHAT_OLD_2 = "chat-old-2"
CHAT_RECENT = "chat-recent"
FIXED_NOW = 1_730_000_000
DAY_SECONDS = 86_400


@pytest.fixture
def mock_user():
    user = MagicMock()
    user.id = "test-user-123"
    return user


@pytest.fixture
def mock_emitter():
    return AsyncMock()


def make_chat(**overrides):
    defaults = {
        "id": CHAT_RECENT,
        "user_id": "test-user-123",
        "updated_at": FIXED_NOW,
        "archived": False,
        "pinned": False,
        "folder_id": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _require_filter():
    if Filter is None:
        pytest.skip("auto_chat_cleanup not yet implemented")
    return Filter


def _build_filter(max_idle_days=30, max_retained_chats=3):
    filter_cls = _require_filter()
    filter_instance = filter_cls()
    filter_instance.valves = getattr(filter_instance, "valves", MagicMock())
    filter_instance.valves.max_idle_days = max_idle_days
    filter_instance.valves.max_retained_chats = max_retained_chats
    filter_instance.valves.debug_mode = False
    filter_instance.user_valves = getattr(
        filter_instance,
        "user_valves",
        getattr(filter_instance, "UserValves", MagicMock)(),
    )
    filter_instance.user_valves.show_status = False
    return filter_instance


def _ids(chats):
    return [chat.id for chat in chats]


def _select_candidates(filter_instance, chats, *, current_chat_id):
    if not hasattr(filter_instance, "_select_candidates"):
        pytest.fail("Filter._select_candidates is required for these tests")

    selector = filter_instance._select_candidates
    try:
        return selector(chats=chats, current_chat_id=current_chat_id, now_ts=FIXED_NOW)
    except TypeError:
        try:
            return selector(chats=chats, current_chat_id=current_chat_id)
        except TypeError:
            return selector(chats, current_chat_id)


async def _run_cleanup(
    filter_instance, *, chats, mock_user, mock_emitter, current_chat_id
):
    if hasattr(filter_instance, "_cleanup_chats"):
        cleanup = filter_instance._cleanup_chats
        try:
            return await cleanup(
                chats=chats,
                user=mock_user,
                emitter=mock_emitter,
                current_chat_id=current_chat_id,
                now_ts=FIXED_NOW,
            )
        except TypeError:
            return await cleanup(
                chats=chats,
                user=mock_user,
                emitter=mock_emitter,
                current_chat_id=current_chat_id,
            )

    if hasattr(filter_instance, "outlet"):
        body = {
            "chat_id": current_chat_id,
            "messages": [{"role": "user", "content": "hello"}],
        }
        outlet = filter_instance.outlet
        return await outlet(body=body, user=mock_user, __event_emitter__=mock_emitter)

    pytest.fail("Filter cleanup entrypoint is not available")


@pytest.mark.asyncio
async def test_age_rule_marks_old_chat_for_deletion(mock_user, mock_emitter):
    filter_instance = _build_filter(max_idle_days=30, max_retained_chats=99)
    chats = [
        make_chat(id=CHAT_ACTIVE, updated_at=FIXED_NOW),
        make_chat(id=CHAT_OLD_1, updated_at=FIXED_NOW - 31 * DAY_SECONDS),
        make_chat(id=CHAT_RECENT, updated_at=FIXED_NOW - 2 * DAY_SECONDS),
    ]

    candidates = _select_candidates(filter_instance, chats, current_chat_id=CHAT_ACTIVE)

    assert CHAT_OLD_1 in _ids(candidates)
    assert CHAT_RECENT not in _ids(candidates)


@pytest.mark.asyncio
async def test_count_rule_marks_excess_chats_for_deletion(mock_user, mock_emitter):
    filter_instance = _build_filter(max_idle_days=999, max_retained_chats=1)
    chats = [
        make_chat(id=CHAT_ACTIVE, updated_at=FIXED_NOW),
        make_chat(id=CHAT_RECENT, updated_at=FIXED_NOW - 1 * DAY_SECONDS),
        make_chat(id=CHAT_OLD_1, updated_at=FIXED_NOW - 2 * DAY_SECONDS),
        make_chat(id=CHAT_OLD_2, updated_at=FIXED_NOW - 3 * DAY_SECONDS),
    ]

    candidates = _select_candidates(filter_instance, chats, current_chat_id=CHAT_ACTIVE)

    assert set(_ids(candidates)) == {CHAT_OLD_1, CHAT_OLD_2}


@pytest.mark.asyncio
async def test_or_semantics_both_rules_active(mock_user, mock_emitter):
    filter_instance = _build_filter(max_idle_days=30, max_retained_chats=2)
    chats = [
        make_chat(id=CHAT_ACTIVE, updated_at=FIXED_NOW),
        make_chat(id=CHAT_RECENT, updated_at=FIXED_NOW - 1 * DAY_SECONDS),
        make_chat(id=CHAT_OLD_1, updated_at=FIXED_NOW - 31 * DAY_SECONDS),
        make_chat(id=CHAT_OLD_2, updated_at=FIXED_NOW - 3 * DAY_SECONDS),
        make_chat(id="chat-over-limit-only", updated_at=FIXED_NOW - 4 * DAY_SECONDS),
    ]

    candidates = _select_candidates(filter_instance, chats, current_chat_id=CHAT_ACTIVE)

    assert set(_ids(candidates)) == {
        CHAT_OLD_1,
        "chat-over-limit-only",
    }


@pytest.mark.asyncio
async def test_protected_folder_chat_never_deleted(mock_user, mock_emitter):
    filter_instance = _build_filter(max_idle_days=1, max_retained_chats=0)
    chats = [
        make_chat(id=CHAT_ACTIVE, updated_at=FIXED_NOW),
        make_chat(
            id=CHAT_FOLDER,
            updated_at=FIXED_NOW - 60 * DAY_SECONDS,
            folder_id="folder-1",
        ),
    ]

    candidates = _select_candidates(filter_instance, chats, current_chat_id=CHAT_ACTIVE)

    assert CHAT_FOLDER not in _ids(candidates)


@pytest.mark.asyncio
async def test_protected_archived_chat_never_deleted(mock_user, mock_emitter):
    filter_instance = _build_filter(max_idle_days=1, max_retained_chats=0)
    chats = [
        make_chat(id=CHAT_ACTIVE, updated_at=FIXED_NOW),
        make_chat(
            id=CHAT_ARCHIVED,
            updated_at=FIXED_NOW - 60 * DAY_SECONDS,
            archived=True,
        ),
    ]

    candidates = _select_candidates(filter_instance, chats, current_chat_id=CHAT_ACTIVE)

    assert CHAT_ARCHIVED not in _ids(candidates)


@pytest.mark.asyncio
async def test_protected_pinned_chat_never_deleted(mock_user, mock_emitter):
    filter_instance = _build_filter(max_idle_days=1, max_retained_chats=0)
    chats = [
        make_chat(id=CHAT_ACTIVE, updated_at=FIXED_NOW),
        make_chat(
            id=CHAT_PINNED,
            updated_at=FIXED_NOW - 60 * DAY_SECONDS,
            pinned=True,
        ),
    ]

    candidates = _select_candidates(filter_instance, chats, current_chat_id=CHAT_ACTIVE)

    assert CHAT_PINNED not in _ids(candidates)


@pytest.mark.asyncio
async def test_active_chat_never_deleted(mock_user, mock_emitter):
    filter_instance = _build_filter(max_idle_days=1, max_retained_chats=0)
    chats = [make_chat(id=CHAT_ACTIVE, updated_at=FIXED_NOW - 60 * DAY_SECONDS)]

    candidates = _select_candidates(filter_instance, chats, current_chat_id=CHAT_ACTIVE)

    assert CHAT_ACTIVE not in _ids(candidates)


@pytest.mark.asyncio
async def test_no_candidates_no_deletion(mock_user, mock_emitter):
    filter_instance = _build_filter(max_idle_days=30, max_retained_chats=5)
    chats = [
        make_chat(id=CHAT_ACTIVE, updated_at=FIXED_NOW),
        make_chat(id=CHAT_RECENT, updated_at=FIXED_NOW - 1 * DAY_SECONDS),
    ]

    if Filter is None:
        pytest.skip("auto_chat_cleanup not yet implemented")

    with (
        patch.object(filter_instance, "_select_candidates", return_value=[]),
        patch.object(
            filter_instance,
            "_load_user_chats",
            new=AsyncMock(return_value=chats),
            create=True,
        ),
        patch.object(
            auto_chat_cleanup_module.Chats,
            "delete_chat_by_id_and_user_id",
            new=AsyncMock(return_value=True),
            create=True,
        ) as mock_delete,
    ):
        await _run_cleanup(
            filter_instance,
            chats=chats,
            mock_user=mock_user,
            mock_emitter=mock_emitter,
            current_chat_id=CHAT_ACTIVE,
        )

    mock_delete.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_updated_at_skipped_with_warning(mock_user, mock_emitter):
    filter_instance = _build_filter(max_idle_days=30, max_retained_chats=1)
    chats = [
        make_chat(id=CHAT_ACTIVE, updated_at=FIXED_NOW),
        make_chat(id=CHAT_OLD_1, updated_at="bad-timestamp"),
        make_chat(id=CHAT_OLD_2, updated_at=FIXED_NOW - 31 * DAY_SECONDS),
    ]

    with patch.object(filter_instance, "log", create=True) as mock_log:
        candidates = _select_candidates(
            filter_instance, chats, current_chat_id=CHAT_ACTIVE
        )

    assert CHAT_OLD_1 not in _ids(candidates)
    assert CHAT_OLD_2 in _ids(candidates)
    mock_log.assert_called()
    assert any("warning" in str(call) for call in mock_log.call_args_list)


@pytest.mark.asyncio
async def test_outlet_supports_async_user_and_chat_loaders(mock_emitter):
    filter_instance = _build_filter(max_idle_days=30, max_retained_chats=5)
    filter_instance.user_valves.show_status = False

    async_user = SimpleNamespace(id="test-user-123")
    chats_response = SimpleNamespace(items=[])
    body = {"chat_id": CHAT_ACTIVE, "messages": [{"role": "user", "content": "hello"}]}

    with (
        patch.object(
            auto_chat_cleanup_module.Users,
            "get_user_by_id",
            new=AsyncMock(return_value=async_user),
        ),
        patch.object(
            auto_chat_cleanup_module.Chats,
            "get_chats_by_user_id",
            new=AsyncMock(return_value=chats_response),
        ),
        patch.object(filter_instance, "_cleanup_chats", new=AsyncMock(return_value=[])) as mock_cleanup,
    ):
        result = await filter_instance.outlet(
            body=body,
            __event_emitter__=mock_emitter,
            __user__={"id": async_user.id, "valves": {"enabled": True, "show_status": False}},
        )

    assert result is body
    mock_cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_chats_supports_async_delete_api(mock_user, mock_emitter):
    filter_instance = _build_filter(max_idle_days=30, max_retained_chats=0)
    filter_instance.user_valves.show_status = False
    chats = [make_chat(id=CHAT_OLD_1, updated_at=FIXED_NOW - 31 * DAY_SECONDS)]

    with patch.object(
        auto_chat_cleanup_module.Chats,
        "delete_chat_by_id_and_user_id",
        new=AsyncMock(return_value=True),
    ):
        deleted = await filter_instance._cleanup_chats(
            chats=chats,
            user=mock_user,
            emitter=mock_emitter,
            current_chat_id=CHAT_ACTIVE,
            now_ts=FIXED_NOW,
        )

    assert _ids(deleted) == [CHAT_OLD_1]
