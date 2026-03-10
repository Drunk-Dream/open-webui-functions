# Open WebUI Functions

Custom plugins/functions for [Open WebUI](https://github.com/open-webui/open-webui).

## Plugins

### Auto Memory (v1.4.7)

Automatically identify and store valuable information from chats as Memories.

**Features:**
- Automatic memory extraction from conversations
- LLM-powered memory management (add/update/delete)
- Memory expiry and decay system
- Per-user configuration and permissions
- Function calling integration

**Requirements:** Open WebUI >= 0.8.1

**Credits:** Forked from [@nokodo](https://nokodo.net/github/open-webui-extensions)

### Auto Chat Cleanup (v1.0.0)

Automatically clean up old chats based on idle time and retention count rules.

**Features:**
- Dual cleanup rules: max idle days OR max retained chats (OR semantics)
- Protected chat support (skip folder/archived/pinned chats)
- Per-user configuration via UserValves
- Status notifications for cleanup operations
- Debug mode for detailed logging

**Requirements:** Open WebUI >= 0.8.1

## Installation

1. Copy the plugin file (e.g., `auto_memory.py` or `auto_chat_cleanup.py`) to your Open WebUI functions directory
2. Restart Open WebUI or reload functions
3. Configure plugin settings in Open WebUI admin panel

## Development

### Setup

```bash
# Install dependencies
uv sync --dev

# Verify setup
uv run python --version
uv run pytest --version
```

### Testing

```bash
# Run all tests (excluding submodule)
uv run pytest -q --ignore=open-webui

# Run specific test file
uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py

# Run single test
uv run pytest -q --ignore=open-webui tests/test_auto_chat_cleanup.py::test_name

# Stop on first failure
uv run pytest -x --ignore=open-webui
```

### Code Style

- Python >= 3.12
- Modern type hints: `dict[str, int]`, `X | None`, `list[str]`
- Pydantic for configuration models
- pytest + pytest-asyncio for testing
- See [AGENTS.md](AGENTS.md) for detailed guidelines

## Project Structure

```
.
├── auto_memory.py              # Memory management plugin
├── auto_chat_cleanup.py        # Chat cleanup plugin
├── tests/
│   ├── test_auto_memory_function_calling.py
│   ├── test_auto_chat_cleanup.py
│   └── conftest.py
├── open_webui/                 # Mock package for testing
│   ├── internal/
│   ├── models/
│   ├── routers/
│   └── utils/
├── open-webui/                 # Upstream submodule (reference only)
├── AGENTS.md                   # Development guidelines
└── README.md
```

## Contributing

1. Read [AGENTS.md](AGENTS.md) for development guidelines
2. Follow existing code patterns and style
3. Add tests for new features
4. Ensure all tests pass before submitting

## License

- **Auto Memory**: See `auto_memory.md` for licensing terms
- **Auto Chat Cleanup**: MIT License

## Author

[@Drunk-Dream](https://github.com/Drunk-Dream)
- Email: dongmh3@outlook.com
- Repository: https://github.com/Drunk-Dream/open-webui-functions

## Credits

- **Auto Memory** forked from [@nokodo](https://nokodo.net/github/open-webui-extensions)
- Built for [Open WebUI](https://github.com/open-webui/open-webui)
