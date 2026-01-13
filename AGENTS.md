# Agent Guidelines

This repository contains an Open WebUI extension for automatic memory management.

## Build/Lint/Test Commands

This is a single-file Python extension with no build system. Commands to verify changes:

```bash
# Check Python syntax
python -m py_compile auto_memory.py

# Type checking (if mypy is available)
mypy auto_memory.py --ignore-missing-imports

# Linting (if ruff is available)
ruff check auto_memory.py

# Format checking (if black is available)
black --check auto_memory.py

# Run extension directly for testing
python auto_memory.py
```

No automated test suite exists. Manual testing involves:

1. Loading the extension in Open WebUI
2. Triggering conversations to test memory extraction
3. Verifying memory CRUD operations work correctly
4. Checking debug logs when `debug_mode` valve is enabled

## Code Style Guidelines

### Imports

- Standard library imports first, then third-party, then local (if any)
- Group alphabetically within each section
- Use `from typing import` for type hints

```python
import asyncio
import json
import logging
from datetime import datetime

from fastapi import HTTPException, Request
from openai import OpenAI
from pydantic import BaseModel, Field
```

### Type Hints

- Use type hints for all function parameters and return values
- Use `Optional[T]` for nullable types
- Use `Union[T1, T2, ...]` for multiple possible types
- Use `Literal["a", "b"]` for string enums
- Define `TypeVar` for generic types

```python
async def function(
    param1: str,
    param2: Optional[int] = None,
) -> dict[str, Any]:
    ...
```

### Naming Conventions

- Classes: `PascalCase`
- Functions and methods: `snake_case`
- Variables: `snake_case`
- Constants: `UPPER_SNAKE_CASE`
- Private methods: `_leading_underscore`

```python
class Filter:
    def process_memory(self):
        self._internal_helper()

MAX_RETRIES = 3
```

### Error Handling

- Raise `ValueError` for invalid arguments
- Raise `RuntimeError` for operational failures
- Use specific exception types when available
- Log errors with `self.log(..., level="error")` before raising

```python
if not emitter:
    raise ValueError("Emitter is required to emit status updates")

try:
    result = await operation()
except Exception as e:
    self.log(f"Operation failed: {e}", level="error")
    raise RuntimeError("Operation failed") from e
```

### Pydantic Models

- Use `Field()` for all model fields with descriptions
- Set validation constraints (`ge`, `le`, `min_length`, etc.)
- Use docstrings for model classes

```python
class Memory(BaseModel):
    """Single memory entry with metadata."""

    mem_id: str = Field(..., description="ID of the memory")
    clarity: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Memory clarity (0.0-1.0)",
    )
```

### Async/Await

- Mark all async functions with `async def`
- Use `await` for all coroutine calls
- Use `_run_detached()` helper for fire-and-forget async operations

### Logging

- Use `self.log(message, level="debug|info|warning|error")`
- Log at `debug` level for detailed diagnostics
- Log at `info` level for normal operations
- Log at `warning` level for recoverable issues
- Log at `error` level for failures

### Documentation

- Use module-level docstring for metadata (title, author, version, etc.)
- Use docstrings for all classes and public methods
- Include type hints in docstring descriptions
- Use inline comments sparingly (code should be self-documenting)

### Format

- No inline comments unless necessary
- Maximum line length: 100-120 characters (be reasonable)
- Use f-strings for string formatting: `f"variable={value}"`
- Use list comprehensions when appropriate
- Break long lines at logical boundaries

### Git Commit Messages

Follow conventional commit format:

- `fix: description` - bug fixes
- `feat: description` - new features
- Use present tense ("Add" not "Added")
- Capitalize subject line
- Provide detailed body explaining the change

```bash
fix: Correct decay calculation to prevent compound decay

The previous implementation was decaying from current clarity instead
of base clarity, causing memories to decay too quickly over time.
```

## Project Structure

```
open-webui-functions/
├── auto_memory.py    # Main extension file (Filter class)
└── __pycache__/      # Python cache (ignore)
```

## Key Patterns

1. **Filter Pattern**: The `Filter` class with `Valves` (admin config) and `UserValves` (user config)
2. **Inlet/Outlet Pattern**: `inlet()` processes incoming requests, `outlet()` processes responses
3. **Async Operations**: All I/O operations are async
4. **Memory Actions**: ADD/UPDATE/DELETE operations executed in order (delete → update → add)
5. **OpenAI SDK**: Uses structured outputs with fallback to JSON schema instruction
6. **Vector DB**: Direct access via `VECTOR_DB_CLIENT` for memory storage

## Notes

- This is an Open WebUI function, not a standalone application
- The extension runs within the Open WebUI FastAPI application
- No standalone tests - integration requires Open WebUI environment
- Debug mode (`valves.debug_mode = True`) provides detailed logging
