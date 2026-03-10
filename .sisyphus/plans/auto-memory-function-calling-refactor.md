# Auto Memory: Function Calling Refactor

## TL;DR
> **Summary**: Replace `auto_memory.py`’s “structured output / JSON parsing” memory-action planning with OpenAI tool calling that returns a single validated action plan; keep execution deterministic (delete → update → add) and hard-fail (no mutations) if the provider doesn’t support tool calling.
> **Deliverables**:
> - `auto_memory.py` refactor: tool-calling action-plan generation + prompt rewrite
> - ID-safe validation: update/delete limited to retrieved memory IDs
> - Minimal pytest coverage for tool-calling behavior (no root tests exist today)
> **Effort**: Medium
> **Parallel**: YES - 3 waves
> **Critical Path**: Tool schema + prompt → tool-call parsing + validation → execute actions → tests

## Context
### Original Request
- For `auto_memory.py`, refactor memory-handling logic using function calling.
- Modify prompt(s) to match the new processing logic.
- Check whether Open WebUI’s original memory-handling functions can be reused.

### Interview Summary
- Function calling style: model only generates an action plan (no direct side effects).
- Prompt language: English.
- Compatibility: do NOT keep a fallback path for endpoints that don’t support tool calling.

### Defaults Applied (no further user input)
- Require exactly one tool call named `memory_actions`. If zero or >1 tool calls, treat as failure (no mutations).
- If the provider returns tool calls plus assistant `content`, ignore content (log warning) and use tool args only.
- Max enum IDs in schema: use the retrieved IDs from `related_memories` (already capped by `valves.related_memories_n`, default 5). If ever larger than 50, truncate to first 50 IDs and log truncation.

### Metis Review (gaps addressed)
- Enforce: tool args validation + ID enum constraint + max iterations.
- Guardrail: if response has no tool calls / only text, hard fail and apply **no** memory actions.
- Add minimal tests: valid tool call, invalid ID rejection, missing tool_calls path.

## Work Objectives
### Core Objective
Refactor memory action planning in `auto_memory.py` to use OpenAI tool calling (function calling), producing a single validated action plan that is executed deterministically with existing Open WebUI memory router functions.

### Deliverables
- Tool-calling action-plan generation method integrated into `Filter.auto_memory(...)` in `auto_memory.py:1580`.
- New tool schema + prompt that reliably yields `actions: [add|update|delete]` with strict validation.
- Hard-fail behavior for non-tool-calling providers (no fallback).
- Unit tests proving: valid plan applies in order, invalid IDs blocked, no tool_calls triggers no mutation.

### Definition of Done (verifiable)
- `uv run python -m py_compile auto_memory.py`
- `uv run pytest -q`

### Must Have
- Deterministic execution order remains `delete → update → add` via `auto_memory.py:1646`.
- Update/delete IDs constrained to retrieved IDs from `auto_memory.py:1340` (no arbitrary memory IDs).
- Action list remains capped at 20 (matches `auto_memory.py:461`).
- Clear error path when tool calling not supported or response malformed: emit status error + no mutations.

### Must NOT Have
- MUST NOT let the model directly execute add/update/delete side effects (no tool loop with mutations).
- MUST NOT reintroduce “schema-instructions + JSON-in-content fallback” for action planning.
- MUST NOT change unrelated features (expiry/boost/cleanup, permission checks, inlet memory-context override) unless required for correctness.

## Verification Strategy
> ZERO HUMAN INTERVENTION — all verification is agent-executed.
- Test decision: tests-after (new minimal pytest suite).
- Evidence files required for each task: `.sisyphus/evidence/task-{N}-{slug}.*`

## Execution Strategy
### Parallel Execution Waves
Wave 1: Tool schema + prompt + parsing contract (foundation)
Wave 2: Integrate into `auto_memory()` + error handling + logging + status emission
Wave 3: Tests + smoke verification

### Dependency Matrix (high level)
- Tool schema/prompt (T1,T2) → integration (T3,T4) → tests (T5,T6)

