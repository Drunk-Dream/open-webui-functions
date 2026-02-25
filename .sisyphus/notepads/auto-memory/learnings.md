
## Upstream Memory Tooling Semantics (builtin.py + routers/memories.py)

### Operation naming (builtin.py lines 527–710)

| Builtin function name    | Router helper called          | Semantic                                      |
|--------------------------|-------------------------------|-----------------------------------------------|
| `search_memories`        | `query_memory`                | Vector similarity search; returns id+date+content |
| `add_memory`             | `_add_memory` (router POST /add) | Insert new memory; embeds content; returns `{status, id}` |
| `replace_memory_content` | `update_memory_by_id`         | Full content replacement (no partial patch); re-embeds; returns `{status, id, content}` |
| `delete_memory`          | `Memories.delete_memory_by_id_and_user_id` + `VECTOR_DB_CLIENT.delete` | Hard delete from SQL + vector store |
| `list_memories`          | `Memories.get_memories_by_user_id` | Full list, no vector search; returns id+content+created_at+updated_at |

### Key semantic details

- **add**: `AddMemoryForm(content=str)` → inserts SQL row, upserts vector with `metadata.created_at`. Returns `MemoryModel` (has `.id`).
- **update** (`replace_memory_content`): `MemoryUpdateModel(content: Optional[str])` — content is optional in the model but the builtin always passes it. Router re-embeds and upserts vector with both `created_at` and `updated_at` in metadata. Scoped to `user_id` (ownership enforced).
- **delete**: Builtin calls `Memories.delete_memory_by_id_and_user_id` directly (bypasses router function), then calls `VECTOR_DB_CLIENT.delete`. The router's `delete_memory_by_id` endpoint does the same two steps. Both paths are equivalent.
- **search**: Uses `QueryMemoryForm(content=str, k=int)`. Returns vector search results; builtin extracts `ids[0]`, `documents[0]`, `metadatas[0]` from the result object.

### Tool registry (utils/tools.py lines 474–484)

Memory builtins are injected only when **both** conditions hold:
```python
if is_builtin_tool_enabled("memory") and features.get("memory"):
    builtin_functions.extend([search_memories, add_memory, replace_memory_content, delete_memory, list_memories])
```
Tool IDs are registered as `builtin:add_memory`, `builtin:replace_memory_content`, etc. (`tool_id = f"builtin:{func.__name__}"`, line 565).

### Compatibility concerns for plugin-level action plan approach

1. **`delete_memory` in builtin bypasses the router function** — it calls the model layer directly. The router's `delete_memory_by_id` endpoint is a separate async function not imported by builtin.py. Plugin code should prefer calling the router-level helpers (`add_memory`, `update_memory_by_id`) for consistency, but for delete must replicate the two-step pattern (SQL delete + vector delete) or call the router endpoint via HTTP.
2. **No partial-update semantic** — `replace_memory_content` is a full replacement. There is no patch/merge operation in the upstream API. Plugin action plans that want to "update a field" must read-then-replace.
3. **Embedding is always synchronous within the request** — `add_memory` and `update_memory_by_id` both `await EMBEDDING_FUNCTION(...)` inline. Plugin mutations that call these helpers will block until embedding completes. This is intentional (see router comments about not holding DB sessions during embedding).
4. **Permission check is inside router helpers** — `add_memory` and `update_memory_by_id` check `has_permission(user.id, "features.memories", ...)` and `ENABLE_MEMORIES`. Plugin calling these helpers directly (not via HTTP) will trigger those checks, which requires a valid `Request` with `app.state.config` populated.

### Conclusion: plugin should continue routing through open_webui.routers.memories helpers

Yes — confirmed. The router helpers (`add_memory`, `update_memory_by_id`, `query_memory`) are the canonical mutation path used by upstream builtins. They handle embedding, vector upsert, permission checks, and SQL atomicity in one place. The plugin should import and call these same helpers rather than touching `Memories.*` or `VECTOR_DB_CLIENT` directly, with the one exception that delete currently bypasses the router function in builtins — plugin should be aware of this and either replicate the two-step delete or call the router's `delete_memory_by_id` function directly.

### File+line references
- `open-webui/backend/open_webui/tools/builtin.py` lines 26–33 (imports), 527–710 (all 5 memory tool functions)
- `open-webui/backend/open_webui/utils/tools.py` lines 54–64 (imports), 474–484 (registry injection), 565 (tool_id format)
- `open-webui/backend/open_webui/routers/memories.py` lines 54–59 (forms), 63–102 (add_memory), 116–151 (query_memory), 258–306 (update_memory_by_id), 315–343 (delete_memory_by_id)
