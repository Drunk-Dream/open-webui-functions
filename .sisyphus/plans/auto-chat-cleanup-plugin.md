# Auto Chat Cleanup Plugin Implementation

## TL;DR
> **Summary**: 直接开发一个新的根目录单文件 Filter 插件，在每次对话 `outlet` 完成后，按当前用户维度自动清理旧对话。清理规则采用“最大闲置时间”与“最大保留数量”并行生效的 OR 语义，并始终跳过受保护对话。
> **Deliverables**:
> - `auto_chat_cleanup.py`
> - `tests/test_auto_chat_cleanup.py`
> - 基于系统删除接口的对话清理逻辑
> - `emit_status()` 风格的删除数量提示
> **Effort**: Medium
> **Parallel**: YES - 2 waves
> **Critical Path**: Task 1 → Task 2 → Task 3 → Task 4 → Task 5 → Task 6

## Context
### Original Request
用户要新增一个自动清理对话的插件，可按最后活跃时间、最大保留数量等配置控制可保留对话数量；可配置跳过在文件夹中的对话、已归档对话、置顶对话等类似受保护对话；优先使用系统默认接口删除对话以避免副作用；若实际发生删除，则参考 `auto_memory.py` 的 `emit_status()` 提示删除数量。该计划用于**直接开发插件**，不是仅做设计。

### Interview Summary
- 插件采用仓库现有根目录单文件 Python Filter 模式，参考 `auto_memory.py`
- 自动清理仅在 `outlet` 执行，不引入定时任务或 `inlet` 双触发
- 清理范围仅限当前触发用户自己的对话
- 删除判定为 OR：超过最大闲置时间 **或** 超过最大保留数量，任一命中即候删
- 默认跳过受保护对话：`folder_id` 非空、`archived=1`、`pinned=1`
- 若实际删除数量大于 0，则参考 `emit_status()` 发出一次状态提示；无删除时不提示
- 测试策略采用 tests-after，使用现有 `pytest + pytest-asyncio`

### Metis Review (gaps addressed)
- 过滤顺序固定：仅当前用户 → `updated_at desc` → 排除当前活跃对话 → 排除受保护对话 → 年龄候选 → 数量候选 → 并集去重 → 调用删除接口
- 失败策略固定：单条删除失败只记录日志并继续后续候选
- 保留数量固定按“可删除池”计算，不把受保护对话与当前活跃对话纳入超量窗口
- `updated_at` 缺失或非法时保守跳过并记录 warning

## Work Objectives
### Core Objective
直接实现“当前用户级对话自动清理”插件，并补齐最小但完整的自动化测试，使执行代理无需再做产品判断即可完成落地。

### Deliverables
- 新插件文件：`auto_chat_cleanup.py`
- 自动化测试文件：`tests/test_auto_chat_cleanup.py`
- `Valves` / `UserValves` 配置实现
- 候删筛选、删除执行、状态提示与日志实现
- 聚焦测试与根测试验证结果

### Definition of Done (verifiable conditions with commands)
- [ ] `uv run python -m py_compile auto_chat_cleanup.py tests/test_auto_chat_cleanup.py` 退出码为 0
- [ ] `uv run pytest -q --ignore=open-webui tests/test_auto_chat_cleanup.py` 全部通过
- [ ] `uv run pytest -q --ignore=open-webui` 不因本插件引入新的根测试失败
- [ ] 插件实际调用 `Chats.delete_chat_by_id_and_user_id(...)`，而不是 direct SQL delete
- [ ] 插件仅在 `outlet` 执行，且不会删除当前活跃对话、受保护对话或其他用户对话
- [ ] 实际删除数量大于 0 时，插件通过 `emit_status()` 风格事件提示删除数量；无删除时不提示

### Must Have
- 根目录单文件 Filter 插件实现
- `outlet` 自动触发实现
- 当前用户作用域实现
- 基于 `updated_at` 的闲置时长规则实现
- 基于 `updated_at desc` 的最大保留数量规则实现
- 受保护对话过滤实现
- 调用系统默认删除接口的实现
- `emit_status()` 风格的删除数量提示实现
- pytest 自动化验证实现