### Agent Dispatch Summary
- Wave 1: 2 tasks (unspecified-high / writing)
- Wave 2: 2 tasks (deep / unspecified-high)
- Wave 3: 2 tasks (quick)

## TODOs

- [x] 1. Design tool-calling contract for memory actions

  **What to do**:
  - Make the action/request models strict about extra keys:
    - Introduce `StrictBaseModel(BaseModel)` with `model_config = ConfigDict(extra="forbid")`.
    - Change `MemoryAddAction`, `MemoryUpdateAction`, `MemoryDeleteAction`, and `MemoryActionRequestStub` to inherit from `StrictBaseModel`.
    - Update `build_actions_request_model(...)` (`auto_memory.py:483`) to use `__base__=StrictBaseModel` (not `BaseModel`) for all `create_model(...)` calls.
  - Define a single OpenAI tool named `memory_actions` whose arguments represent the full action plan.
  - Arguments must map 1:1 to existing action models in `auto_memory.py:442` (`MemoryAddAction`, `MemoryUpdateAction`, `MemoryDeleteAction`).
  - Enforce:
    - `actions` is an array with max length 20 (keep parity with `auto_memory.py:461`).
    - For update/delete actions, `id` must be restricted to the set of retrieved memory IDs from `get_related_memories()` (`auto_memory.py:1340`).
  - Decide and document: exactly one tool call per planning request (no multi-call plan).
  - **Decision-complete schema generation** (executor must follow exactly):
    - Build `existing_ids` from `related_memories` (`[m.mem_id for m in related_memories]`).
    - Apply ID truncation rule from Defaults Applied (cap to 50 IDs).
    - Reuse existing dynamic Pydantic model builder: `ActionsModel = build_actions_request_model(existing_ids)` from `auto_memory.py:483`.
    - Tool spec must be constructed per request (use only fields known-safe for `chat.completions` tool calling):
      - `tools = [{"type": "function", "function": {"name": "memory_actions", "description": "Return a memory action plan.", "parameters": ActionsModel.model_json_schema()}}]`
      - `tool_choice = {"type": "function", "function": {"name": "memory_actions"}}`
    - Validation must use the same `ActionsModel`:
      - `plan = ActionsModel.model_validate_json(tool_call.function.arguments)`

  **Must NOT do**:
  - Do not add tools that perform side effects (no `add_memory` / `delete_memory` as model tools).
  - Do not allow arbitrary IDs for update/delete.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — needs careful schema + validation design
  - Skills: []

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: 3 | Blocked By: none

  **References**:
  - Existing models: `auto_memory.py:442`
  - Existing ID restriction approach: `auto_memory.py:483`
  - Max actions cap: `auto_memory.py:461`
  - External docs: https://platform.openai.com/docs/guides/function-calling

  **Acceptance Criteria**:
  - [x] Tool schema (from Pydantic JSON Schema) sets `additionalProperties: false` (via `extra="forbid"`) and includes required fields.
  - [x] Update/delete `id` validation rejects unknown IDs before any mutation.
  - [x] Extra keys in tool args are rejected (not silently ignored).

  **QA Scenarios**:
  ```
  Scenario: Tool schema rejects extra fields
    Tool: Bash
    Steps: Run unit test that passes extra properties in tool args
    Expected: Validation fails; no actions executed
    Evidence: .sisyphus/evidence/task-1-schema-strict.txt

  Scenario: Update/delete IDs constrained
    Tool: Bash
    Steps: Run unit test with update action id not in retrieved IDs
    Expected: Validation fails; no actions executed
    Evidence: .sisyphus/evidence/task-1-id-enum.txt
  ```

  **Commit**: NO (unless user explicitly requests) | Message: `refactor(auto-memory): define tool schema for memory action plan` | Files: `auto_memory.py`


