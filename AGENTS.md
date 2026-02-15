# AGENTS.md

Guidance for autonomous coding agents in this repository.

## Project Scope

- This repo is for Open WebUI plugin/function development.
- Current active plugin is `auto_memory.py`.
- `open-webui/` is the upstream Open WebUI project as a submodule, used as reference.
- `open_webui/` is a minimal local mock package for plugin imports/tests.
- Default target is root plugin code, not submodule internals.

## Important Directories

- Root plugin files:
  - `auto_memory.py`
  - `auto_memory.backup.py` (reference)
  - `pyproject.toml`
- Mock dependency package:
  - `open_webui/internal/db.py`
  - `open_webui/main.py`
  - `open_webui/models/users.py`
- Reference-only submodule:
  - `open-webui/`

## Environment Baseline

- Python: `>=3.12` (`pyproject.toml`).
- Dependency manager: `uv` (`uv.lock` present).
- Root dev dependencies include `pytest`, `pytest-asyncio`, `fastapi`, `pydantic`, `sqlalchemy`, `openai`.

## Setup Commands

Run from repository root:

```bash
uv sync --dev
```

Optional checks:

```bash
.venv/bin/python --version
.venv/bin/pytest --version
```

## Build / Lint / Typecheck / Test Commands

Root-level status today:

- Build: no dedicated root build command configured.
- Lint: no dedicated root lint command configured.
- Typecheck: no dedicated root typecheck command configured.

Testing commands:

```bash
uv run pytest
uv run pytest path/to/test_file.py
uv run pytest path/to/test_file.py::test_function_name
uv run pytest path/to/test_file.py::TestClassName::test_method_name
```

Useful pytest flags:

```bash
uv run pytest -q
uv run pytest -x
```

## Single-Test Guidance

- Prefer node-id targeting for fast feedback.
- Run one failing test first, then its file, then broader suite if needed.

## Command Selection Policy

- Prefer root commands for plugin work.
- Do not assume submodule (`open-webui/`) Makefile/scripts apply to root plugin flow.
- Only run submodule commands when explicitly asked to work in `open-webui/`.

## Coding Style - Imports

Observed style in `auto_memory.py`:

- Import grouping:
  1. standard library
  2. third-party packages
  3. local package imports
- Use absolute imports.
- Use multiline parenthesized imports for long type lists.
- Keep blank lines between groups.

## Coding Style - Formatting

- Follow existing formatting in touched files.
- Prefer double-quoted strings, matching current dominant style.
- Keep code readable and explicit over clever shortcuts.
- Add comments only when logic is non-obvious.

## Coding Style - Typing

- Add/maintain type hints for modified function signatures.
- Existing code uses `Literal`, `Optional`, `Union`, `TypeVar`, `overload`, and `cast`.
- Match local file style for typing syntax.
- Pydantic models should have explicit field types.
- Use `Field(...)` when validation metadata is meaningful.

## Naming Conventions

- Functions/methods/variables: `snake_case`.
- Classes: `PascalCase`.
- Constants/type aliases: `UPPER_SNAKE_CASE`.
- Private helpers may use leading underscore.

## Error Handling Conventions

- Validate required inputs early and fail clearly.
- Catch specific exceptions before generic ones.
- Log with context before rethrowing where appropriate.
- Use explicit fallback paths only when behavior is intentional.
- Avoid silent exception swallowing.

## Logging Conventions

- Use logging, not print.
- Existing plugin centralizes logs via `Filter.log(...)` and `debug_mode` gating.
- Use levels intentionally:
  - `debug` internal details
  - `info` lifecycle milestones
  - `warning` recoverable anomalies
  - `error` failures

## Async / Concurrency Conventions

- Keep async flows `async`/`await` end-to-end.
- Bridge blocking work using safe adapters (for example `asyncio.to_thread`).
- Reuse existing detached async thread runner pattern only when needed.
- Do not introduce new concurrency models without strong reason.

## Data / DB Conventions

- Reuse existing SQLAlchemy context-manager patterns (`get_db_context`, `get_db`).
- Keep DB operations scoped and short-lived.

## Modification Boundaries

- Default editable scope:
  - root plugin files
  - root docs/config relevant to plugin development
  - `open_webui/` mock package when task requires
- Avoid editing `open-webui/` submodule unless explicitly requested.

## Testing Expectations for Changes

- If tests exist for touched area: run single test, then file, then broader suite as needed.
- If no tests exist, state that explicitly in final report.

## Agent Workflow Expectations

- Read relevant files before editing.
- Match existing patterns before introducing new ones.
- Keep diffs minimal and focused.
- Do not commit unless explicitly requested.
- Do not modify unrelated files.

## Notes

- Root tooling is intentionally lightweight for plugin-focused development.