### Must NOT Have (guardrails, AI slop patterns, scope boundaries)
- 不设计定时任务、后台调度器、cron、celery 类能力
- 不设计前端配置页面或管理面板
- 不设计管理员全局清理或跨用户清理
- 不直接写 SQL 删除 `chat` / `chat_message`
- 不修改 `open-webui/` 子模块源码
- 不把标签、标题模糊匹配、消息数、token 数等扩展规则纳入 v1
- 不把受保护对话计入“可删除池”的保留数量窗口

## Verification Strategy
> ZERO HUMAN INTERVENTION — all verification is agent-executed.
- Test decision: tests-after + `pytest` / `pytest-asyncio`
- QA policy: Every task has executable commands
- Evidence: `.sisyphus/evidence/task-{N}-auto-chat-cleanup.{ext}`

## Execution Strategy
### Parallel Execution Waves
Wave 1: 任务 1-3（测试骨架、配置与候选逻辑、插件主体）
Wave 2: 任务 4-6（状态提示与失败处理、验证收敛、收尾）

### Dependency Matrix (full, all tasks)
| Task | Depends On | Blocks |
|---|---|---|
| 1 | None | 2, 3, 4, 5 |
| 2 | 1 | 3, 4, 5 |
| 3 | 1, 2 | 4, 5 |
| 4 | 2, 3 | 5, 6 |
| 5 | 3, 4 | 6 |
| 6 | 4, 5 | Final Verification |

### Agent Dispatch Summary (wave → task count → categories)
- Wave 1 → 3 tasks → `unspecified-high`
- Wave 2 → 3 tasks → `unspecified-high` / `quick`

## TODOs
> Implementation + Test = ONE task. Never separate.
> EVERY task MUST have: Agent Profile + Parallelization + QA Scenarios.

- [ ] 1. 创建测试骨架并锁定行为语义

  **What to do**: 新建 `tests/test_auto_chat_cleanup.py`，先写失败测试来锁定插件行为：仅 `outlet` 触发、仅当前用户范围、受保护对话永不删除、当前活跃对话永不删除、年龄/数量规则为 OR、`updated_at` 异常时跳过、无候选时不删除。沿用 `tests/conftest.py` 与 `tests/test_auto_memory_function_calling.py` 的 fixture/patch 风格。
  **Must NOT do**: 不接真实数据库，不碰 `open-webui/` 子模块，不先写插件后补测试。

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: 先锁语义可避免后续误删行为
  - Skills: [] — 无需额外技能
  - Omitted: [`playwright`] — 全部用 pytest/mock 完成

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: 2, 3, 4, 5 | Blocked By: None

  **References**:
  - Test: `tests/conftest.py:1-10` — 测试导入路径配置
  - Test: `tests/test_auto_memory_function_calling.py:37-72` — fixture 结构
  - Test: `tests/test_auto_memory_function_calling.py:131-203` — 异步测试与 patch 风格
  - API/Type: `open-webui/backend/open_webui/models/chats.py:37-53` — `Chat` 字段定义

  **Acceptance Criteria**:
  - [ ] `tests/test_auto_chat_cleanup.py` 已创建并包含核心 fixture
  - [ ] 至少包含年龄规则、数量规则、保护规则、活跃对话保护、无候选 no-op 的失败测试
  - [ ] 测试使用固定 chat id，如 `chat-active`、`chat-folder`、`chat-archived`、`chat-pinned`、`chat-old-1`

  **QA Scenarios**:
  ```
  Scenario: Focused test file exists and imports cleanly
    Tool: Bash
    Steps: uv run python -m py_compile tests/test_auto_chat_cleanup.py
    Expected: Exit code 0
    Evidence: .sisyphus/evidence/task-1-auto-chat-cleanup.txt

  Scenario: Initial behavior tests run
    Tool: Bash
    Steps: uv run pytest -q --ignore=open-webui tests/test_auto_chat_cleanup.py
    Expected: Tests execute; initial failure state is acceptable before implementation, but file collection must succeed
    Evidence: .sisyphus/evidence/task-1-auto-chat-cleanup-collection.txt
  ```

  **Commit**: YES | Message: `test(auto-chat-cleanup): lock cleanup selection semantics` | Files: `tests/test_auto_chat_cleanup.py`