- [x] 2. Rewrite the memory prompt for tool-calling plan generation

  **What to do**:
  - Replace/augment `UNIFIED_SYSTEM_PROMPT` usage (`auto_memory.py:65`, `auto_memory.py:1623`) for the action-planning step.
  - New system prompt must:
    - Be English.
    - Preserve existing memory rules (what to extract / not extract; consolidation rules; honor explicit memory requests).
    - Add one new safety rule: never store credentials/secrets (passwords, API keys, tokens) even if the user asks.
    - Instruct: “Call tool `memory_actions` exactly once” and “Return empty `actions` when no changes needed”.
    - Explicitly prohibit returning normal text output.
  - Prompt rewrite decision: remove the large example block; keep 0–2 short examples max, and remove the confusing “(-2)” indexing language.
    - Replace with: “Focus on the latest user message (most recent message with role=user)”.
  - **Decision-complete input formatting** (executor must follow exactly):
    - Keep sending the same core inputs as today, but make them explicit:
      - `LATEST_USER_MESSAGE: ...` (extract from messages list)
      - `RECENT_CONVERSATION_SNIPPET:` (use `messages_to_string(...)`)
      - `RELATED_MEMORIES_JSON:` (the existing `stringified_memories` payload)
    - The tool-planning user message must be:
      - A single string with these three labeled sections in this order.

  **Must NOT do**:
  - Do not change the underlying semantics of ADD/UPDATE/DELETE.
  - Do not require multi-turn tool loops.

  **Recommended Agent Profile**:
  - Category: `writing` — prompt rewrite requires careful instruction design
  - Skills: []

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: 3 | Blocked By: none

  **References**:
  - Current prompt: `auto_memory.py:65`
  - Model call site: `auto_memory.py:1623`
  - External docs: https://platform.openai.com/docs/guides/function-calling

  **Acceptance Criteria**:
  - [x] Prompt explicitly forces a tool call and allows `actions: []`.
  - [x] Prompt uses “latest user message” wording (no numeric index references like “-2”).

  **QA Scenarios**:
  ```
  Scenario: No-op case produces empty actions
    Tool: Bash
    Steps: Run unit test with benign conversation + empty related memories
    Expected: Tool call args contain actions: []
    Evidence: .sisyphus/evidence/task-2-noop.txt

  Scenario: Explicit remember request produces ADD
    Tool: Bash
    Steps: Run unit test stub where last user msg says “remember this: …”
    Expected: Tool call args contain one add action
    Evidence: .sisyphus/evidence/task-2-remember.txt
  ```

  **Commit**: NO (unless user explicitly requests) | Message: `refactor(auto-memory): update prompt for tool-calling memory plan` | Files: `auto_memory.py`


- [x] 3. Implement tool-calling action-plan generation (no fallback)

  **What to do**:
  - Add a dedicated method (or extend `query_openai_sdk`) to request a tool call using `client.chat.completions.create` (current client is `OpenAI(...)` in `auto_memory.py:941`).
  - Call must:
    - Pass `tools=[...]` with the `memory_actions` schema.
    - Force tool usage with `tool_choice` pointing at `memory_actions` (see Task 1 exact structure).
    - Make exactly one request (no tool loop). Treat any of these as failure: provider rejects `tools` params (e.g., `BadRequestError`), zero tool calls, more than one tool call, tool name mismatch, invalid/empty JSON arguments, Pydantic validation errors.
  - Parse tool call arguments and validate with Pydantic models.
    - Required parsing shape (OpenAI chat.completions):
      - `choice = response.choices[0]`
      - `tool_calls = choice.message.tool_calls`
      - `tool_call = tool_calls[0]` (after enforcing exactly one)
      - `tool_call.function.name == "memory_actions"`
      - `args_json = tool_call.function.arguments` (string)
      - `plan = ActionsModel.model_validate_json(args_json)`
  - On failure (provider rejects tools, returns no tool_calls, invalid JSON args, validation error):
    - Log error
    - Emit status `error` (reuse `emit_status` in `auto_memory.py:419`)
    - Return without calling `apply_memory_actions`.

  **Must NOT do**:
  - Do not fall back to `chat.completions.parse` or “schema-instructions JSON” (`auto_memory.py:1063`).
  - Do not execute memory mutations on partial/invalid tool output.

  **Recommended Agent Profile**:
  - Category: `deep` — careful error handling and validation boundaries
  - Skills: []

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: 5,6 | Blocked By: 1,2

  **References**:
  - Existing OpenAI client wrapper: `auto_memory.py:894`
  - Existing structured-output fallback (to remove for this flow): `auto_memory.py:1063`
  - Action-apply step: `auto_memory.py:1646`
  - External docs: https://platform.openai.com/docs/guides/function-calling

  **Acceptance Criteria**:
  - [x] When provider returns a valid tool call, a validated `actions` list is produced.
  - [x] When provider does not support tools, the plugin emits an error and applies zero actions.

  **QA Scenarios**:
  ```
  Scenario: Provider returns tool_calls
    Tool: Bash
    Steps: Run unit test with mocked OpenAI response containing tool_calls
    Expected: Parsed actions match expected; apply_memory_actions called exactly once
    Evidence: .sisyphus/evidence/task-3-toolcalls.txt

  Scenario: Provider returns no tool_calls
    Tool: Bash
    Steps: Run unit test with mocked OpenAI response lacking tool_calls
    Expected: No apply_memory_actions call; status error emitted
    Evidence: .sisyphus/evidence/task-3-no-toolcalls.txt
  ```

  **Commit**: NO (unless user explicitly requests) | Message: `refactor(auto-memory): generate memory action plan via tool calling` | Files: `auto_memory.py`


