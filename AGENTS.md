# AGENTS.md

Guide for coding agents in this repository.

## Purpose

- Focus on root plugin development with fast, reliable validation.
- Keep diffs minimal and aligned with existing code patterns.
- Prevent false failures by skipping `open-webui/` submodule tests by default.

## Scope Boundaries

- Default editable scope:
  - `auto_memory.py`, `auto_chat_cleanup.py` (plugins)
  - `tests/` (test files)
  - root docs/config files related to plugin work
  - `open_webui/` mock package when needed for tests
- Reference-only by default:
  - `open-webui/` upstream submodule
- Do not edit submodule files unless explicitly requested.

## Repository Map

- Plugins:
  - `auto_memory.py` (memory management plugin)
  - `auto_chat_cleanup.py` (chat cleanup plugin)
  - `auto_memory.backup.py` (reference)
- Root tests:
  - `tests/test_auto_memory_function_calling.py`
  - `tests/test_auto_chat_cleanup.py`
  - `tests/conftest.py`
- Local mock dependency package:
  - `open_webui/internal/db.py`
  - `open_webui/models/users.py`
  - `open_webui/models/chats.py`
  - `open_webui/routers/memories.py`
  - `open_webui/utils/access_control/`
- Upstream submodule:
  - `open-webui/`

## Rules Files Check

- `.cursorrules`: not present
- `.cursor/rules/`: not present
- `.github/copilot-instructions.md`: not present
- If these appear later, merge their constraints into this guide.

## Environment

- Python: `>=3.12`
- Package manager: `uv`
- Dev deps: `pytest`, `pytest-asyncio`, `fastapi`, `pydantic`, `sqlalchemy`, `openai`, `ruff`

## Setup

Run from repo root:

```bash
uv sync --dev
```

Quick sanity checks:

```bash
uv run python --version
uv run pytest --version
```

## Build / Lint / Typecheck

- No dedicated root build command is configured.
- Linter available: `ruff` (not configured for auto-run)
- No dedicated root typecheck command is configured.
- File-level syntax check:

```bash
uv run python -m py_compile auto_memory.py auto_chat_cleanup.py tests/test_auto_memory_function_calling.py
```

## Test Commands (Default)

Always ignore submodule tests in normal plugin work:

```bash
uv run pytest -q --ignore=open-webui
uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py
uv run pytest -q --ignore=open-webui tests/test_auto_chat_cleanup.py::test_name
uv run pytest -x --ignore=open-webui
```

## Single-Test Workflow

- Start with one failing test node-id.
- Then run its test file.
- Then run broader root tests.
- Keep `--ignore=open-webui` in all routine runs.

## Why Ignore `open-webui/`

- The submodule contains upstream suites with extra dependencies.
- Running them from this repo can fail for unrelated reasons.
- Root plugin validation should remain isolated unless submodule work is requested.

## Type Hints (Modern Python 3.12+)

- Use modern syntax: `dict[str, int]` not `Dict[str, int]`
- Use union operator: `X | None` not `Optional[X]`
- Use built-in generics: `list[str]` not `List[str]`
- Define type aliases for clarity:
  ```python
  LogLevel = Literal["debug", "info", "warning", "error"]
  EmitterType = Callable[[object], Awaitable[None]] | None
  ```
- Keep Pydantic fields explicitly typed with Field descriptors

## Plugin Structure

Standard Filter class pattern:

```python
class Filter:
    valves: "Filter.Valves"
    user_valves: "Filter.UserValves"
    
    class Valves(BaseModel):
        field: type = Field(default=..., description="...")
    
    class UserValves(BaseModel):
        enabled: bool = Field(default=True, description="...")
    
    def __init__(self) -> None:
        self.valves = self.Valves()
        self.user_valves = self.UserValves()
    
    def log(self, message: str, level: LogLevel = "info") -> None:
        ...
    
    async def outlet(self, body: dict[str, object], ...) -> dict[str, object]:
        ...
```

## Module Docstrings

Include plugin metadata in module-level docstring:

```python
"""
title: Plugin Name
author: @username
description: Brief description
version: 1.0.0
required_open_webui_version: >= 0.8.1
license: MIT
"""
```

## Test Organization

- Test files: `test_<module_name>.py` in `tests/` directory
- All async tests use `@pytest.mark.asyncio` decorator
- No test classes - use pure functions: `test_<behavior>_<expected_outcome>`
- Fixtures defined in test files (not conftest.py unless truly shared)
- Helper functions for test data construction

## Test Fixtures & Mocks

Define fixtures in test files:

```python
@pytest.fixture
def mock_user():
    user = MagicMock()
    user.id = "test-user-123"
    return user
```

Mock patterns:
```python
from unittest.mock import AsyncMock, MagicMock, patch

# Function-level mock
with patch("module.function_name", new=mock_function):
    ...

# Object method mock
with patch.object(instance, "method_name", return_value=value):
    ...

# Database context mock
with patch("open_webui.internal.db.get_db") as mock_get_db:
    mock_get_db.return_value.__enter__.return_value = MagicMock()
    ...
```

## Imports

- Use import groups: 1) standard library, 2) third-party, 3) local modules
- Use absolute imports
- Use multiline parenthesized imports for long lists

## Formatting & Style

- Follow existing style in modified files
- Prefer double-quoted strings
- Keep code explicit over clever shortcuts
- Add comments only for non-obvious logic

## Naming

- Functions/methods/variables: `snake_case`
- Classes: `PascalCase`
- Constants/type aliases: `UPPER_SNAKE_CASE`
- Private helpers: leading underscore when needed

## Error Handling

- Validate inputs early and fail clearly
- Catch specific exceptions before generic ones
- Log useful context on failure
- Re-raise with clear intent when needed
- Avoid silent exception swallowing

## Logging

- Use logging, not `print`
- In plugin code, prefer `Filter.log(...)` conventions
- Level usage: `debug` (internal), `info` (milestones), `warning` (recoverable), `error` (failures)

## Async and Concurrency

- Keep async flow consistent with `async`/`await`
- Bridge blocking tasks with existing thread patterns when necessary
- Avoid introducing new concurrency models without a strong reason

## Data and DB

- Reuse existing DB context-manager patterns
- Keep DB operations short-lived and scoped
- Avoid broad stateful side effects

## Agent Execution Discipline

- Read relevant files before editing
- Match local patterns before adding abstractions
- Keep changes focused and non-disruptive
- Do not commit unless explicitly requested
- Report exactly what was verified and with which commands

## Escalation Rule

- Only run or edit `open-webui/` when the user explicitly asks for submodule-level work