- [ ] 2. 实现配置模型与候删筛选逻辑

  **What to do**: 在 `auto_chat_cleanup.py` 中实现 `Filter`、`Valves`、`UserValves`、`log()` 以及纯筛选逻辑。配置至少包含：`enabled`、`show_status`、`max_idle_days`、`max_retained_chats`、`skip_folder_chats`、`skip_archived_chats`、`skip_pinned_chats`、`debug_mode`、可选 `min_cleanup_interval_seconds`。禁用语义固定为 `None` 或 `0` 表示该规则关闭。筛选顺序固定为：当前用户 chats → `updated_at desc` + `id` 升序稳定次排序 → 排除当前活跃对话 → 排除受保护对话 → 年龄候选 → 数量候选 → 并集去重。
  **Must NOT do**: 不直接删除任何 chat，不在此任务里实现 `outlet` 删除流程，不引入额外保护规则。

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: 这是误删风险最高的核心业务逻辑
  - Skills: [] — 无需额外技能
  - Omitted: [`frontend-ui-ux`] — 无前端内容

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: 3, 4, 5 | Blocked By: 1

  **References**:
  - Pattern: `auto_memory.py:643-760` — `Filter` / `Valves` / `UserValves` / `log()` 结构
  - API/Type: `open-webui/backend/open_webui/models/chats.py:45-53` — `updated_at` / `archived` / `pinned` / `folder_id`
  - Pattern: `database_structure.txt:183-200` — `chat` 表字段说明

  **Acceptance Criteria**:
  - [ ] `auto_chat_cleanup.py` 定义了 `Filter`、`Valves`、`UserValves`
  - [ ] 筛选逻辑通过 Task 1 中的选择语义测试
  - [ ] `updated_at` 非法值会被 warning 并跳过，不会进入候删列表

  **QA Scenarios**:
  ```
  Scenario: Plugin file compiles
    Tool: Bash
    Steps: uv run python -m py_compile auto_chat_cleanup.py
    Expected: Exit code 0
    Evidence: .sisyphus/evidence/task-2-auto-chat-cleanup.txt

  Scenario: Selection semantics tests pass
    Tool: Bash
    Steps: uv run pytest -q --ignore=open-webui tests/test_auto_chat_cleanup.py -k "idle or retained or protected or active or no_candidate"
    Expected: Exit code 0 with matching tests passed
    Evidence: .sisyphus/evidence/task-2-auto-chat-cleanup-selection.txt
  ```

  **Commit**: YES | Message: `feat(auto-chat-cleanup): add selection and config logic` | Files: `auto_chat_cleanup.py`, `tests/test_auto_chat_cleanup.py`

- [ ] 3. 实现 outlet 删除流程与系统接口集成

  **What to do**: 在 `async outlet(...)` 中实现当前用户上下文解析、当前 chat id 识别、可选冷却时间短路、查询当前用户 chats、调用筛选逻辑、逐条调用 `Chats.delete_chat_by_id_and_user_id(id, user.id, db=db)` 删除。删除失败时只记录并继续，不中断整个 `outlet`；插件必须返回原始 `body`。
  **Must NOT do**: 不通过 HTTP 调路由，不 direct SQL delete，不删除其他用户对话，不删除当前活跃对话。

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: 涉及真实副作用路径和安全边界
  - Skills: [] — 无需额外技能
  - Omitted: [`dev-browser`] — 非浏览器任务

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: 4, 5 | Blocked By: 1, 2

  **References**:
  - Pattern: `auto_memory.py:1854-1926` — `outlet` 生命周期模式
  - API/Type: `open-webui/backend/open_webui/models/chats.py:1528-1538` — 删除接口
  - API/Type: `open-webui/backend/open_webui/routers/chats.py:1137-1147` — 上游按用户删除的参考流程

  **Acceptance Criteria**:
  - [ ] `outlet` 是唯一自动清理入口
  - [ ] 删除路径只调用 `Chats.delete_chat_by_id_and_user_id(...)`
  - [ ] 删除失败不会阻断后续候选，也不会破坏原始 `body` 返回

  **QA Scenarios**:
  ```
  Scenario: Deletion API integration tests pass
    Tool: Bash
    Steps: uv run pytest -q --ignore=open-webui tests/test_auto_chat_cleanup.py -k "delete and outlet and current_user"
    Expected: Exit code 0 with integration-style mock tests passed
    Evidence: .sisyphus/evidence/task-3-auto-chat-cleanup.txt

  Scenario: No direct SQL delete introduced
    Tool: Bash
    Steps: uv run python - <<'PY'
from pathlib import Path
text = Path('auto_chat_cleanup.py').read_text(encoding='utf-8')
assert '.delete()' not in text or 'Chats.delete_chat_by_id_and_user_id' in text
print('ok')
PY
    Expected: Exit code 0 and prints ok
    Evidence: .sisyphus/evidence/task-3-auto-chat-cleanup-safety.txt
  ```

  **Commit**: YES | Message: `feat(auto-chat-cleanup): add outlet-based chat cleanup plugin` | Files: `auto_chat_cleanup.py`, `tests/test_auto_chat_cleanup.py`

