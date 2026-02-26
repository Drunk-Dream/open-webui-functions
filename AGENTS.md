# AGENTS.md

Guide for coding agents in this repository.

## Purpose

- Focus on root plugin development with fast, reliable validation.
- Keep diffs minimal and aligned with existing code patterns.
- Prevent false failures by skipping `open-webui/` submodule tests by default.

## Scope Boundaries

- Default editable scope:
  - `auto_memory.py`
  - `tests/`
  - root docs/config files related to plugin work
  - `open_webui/` mock package when needed for tests
- Reference-only by default:
  - `open-webui/` upstream submodule
- Do not edit submodule files unless explicitly requested.

## Repository Map

- Plugin:
  - `auto_memory.py`
  - `auto_memory.backup.py` (reference)
- Root tests:
  - `tests/test_auto_memory_function_calling.py`
  - `tests/conftest.py`
- Local mock dependency package:
  - `open_webui/internal/db.py`
  - `open_webui/models/users.py`
  - `open_webui/routers/memories.py`
  - `open_webui/utils/access_control.py`
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
- Dev deps include `pytest`, `pytest-asyncio`, `fastapi`, `pydantic`, `sqlalchemy`, `openai`.

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
- No dedicated root lint command is configured.
- No dedicated root typecheck command is configured.
- File-level syntax check:

```bash
uv run python -m py_compile auto_memory.py tests/test_auto_memory_function_calling.py
```

## Test Commands (Default)

Always ignore submodule tests in normal plugin work:

```bash
uv run pytest -q --ignore=open-webui
uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py
uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py::test_name
uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py::TestClassName::test_method_name
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

## Imports

- Use import groups in order:
  1) standard library
  2) third-party
  3) local modules
- Use absolute imports.
- Use multiline parenthesized imports for long lists.

## Formatting

- Follow existing style in modified files.
- Prefer double-quoted strings.
- Keep code explicit over clever shortcuts.
- Add comments only for non-obvious logic.

## Typing

- Preserve type hints for modified signatures.
- Follow local style (`Optional`, `Literal`, `Union`, `TypeVar`, `cast`, `overload`).
- Keep Pydantic fields explicitly typed.
- Do not introduce unsafe type-suppression shortcuts.

## Naming

- Functions/methods/variables: `snake_case`
- Classes: `PascalCase`
- Constants/type aliases: `UPPER_SNAKE_CASE`
- Private helpers: leading underscore when needed

## Error Handling

- Validate inputs early and fail clearly.
- Catch specific exceptions before generic ones.
- Log useful context on failure.
- Re-raise with clear intent when needed.
- Avoid silent exception swallowing.

## Logging

- Use logging, not `print`.
- In plugin code, prefer `Filter.log(...)` conventions.
- Level usage:
  - `debug`: internal details
  - `info`: milestones
  - `warning`: recoverable anomalies
  - `error`: failures

## Async and Concurrency

- Keep async flow consistent with `async`/`await`.
- Bridge blocking tasks with existing thread patterns when necessary.
- Avoid introducing new concurrency models without a strong reason.

## Data and DB

- Reuse existing DB context-manager patterns.
- Keep DB operations short-lived and scoped.
- Avoid broad stateful side effects.

## Agent Execution Discipline

- Read relevant files before editing.
- Match local patterns before adding abstractions.
- Keep changes focused and non-disruptive.
- Do not commit unless explicitly requested.
- Report exactly what was verified and with which commands.

## Escalation Rule

- Only run or edit `open-webui/` when the user explicitly asks for submodule-level work.