- [x] 4. Wire tool-calling plan into `auto_memory()` and preserve existing lifecycle

  **What to do**:
  - In `auto_memory.py:1580`, keep current sequence:
    - `get_related_memories` (`auto_memory.py:1340`)
    - `boost_memories` (`auto_memory.py:1485`)
    - `cleanup_expired_memories` (`auto_memory.py:1407`)
    - build conversation string (`auto_memory.py:844`)
  - Replace only the “action plan generation” call site currently using `query_openai_sdk(... response_model=build_actions_request_model(...))` (`auto_memory.py:1623`).
  - Ensure `apply_memory_actions` remains the only mutation point, and only called if tool plan validated.
  - Keep permission checks and detached execution in `Filter.outlet` intact (`auto_memory.py:1771`).

  **Must NOT do**:
  - Do not change memory injection override logic (`auto_memory.py:1169`).
  - Do not change expiry schema/table behavior (`auto_memory.py:605`).

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — integration-focused refactor
  - Skills: []

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: 5,6 | Blocked By: 3

  **References**:
  - Core flow: `auto_memory.py:1580`
  - Apply actions: `auto_memory.py:1646`
  - Outlet trigger: `auto_memory.py:1771`

  **Acceptance Criteria**:
  - [x] On valid tool plan, actions are applied in the same order as before.
  - [x] On invalid tool plan, no memory router methods are called.
  - [x] When `actions: []`, do NOT call `apply_memory_actions`; log “no changes” and return.

  **QA Scenarios**:
  ```
  Scenario: Mixed actions order preserved
    Tool: Bash
    Steps: Run unit test that returns delete+update+add actions
    Expected: Calls happen delete → update → add
    Evidence: .sisyphus/evidence/task-4-order.txt

  Scenario: Empty actions produces “no changes”
    Tool: Bash
    Steps: Run unit test returning actions: []
    Expected: apply_memory_actions not called; no mutations; log shows “no changes”
    Evidence: .sisyphus/evidence/task-4-empty.txt
  ```

  **Commit**: NO (unless user explicitly requests) | Message: `refactor(auto-memory): integrate tool plan into auto_memory flow` | Files: `auto_memory.py`