- [ ] 4. 实现状态提示、日志与失败处理

  **What to do**: 参考 `auto_memory.py` 的 `emit_status()` 与 `show_status` 模式，实现删除数量提示：仅当删除数量 `> 0` 且 `show_status` 启用时，向 `__event_emitter__` 发出一次完成状态，例如“删除3个对话”；无删除时不提示。日志层级固定为：`info` 记录开始/结束与汇总，`debug` 记录逐条跳过理由，`warning` 记录 `updated_at` 异常与 delete 返回 False，`error` 记录 delete 抛异常。
  **Must NOT do**: 不在无删除时发出噪音提示，不向会话正文插入消息，不做阻塞性重试。

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: 这里直接决定用户可见反馈和运行时稳定性
  - Skills: [] — 无需额外技能
  - Omitted: [`playwright`] — 用 mock 验证事件发射即可

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: 5, 6 | Blocked By: 2, 3

  **References**:
  - Pattern: `auto_memory.py:273-290` — `emit_status()` 事件格式
  - Pattern: `auto_memory.py:1768-1776` — `show_status` 启用时发状态提示
  - Pattern: `auto_memory.py:753-760` — 日志模式

  **Acceptance Criteria**:
  - [ ] 删除成功且数量 > 0 时发送一次状态提示
  - [ ] 无删除时不发送状态提示
  - [ ] 单条删除失败只记日志并继续

  **QA Scenarios**:
  ```
  Scenario: Status emission tests pass
    Tool: Bash
    Steps: uv run pytest -q --ignore=open-webui tests/test_auto_chat_cleanup.py -k "emit_status or show_status"
    Expected: Exit code 0 with status-related tests passed
    Evidence: .sisyphus/evidence/task-4-auto-chat-cleanup.txt

  Scenario: Failure-handling tests pass
    Tool: Bash
    Steps: uv run pytest -q --ignore=open-webui tests/test_auto_chat_cleanup.py -k "failure or exception or continue"
    Expected: Exit code 0 with failure-handling tests passed
    Evidence: .sisyphus/evidence/task-4-auto-chat-cleanup-safety.txt
  ```

  **Commit**: YES | Message: `test(auto-chat-cleanup): cover failure handling and status emission` | Files: `auto_chat_cleanup.py`, `tests/test_auto_chat_cleanup.py`