- [x] 5. Add unit tests for tool-calling planner and mutation guardrails

  **What to do**:
  - Create a root-level pytest suite (none exists today): `tests/test_auto_memory_function_calling.py`.
  - Use pytest to monkeypatch/mock the OpenAI client so tests are offline and deterministic.
  - Add at least these tests:
    - Valid tool call with mixed actions triggers deterministic execution order.
    - Invalid update/delete ID is rejected; no mutations.
    - Provider response has no tool_calls: hard fail; no mutations.
    - Tool args contain extra keys: rejected by strict schema/validation; no mutations.

  **Must NOT do**:
  - Do not depend on `open-webui/` submodule tests.
  - Do not require network.

  **Recommended Agent Profile**:
  - Category: `quick` — small focused tests
  - Skills: []

  **Parallelization**: Can Parallel: YES | Wave 3 | Blocks: 6 | Blocked By: 4

  **References**:
  - No existing tests (repo fact): `pyproject.toml` only
  - Mock Open WebUI memory ops (for call interception): `open_webui/routers/memories.py:32`
  - apply order: `auto_memory.py:1646`

  **Acceptance Criteria**:
  - [x] `uv run pytest -q` passes.

  **QA Scenarios**:
  ```
  Scenario: Run unit tests
    Tool: Bash
    Steps: uv run pytest -q
    Expected: Exit code 0
    Evidence: .sisyphus/evidence/task-5-pytest.txt

  Scenario: Validate no-mutation on failures
    Tool: Bash
    Steps: Run tests that simulate invalid IDs / missing tool_calls
    Expected: No calls to add/update/delete mocks
    Evidence: .sisyphus/evidence/task-5-no-mutate.txt
  ```

  **Commit**: NO (unless user explicitly requests) | Message: `test(auto-memory): cover tool-calling memory action planning` | Files: `tests/test_auto_memory_function_calling.py`


- [x] 6. Verification sweep + reuse check against upstream Open WebUI memory tooling

  **What to do**:
  - Verify compilation and tests:
    - `uv run python -m py_compile auto_memory.py`
    - `uv run pytest -q`
  - “Reuse check” (documented in code comments / plan notes):
    - Confirm we are reusing Open WebUI memory router functions (`open_webui.routers.memories`) for actual mutations (already true).
    - Optionally compare upstream builtin memory tools for naming/semantics alignment (reference only): `open-webui/backend/open_webui/tools/builtin.py:523` and `open-webui/backend/open_webui/utils/tools.py:415`.
  - Ensure error messaging is clear for non-tool-calling providers.

  **Must NOT do**:
  - Do not introduce runtime dependency on `open-webui/` submodule.

  **Recommended Agent Profile**:
  - Category: `quick`
  - Skills: []

  **Parallelization**: Can Parallel: NO | Wave 3 | Blocks: none | Blocked By: 5

  **References**:
  - Router functions used by plugin: `auto_memory.py:49`
  - Upstream builtin memory tools (reference): `open-webui/backend/open_webui/tools/builtin.py:523`
  - Upstream tool registry: `open-webui/backend/open_webui/utils/tools.py:415`

  **Acceptance Criteria**:
  - [x] `uv run python -m py_compile auto_memory.py` succeeds.
  - [x] `uv run pytest -q` succeeds.

  **QA Scenarios**:
  ```
  Scenario: Compile check
    Tool: Bash
    Steps: uv run python -m py_compile auto_memory.py
    Expected: Exit code 0
    Evidence: .sisyphus/evidence/task-6-pycompile.txt

  Scenario: Full verification
    Tool: Bash
    Steps: uv run pytest -q
    Expected: Exit code 0
    Evidence: .sisyphus/evidence/task-6-verify.txt
  ```

  **Commit**: NO


## Final Verification Wave (4 parallel agents, ALL must APPROVE)
- [x] F1. Plan Compliance Audit — oracle
- [x] F2. Code Quality Review — unspecified-high
- [x] F3. Real Manual QA (simulated) — unspecified-high
- [x] F4. Scope Fidelity Check — deep

## Commit Strategy
- Default: do not create commits (unless user explicitly requests).
- If user requests commits: prefer 3–5 atomic commits aligned to tasks 1–5.

## Success Criteria
- Tool-calling memory planning works end-to-end in `Filter.auto_memory` without changing existing memory semantics.
- Invalid tool output and tool-calling-unsupported providers cause a clear error and **no memory mutations**.
- Tests demonstrate correctness and guardrails.