- [ ] 5. 完成聚焦验证与根测试验证

  **What to do**: 运行语法检查、聚焦测试、根测试，并在失败时只修复与当前插件直接相关的问题。验证命令顺序固定为：`py_compile` → `tests/test_auto_chat_cleanup.py` → 根测试 `uv run pytest -q --ignore=open-webui`。
  **Must NOT do**: 不顺手修 unrelated 测试，不跑子模块测试，不扩展功能范围。

  **Recommended Agent Profile**:
  - Category: `quick` — Reason: 以执行验证和小范围修复为主
  - Skills: [] — 无需额外技能
  - Omitted: [`git-master`] — 非 git 任务

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: 6 | Blocked By: 3, 4

  **References**:
  - Guidance: `AGENTS.md:51-66` — 标准测试命令
  - Test: `tests/test_auto_memory_function_calling.py` — 根测试现有模式参考

  **Acceptance Criteria**:
  - [ ] `uv run python -m py_compile auto_chat_cleanup.py tests/test_auto_chat_cleanup.py` 通过
  - [ ] `uv run pytest -q --ignore=open-webui tests/test_auto_chat_cleanup.py` 通过
  - [ ] `uv run pytest -q --ignore=open-webui` 通过

  **QA Scenarios**:
  ```
  Scenario: Syntax and focused tests pass
    Tool: Bash
    Steps: uv run python -m py_compile auto_chat_cleanup.py tests/test_auto_chat_cleanup.py && uv run pytest -q --ignore=open-webui tests/test_auto_chat_cleanup.py
    Expected: Exit code 0
    Evidence: .sisyphus/evidence/task-5-auto-chat-cleanup.txt

  Scenario: Root test suite passes
    Tool: Bash
    Steps: uv run pytest -q --ignore=open-webui
    Expected: Exit code 0
    Evidence: .sisyphus/evidence/task-5-auto-chat-cleanup-root.txt
  ```

  **Commit**: NO | Message: `n/a` | Files: `auto_chat_cleanup.py`, `tests/test_auto_chat_cleanup.py`

- [ ] 6. 收尾、文档对齐与交付边界确认

  **What to do**: 确认只改动 `auto_chat_cleanup.py`、`tests/test_auto_chat_cleanup.py` 和必要的 `.sisyphus/evidence/`；回顾计划约束是否全部满足；准备最终交付说明，列出验证命令与结果。若需要提交，则遵循前述 commit strategy；若未被要求提交，则保持未提交状态。
  **Must NOT do**: 不改动 `open-webui/` 子模块，不顺手修改无关计划文件，不创建额外文档页。

  **Recommended Agent Profile**:
  - Category: `quick` — Reason: 以范围确认和交付收束为主
  - Skills: [] — 无需额外技能
  - Omitted: [`writing`] — 无额外文档产出需求

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: Final Verification | Blocked By: 4, 5

  **References**:
  - Guidance: `AGENTS.md:7-18` — 默认可编辑范围与子模块边界
  - Guidance: `AGENTS.md:51-66` — 验证命令要求

  **Acceptance Criteria**:
  - [ ] 最终改动范围与计划一致
  - [ ] 最终说明包含已运行命令与结果
  - [ ] 未请求提交时不创建 git commit

  **QA Scenarios**:
  ```
  Scenario: Verify changed files stay in scope
    Tool: Bash
    Steps: git status --short
    Expected: Only expected plugin/test/.sisyphus files are changed for this task
    Evidence: .sisyphus/evidence/task-6-auto-chat-cleanup.txt

  Scenario: Verify no submodule edits
    Tool: Bash
    Steps: uv run python - <<'PY'
import subprocess
out = subprocess.check_output(['git', 'status', '--short'], text=True)
assert ' open-webui/' not in out
print('ok')
PY
    Expected: Exit code 0 and prints ok
    Evidence: .sisyphus/evidence/task-6-auto-chat-cleanup-scope.txt
  ```

  **Commit**: NO | Message: `n/a` | Files: `auto_chat_cleanup.py`, `tests/test_auto_chat_cleanup.py`

## Final Verification Wave (4 parallel agents, ALL must APPROVE)
- [ ] F1. Plan Compliance Audit — oracle
- [ ] F2. Code Quality Review — unspecified-high
- [ ] F3. Automated QA Review — unspecified-high
- [ ] F4. Scope Fidelity Check — deep

## Commit Strategy
- Commit 1: `test(auto-chat-cleanup): lock cleanup selection semantics`
- Commit 2: `feat(auto-chat-cleanup): add outlet-based chat cleanup plugin`
- Commit 3: `test(auto-chat-cleanup): cover failure handling and status emission`

## Success Criteria
- 插件在 `outlet` 中按既定规则自动清理当前用户旧对话
- 当前活跃对话、受保护对话、其他用户对话都不会被误删
- 删除路径完全复用系统接口，避免 direct SQL delete 副作用
- 实际删除发生时，用户可见一次删除数量提示；无删除时无额外噪音
- 所有关键行为均由 pytest 用固定 fixture 与 mock 精确验证
