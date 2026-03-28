# Auto Memory 全文件重构计划

## TL;DR
> **Summary**: 在保持 `auto_memory.py` 单脚本文件原则的前提下，执行一次严格的“行为不变型重构”，通过先冻结契约、再按内部职责分层整理，降低 `Filter` 内部耦合并清理冗余实现。
> **Deliverables**:
> - `auto_memory.py` 单文件内完成结构重组与冗余清理
> - `tests/test_auto_memory_function_calling.py` 补强并锁定关键外部行为
> - 保留现有对 Open WebUI router、Valves/UserValves、tool schema、状态事件与 inlet/outlet 语义的兼容性
> **Effort**: Large
> **Parallel**: YES - 4 waves
> **Critical Path**: 1 → 2 → 4 → 7 → 9 → F1-F4

## Context
### Original Request
- 重构 `auto_memory.py` 整个文件
- 优化脚本架构
- 清除冗余代码
- 保持单一脚本文件原则

### Interview Summary
- 目标优先级：**行为完全不变，优先可维护性**
- 测试策略：**测试后补**（先补/锁关键回归，再做内部重构）
- 允许改动范围：**`auto_memory.py` + `tests/test_auto_memory_function_calling.py`**
- 明确排除：多文件拆分、接口重命名、顺手功能改造、子模块 `open-webui/` 改动

### Metis Review (gaps addressed)
- 将本次工作限定为**行为保持型重构**，不是架构重设计
- 明确保留导入时初始化、副作用顺序、tool-calling 解析语义、`apply_memory_actions()` 的部分失败容忍行为
- 将重构拆为“冻结契约 → seam-by-seam 重构 → 每步验证”的原子梯子
- 在计划中加入对空 tool_calls、非法 ID、额外 schema 字段、blank 内容、boost/cleanup 边界、inlet/outlet 短路条件的专项验证

## Work Objectives
### Core Objective
在不改变 `auto_memory.py` 对外行为、接口、配置语义与运行时副作用的前提下，重构其内部组织结构，形成更清晰的单文件分层：常量/模型、OpenAI 规划、memory action 执行、生命周期管理、hook 编排。

### Deliverables
- 将 `auto_memory.py` 重组为可辨识的单文件内部层次，减少 `Filter` 方法间隐式耦合
- 去除重复逻辑与散落状态处理，统一内部 helper 责任边界
- 为关键不可变行为补齐回归测试，确保重构期间无语义漂移
- 输出可独立通过的原子提交序列

### Definition of Done (verifiable conditions with commands)
- `uv run python -m py_compile auto_memory.py tests/test_auto_memory_function_calling.py`
- `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py`
- `uv run pytest -q --ignore=open-webui`
- 上述命令全部成功，且既有锁定行为未回归

### Must Have
- 保持单文件实现，不新增模块
- 保持 `Filter.inlet()` / `Filter.outlet()` 外部调用方式与语义不变
- 保持 `Valves` / `UserValves` 字段名、默认值语义、权限覆盖规则不变
- 保持 `ACTION_ORDER` 为 `delete -> update -> add`
- 保持 `build_memory_action_tools()` 的严格 schema 与 ID 限制行为不变
- 保持 `emit_status()` payload 结构不变
- 保持 memory 变更仍通过 `add_memory` / `update_memory_by_id` / `delete_memory_by_id` 路由函数完成
- 保持导入时表初始化与 lifecycle 列补齐副作用不变

### Must NOT Have (guardrails, AI slop patterns, scope boundaries)
- 不拆分成多文件/多包
- 不重命名公开配置字段、公开模型字段或 hook 方法签名
- 不改变异常路径、no-op 路径、部分失败继续执行语义，除非先由测试锁定且证明等价
- 不改写 inlet memory block 文本前缀 `INLET_MEMORY_CONTEXT_PREFIX`
- 不引入新依赖、不改 `pyproject.toml`、不改 `open-webui/` 子模块
- 不把导入时初始化改成 lazy init
- 不顺手做性能优化、日志文案重写、业务规则调整

## Verification Strategy
> ZERO HUMAN INTERVENTION — all verification is agent-executed.
- Test decision: tests-after + `pytest` / `pytest-asyncio`
- QA policy: 每个任务都必须绑定 agent 可执行验证；实现与测试不可拆开
- Evidence: `.sisyphus/evidence/task-{N}-{slug}.{ext}`
- Syntax gate: `uv run python -m py_compile auto_memory.py tests/test_auto_memory_function_calling.py`
- Targeted regression: `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py`
- Full isolated regression: `uv run pytest -q --ignore=open-webui`

## Execution Strategy
### Parallel Execution Waves
> Target: 5-8 tasks per wave. <3 per wave (except final) = under-splitting.
> Extract shared dependencies as Wave-1 tasks for max parallelism.

Wave 1: 契约冻结与缺口测试（测试优先，建立不可变边界）
Wave 2: 纯函数/helper seam 重构（schema、planner 解析、消息构造）
Wave 3: 生命周期与动作执行 seam 重构（expiry、cleanup、boost、apply）
Wave 4: hook 编排与最终整理（auto_memory / inlet / outlet / 文档化段落整理）

### Dependency Matrix (full, all tasks)
- 1 blocks 2,3,4,5,6,7,8,9
- 2 blocks 4,5,6,7,8,9
- 3 blocks 7,8,9
- 4 blocks 7,8,9
- 5 blocks 7,8,9
- 6 blocks 8,9
- 7 blocks 9
- 8 blocks 9
- 9 blocks F1-F4

### Agent Dispatch Summary (wave → task count → categories)
- Wave 1 → 3 tasks → quick / unspecified-low
- Wave 2 → 3 tasks → quick / unspecified-high
- Wave 3 → 2 tasks → unspecified-high
- Wave 4 → 1 task → unspecified-high
- Final Verification → 4 tasks → oracle / unspecified-high / deep

## TODOs
> Implementation + Test = ONE task. Never separate.
> EVERY task MUST have: Agent Profile + Parallelization + QA Scenarios.

- [x] 1. 冻结现有外部契约与不可变行为基线

  **What to do**: 审核并补强 `tests/test_auto_memory_function_calling.py`，将当前已知稳定行为固化为“重构前必须通过”的契约测试。至少显式锁定：`delete -> update -> add` 顺序、非法 ID 拒绝、额外字段拒绝、无 tool_calls noop、inlet memory block 注入/替换、cleanup/boost 边界行为、`_build_webui_request()` 与 `_run_coro_in_new_loop()` 现有语义。
  **Must NOT do**: 不修改 `auto_memory.py` 的实现逻辑；不引入任何接口变更；不新增与本次重构无关的功能测试。

  **Recommended Agent Profile**:
  - Category: `quick` — Reason: 主要是现有测试文件的定向补强与契约锁定
  - Skills: `[]` — 无额外技能依赖
  - Omitted: `['git-master']` — 当前不是提交阶段

  **Parallelization**: Can Parallel: NO | Wave 1 | Blocks: [2, 3, 4, 5, 6, 7, 8, 9] | Blocked By: []

  **References** (executor has NO interview context — be exhaustive):
  - Pattern: `tests/test_auto_memory_function_calling.py:1-739` — 现有 `auto_memory.py` 行为约束主测试文件
  - Pattern: `auto_memory.py:115-119` — `ACTION_ORDER` 常量必须保持
  - Pattern: `auto_memory.py:288-308` — `emit_status()` payload 结构
  - Pattern: `auto_memory.py:371-408` — `build_memory_action_tools()` 的严格 schema 与动态 ID literal 行为
  - Pattern: `auto_memory.py:464-526` — async/thread/request helper 当前语义
  - Pattern: `auto_memory.py:1606-1634` — inlet memory block 替换/插入逻辑
  - Pattern: `README.md:54-68` — 仓库隔离测试命令

  **Acceptance Criteria** (agent-executable only):
  - [ ] `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py` 通过，并且新增/调整后的测试仅锁定既有行为，不要求实现变更才可通过

  **QA Scenarios** (MANDATORY — task incomplete without these):
  ```
  Scenario: Freeze current contract tests
    Tool: Bash
    Steps: 运行 `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py`
    Expected: 所有测试通过；输出中无失败用例；关键 node 覆盖动作顺序、严格 schema、inlet 注入、cleanup/boost、helper 行为
    Evidence: .sisyphus/evidence/task-1-contract-freeze.txt

  Scenario: Guard against scope creep in tests
    Tool: Bash
    Steps: 运行 `uv run python -m py_compile tests/test_auto_memory_function_calling.py`
    Expected: 命令退出码为 0，测试文件语法正确且未引入额外依赖要求
    Evidence: .sisyphus/evidence/task-1-contract-freeze-syntax.txt
  ```

  **Commit**: YES | Message: `test(auto-memory): freeze refactor contracts` | Files: [`tests/test_auto_memory_function_calling.py`]

- [x] 2. 为未充分覆盖的边界语义补足回归用例

  **What to do**: 在同一测试文件中增加针对 Metis 标记的高风险边界：`query_openai_sdk()` 在 dict-mode 与 single-model-mode 下的 no-tool-call / malformed tool 调用语义差异、`apply_memory_actions()` 的部分失败继续执行、blank/whitespace 内容跳过、`existing_ids` 截断边界（若当前实现为明确行为）、导入时初始化行为（若可通过非侵入方式锁定）。
  **Must NOT do**: 不为了方便测试而改实现；不新增第二测试文件；不通过 mock 绕过真正要锁的分支语义。

  **Recommended Agent Profile**:
  - Category: `unspecified-low` — Reason: 边界回归测试设计更细，但仍限于单测试文件
  - Skills: `[]` — 无额外技能依赖
  - Omitted: `['git-master']` — 当前不是提交阶段

  **Parallelization**: Can Parallel: NO | Wave 1 | Blocks: [4, 5, 6, 7, 8, 9] | Blocked By: [1]

  **References** (executor has NO interview context — be exhaustive):
  - Pattern: `auto_memory.py:1040-1224` — `query_openai_sdk()` 分支、tool 解析、返回模型差异
  - Pattern: `auto_memory.py:2026-2109` — `apply_memory_actions()` 分组、顺序、失败隔离、状态汇总
  - Pattern: `auto_memory.py:371-408` — `MAX_MEMORY_IDS_FOR_TOOLS` 相关 tool schema 构造边界
  - Pattern: `tests/test_auto_memory_function_calling.py:133-372` — 已有 planner/action 测试风格
  - Pattern: `tests/test_auto_memory_function_calling.py:497-731` — cleanup/boost/helper 测试风格

  **Acceptance Criteria** (agent-executable only):
  - [ ] `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py -k "tool_calls or schema or boost or cleanup or inlet"` 通过
  - [ ] 新增边界测试在当前实现下通过，且未强迫引入行为变更

  **QA Scenarios** (MANDATORY — task incomplete without these):
  ```
  Scenario: Verify malformed and no-op planner paths
    Tool: Bash
    Steps: 运行 `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py -k "tool_calls or strict_schema or invalid_id"`
    Expected: 所有相关测试通过，证明 dict-mode / no-op / strict schema 路径被锁定
    Evidence: .sisyphus/evidence/task-2-planner-edges.txt

  Scenario: Verify partial-failure and blank-content guardrails
    Tool: Bash
    Steps: 运行 `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py -k "apply or blank or failure"`
    Expected: 所有相关测试通过，证明 action 执行发生局部失败时仍保持既定处理语义
    Evidence: .sisyphus/evidence/task-2-action-edges.txt
  ```

  **Commit**: YES | Message: `test(auto-memory): lock edge-case behavior` | Files: [`tests/test_auto_memory_function_calling.py`]

- [x] 3. 记录并保留导入时初始化与运行时副作用顺序

  **What to do**: 审视 `MemoryExpiries`、`_ensure_table_exists()`、`_ensure_lifecycle_columns()` 的导入时执行关系，确保重构计划明确“保留副作用时机与顺序”。若测试无法直接断言 DB DDL，则至少以注释/结构边界锁定：这些调用仍位于模块加载阶段，且不会被懒加载替代。
  **Must NOT do**: 不把导入时初始化改造成延迟初始化；不改变表名、列名、DDL 行为；不接触 `open_webui` mock 包。

  **Recommended Agent Profile**:
  - Category: `quick` — Reason: 属于小范围结构约束与最小测试/注释冻结
  - Skills: `[]` — 无额外技能依赖
  - Omitted: `['git-master']` — 当前不是提交阶段

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: [7, 8, 9] | Blocked By: [1]

  **References** (executor has NO interview context — be exhaustive):
  - Pattern: `auto_memory.py:714-758` — `MemoryExpiries` 实例与初始化 helper 定义位置
  - Pattern: `auto_memory.py:759-760` — 导入时执行 `_ensure_table_exists()` / `_ensure_lifecycle_columns()`
  - Pattern: `tests/test_auto_memory_function_calling.py:1-739` — 当前未显式锁定 import-time side effect，可在不侵入实现的前提下最小补强

  **Acceptance Criteria** (agent-executable only):
  - [ ] `uv run python -m py_compile auto_memory.py tests/test_auto_memory_function_calling.py` 通过
  - [ ] 计划中的实现步骤明确保留导入时初始化，不引入 lazy init

  **QA Scenarios** (MANDATORY — task incomplete without these):
  ```
  Scenario: Preserve import-time bootstrap structure
    Tool: Bash
    Steps: 运行 `uv run python -m py_compile auto_memory.py tests/test_auto_memory_function_calling.py`
    Expected: 语法检查通过；导入级 helper 仍保持可解析且未改为延迟工厂形式
    Evidence: .sisyphus/evidence/task-3-import-bootstrap.txt

  Scenario: Confirm no unintended test expansion
    Tool: Bash
    Steps: 运行 `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py -k "build_webui_request or run_coro"`
    Expected: helper 相关测试通过，说明导入期/基础设施边界未被破坏
    Evidence: .sisyphus/evidence/task-3-import-helper-regression.txt
  ```

  **Commit**: NO | Message: `n/a` | Files: [`auto_memory.py`, `tests/test_auto_memory_function_calling.py`]

- [x] 4. 提取并内聚 planner/schema 纯函数辅助层（仍保留在单文件内）

  **What to do**: 在 `auto_memory.py` 内将 tool schema 构造、tool call 解析、action 对象组装、planner request 参数构造中可纯化/可命名的部分提取为内部 helper，使 `query_openai_sdk()` 只保留“准备请求 → 调用 provider → 解析返回 → 返回计划”的编排职责。优先提取不会触碰 OpenAI I/O 的纯逻辑；保留 `build_memory_action_tools()` 的返回形态与动态 ID literal 校验。
  **Must NOT do**: 不修改 `query_openai_sdk()` 的公开签名；不改变 dict-mode 与单模型模式的返回差异；不改变 strict schema 失败时的跳过/报错语义。

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: 涉及高耦合函数内部重构，但不应改动外部行为
  - Skills: `[]` — 无额外技能依赖
  - Omitted: `['refactor']` — 本仓库约束下直接按明确计划操作更稳妥

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: [7, 8, 9] | Blocked By: [1, 2]

  **References** (executor has NO interview context — be exhaustive):
  - Pattern: `auto_memory.py:371-408` — 保留 `build_memory_action_tools()` 契约
  - Pattern: `auto_memory.py:1040-1224` — `query_openai_sdk()` 当前职责全集
  - Pattern: `tests/test_auto_memory_function_calling.py:133-319` — planner、schema、no-tool-call、strict validation 已有约束
  - External: `https://platform.openai.com/docs/guides/function-calling` — 仅用于理解 tool-calling 请求/响应结构，不可借机改语义

  **Acceptance Criteria** (agent-executable only):
  - [ ] `uv run python -m py_compile auto_memory.py tests/test_auto_memory_function_calling.py` 通过
  - [ ] `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py -k "deterministic_order or invalid_id or no_tool_calls or extra_keys"` 通过
  - [ ] `query_openai_sdk()` 代码长度与分支复杂度下降，但外部测试结果不变

  **QA Scenarios** (MANDATORY — task incomplete without these):
  ```
  Scenario: Preserve planner contract after helper extraction
    Tool: Bash
    Steps: 运行 `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py -k "deterministic_order or invalid_id or no_tool_calls or extra_keys"`
    Expected: 所有 planner 相关回归测试通过，证明 schema、tool_calls、strict validation 行为未变
    Evidence: .sisyphus/evidence/task-4-planner-refactor.txt

  Scenario: Preserve syntax after internal helper moves
    Tool: Bash
    Steps: 运行 `uv run python -m py_compile auto_memory.py tests/test_auto_memory_function_calling.py`
    Expected: 退出码为 0，helper 提取未引入循环引用或签名错误
    Evidence: .sisyphus/evidence/task-4-planner-refactor-syntax.txt
  ```

  **Commit**: YES | Message: `refactor(auto-memory): extract planner helpers within single file` | Files: [`auto_memory.py`, `tests/test_auto_memory_function_calling.py`]

- [x] 5. 清理消息拼接与 memory query 构造的重复逻辑

  **What to do**: 以内聚 helper 的方式整理 `messages_to_string()`、`build_memory_query()`、`build_inlet_memory_context()`、`inject_memory_context_into_messages()` 周边的重复字符串拼接与索引决策，使消息构造规则集中表达且命名清晰；保持 `INLET_MEMORY_CONTEXT_PREFIX`、插入位置与替换旧 memory block 的语义完全不变。
  **Must NOT do**: 不改变 memory block 文本格式；不修改 query 选择“最近 user 消息 + 可选 assistant 上下文”的规则；不改变单条 user message 的处理分支。

  **Recommended Agent Profile**:
  - Category: `quick` — Reason: 以纯逻辑整理和重复消除为主，风险低于 provider/DB seam
  - Skills: `[]` — 无额外技能依赖
  - Omitted: `['refactor']` — 无需额外抽象工具链

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: [8, 9] | Blocked By: [1]

  **References** (executor has NO interview context — be exhaustive):
  - Pattern: `auto_memory.py:108-109` — `STRINGIFIED_MESSAGE_TEMPLATE` 与 `INLET_MEMORY_CONTEXT_PREFIX`
  - Pattern: `auto_memory.py:976-1008` — `messages_to_string()`
  - Pattern: `auto_memory.py:1431-1509` — `build_memory_query()`
  - Pattern: `auto_memory.py:1588-1634` — inlet memory context 构造与注入
  - Test: `tests/test_auto_memory_function_calling.py:402-496` — inlet 注入/替换现有行为约束

  **Acceptance Criteria** (agent-executable only):
  - [ ] `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py -k "inlet or inject_memory_context"` 通过
  - [ ] `uv run python -m py_compile auto_memory.py tests/test_auto_memory_function_calling.py` 通过

  **QA Scenarios** (MANDATORY — task incomplete without these):
  ```
  Scenario: Preserve inlet memory block insertion semantics
    Tool: Bash
    Steps: 运行 `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py -k "inlet or inject_memory_context"`
    Expected: 单条 user message、替换旧 memory block、插入位置相关测试全部通过
    Evidence: .sisyphus/evidence/task-5-inlet-context.txt

  Scenario: Preserve message formatting helpers
    Tool: Bash
    Steps: 运行 `uv run python -m py_compile auto_memory.py tests/test_auto_memory_function_calling.py`
    Expected: 语法检查通过，字符串模板与 helper 提取未破坏函数签名
    Evidence: .sisyphus/evidence/task-5-inlet-context-syntax.txt
  ```

  **Commit**: NO | Message: `n/a` | Files: [`auto_memory.py`]

- [x] 7. 收敛 expiry CRUD、cleanup 与 boost 的生命周期逻辑

  **What to do**: 在不改变表结构、字段语义与调用时机的前提下，整理 `MemoryExpiryTable` 相关更新逻辑，以及 `cleanup_expired_memories()` / `boost_memories()` 的重复时间计算、clamp、fallback、统计汇总与写预算分支。目标是让 lifecycle 逻辑更易读、分支职责更清晰，但最终统计、hard cap、legacy backfill、缺失记录 fallback 行为必须保持不变。
  **Must NOT do**: 不改表名/列名/索引；不改变默认 hard expiry、strength clamp、cleanup retry 强删阈值；不合并掉现有统计字段；不修改实际删除仍经由 router/同步删除 helper 的路径。

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: 生命周期逻辑复杂、状态分支密集、回归风险高
  - Skills: `[]` — 无额外技能依赖
  - Omitted: `['refactor']` — 需要按既定 seam 稳定推进，而不是大范围自动改写

  **Parallelization**: Can Parallel: NO | Wave 3 | Blocks: [9] | Blocked By: [1, 2, 3]

  **References** (executor has NO interview context — be exhaustive):
  - Pattern: `auto_memory.py:532-714` — `MemoryExpiry` / `MemoryExpiryTable` 现有字段与 CRUD
  - Pattern: `auto_memory.py:1271-1361` — expiry 初始化与 add/delete 联动逻辑
  - Pattern: `auto_memory.py:1637-1729` — `cleanup_expired_memories()` 统计、retry、强删语义
  - Pattern: `auto_memory.py:1731-1922` — `boost_memories()` hard cap、legacy/backfill/fallback、写预算逻辑
  - Test: `tests/test_auto_memory_function_calling.py:330-372` — add 后 expiry 初始化行为
  - Test: `tests/test_auto_memory_function_calling.py:497-708` — cleanup/boost 现有行为回归

  **Acceptance Criteria** (agent-executable only):
  - [ ] `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py -k "expiry or cleanup or boost or add_action"` 通过
  - [ ] `uv run python -m py_compile auto_memory.py tests/test_auto_memory_function_calling.py` 通过
  - [ ] cleanup/boost/add-expiry 相关统计与边界测试结果不变

  **QA Scenarios** (MANDATORY — task incomplete without these):
  ```
  Scenario: Preserve cleanup and retry semantics
    Tool: Bash
    Steps: 运行 `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py -k "cleanup_expired_memories"`
    Expected: 详细统计、batch size、retry 强删阈值相关测试全部通过
    Evidence: .sisyphus/evidence/task-7-cleanup-lifecycle.txt

  Scenario: Preserve boost hard-cap and fallback semantics
    Tool: Bash
    Steps: 运行 `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py -k "boost_memories or add_action_initializes_expiry or add_action_without_memory_id"`
    Expected: hard cap、legacy record 回填、缺失记录 fallback、add 后 expiry 初始化相关测试全部通过
    Evidence: .sisyphus/evidence/task-7-boost-lifecycle.txt
  ```

  **Commit**: YES | Message: `refactor(auto-memory): consolidate expiry and action execution helpers` | Files: [`auto_memory.py`, `tests/test_auto_memory_function_calling.py`]

- [x] 8. 拆清 action 执行编排，保留顺序与局部失败容忍语义

  **What to do**: 重构 `apply_memory_actions()`，将 action 分组、具体执行、错误包装、状态汇总拆成内部 helper，使主体方法只表达顺序与编排。必须保留 `ACTION_ORDER` 驱动顺序、单个 action 失败不阻断后续 action 的语义、状态 emit 与日志摘要格式的兼容性。
  **Must NOT do**: 不改变 add/update/delete 路由调用方式；不改变无 action 时的返回与状态路径；不改变 blank 内容跳过或 invalid action 的处理语义。

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: 直接处于 mutation 编排核心，牵涉高风险语义
  - Skills: `[]` — 无额外技能依赖
  - Omitted: `['refactor']` — 需严格服从现有测试契约逐步整理

  **Parallelization**: Can Parallel: YES | Wave 3 | Blocks: [9] | Blocked By: [1, 2]

  **References** (executor has NO interview context — be exhaustive):
  - Pattern: `auto_memory.py:115-119` — `ACTION_ORDER`
  - Pattern: `auto_memory.py:1230-1361` — delete/add 辅助路径
  - Pattern: `auto_memory.py:2026-2109` — `apply_memory_actions()` 当前分组、顺序与状态逻辑
  - Test: `tests/test_auto_memory_function_calling.py:133-205` — 确定性执行顺序测试
  - Test: `tests/test_auto_memory_function_calling.py:208-319` — strict validation / no-op / extra keys 等 planner 前置约束
  - Test: `tests/test_auto_memory_function_calling.py`（任务 2 补充的 edge tests） — 局部失败、blank 内容、异常路径

  **Acceptance Criteria** (agent-executable only):
  - [ ] `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py -k "deterministic_order or failure or blank"` 通过
  - [ ] `uv run python -m py_compile auto_memory.py tests/test_auto_memory_function_calling.py` 通过

  **QA Scenarios** (MANDATORY — task incomplete without these):
  ```
  Scenario: Preserve action ordering and execution grouping
    Tool: Bash
    Steps: 运行 `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py -k "deterministic_order"`
    Expected: delete -> update -> add 顺序测试通过
    Evidence: .sisyphus/evidence/task-8-action-order.txt

  Scenario: Preserve failure isolation semantics
    Tool: Bash
    Steps: 运行 `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py -k "failure or blank"`
    Expected: 局部失败与空内容相关测试通过，证明 apply 层没有变成全局失败
    Evidence: .sisyphus/evidence/task-8-action-failure-isolation.txt
  ```

  **Commit**: NO | Message: `n/a` | Files: [`auto_memory.py`, `tests/test_auto_memory_function_calling.py`]

- [x] 9. 精简 Filter 编排层，重写 `auto_memory()` / `inlet()` / `outlet()` 的可读性结构

  **What to do**: 在前置 helper 已稳定后，整理 `Filter` 顶层流程：把 gating、query、cleanup、boost、planner、action apply、status emit 的调用顺序明确表达出来，让 `auto_memory()` 只做 orchestration；同步简化 `inlet()` / `outlet()` 的早返回条件和调用桥接，但必须保持 temp chat、权限检查、feature 开关、user valves、memory context 注入与 detached 执行语义不变。
  **Must NOT do**: 不调整 gating 顺序；不改 outlet 是否 fire-and-forget；不改 inlet 注入时机；不改 `get_restricted_user_valve()` 的权限覆盖规则；不改日志/debug 是否输出的控制条件。

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: 顶层流程重排容易引入短路顺序回归，需要高谨慎度
  - Skills: `[]` — 无额外技能依赖
  - Omitted: `['refactor']` — 以行为锁定为主，不宜大范围自动重命名/搬运

  **Parallelization**: Can Parallel: NO | Wave 4 | Blocks: [F1, F2, F3, F4] | Blocked By: [4, 5, 6, 7, 8]

  **References** (executor has NO interview context — be exhaustive):
  - Pattern: `auto_memory.py:1363-1430` — `get_restricted_user_valve()` 权限覆盖
  - Pattern: `auto_memory.py:1512-1634` — related memories 检索与 inlet context 注入
  - Pattern: `auto_memory.py:1924-2024` — `auto_memory()` 当前 orchestration
  - Pattern: `auto_memory.py:2112-2261` — `inlet()` / `outlet()` gating、bridge、detached 执行
  - Test: `tests/test_auto_memory_function_calling.py:402-496` — inlet 注入与替换
  - Test: `tests/test_auto_memory_function_calling.py:133-708` — planner / apply / cleanup / boost 相关不变量
  - Pattern: `README.md:54-68` — 最终验证命令

  **Acceptance Criteria** (agent-executable only):
  - [ ] `uv run python -m py_compile auto_memory.py tests/test_auto_memory_function_calling.py` 通过
  - [ ] `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py` 通过
  - [ ] `uv run pytest -q --ignore=open-webui` 通过

  **QA Scenarios** (MANDATORY — task incomplete without these):
  ```
  Scenario: Preserve full auto_memory single-file orchestration behavior
    Tool: Bash
    Steps: 运行 `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py`
    Expected: 整个 auto_memory 相关测试文件全部通过
    Evidence: .sisyphus/evidence/task-9-auto-memory-full-regression.txt

  Scenario: Preserve isolated repository behavior
    Tool: Bash
    Steps: 运行 `uv run pytest -q --ignore=open-webui`
    Expected: 根级测试全集通过，且未触发 `open-webui/` 子模块测试
    Evidence: .sisyphus/evidence/task-9-repo-isolated-regression.txt
  ```

  **Commit**: YES | Message: `refactor(auto-memory): simplify hook orchestration without behavior changes` | Files: [`auto_memory.py`, `tests/test_auto_memory_function_calling.py`]

- [x] 6. 统一 async/thread/request 辅助层的边界与命名

  **What to do**: 整理 `_run_coro_in_new_loop()`、`_run_async_in_thread()`、`_run_detached()`、`_build_webui_request()` 的注释、命名与调用边界，使同步/异步桥接职责清晰且彼此不重复；允许小范围共用 helper，但不得改变线程创建、异常传播、fire-and-forget 行为。
  **Must NOT do**: 不切换并发模型；不把同步桥接改为事件循环复用；不改变 `_run_coro_in_new_loop()` 的异常传播语义。

  **Recommended Agent Profile**:
  - Category: `quick` — Reason: 基础设施层小范围整理，但要保持行为精确不变
  - Skills: `[]` — 无额外技能依赖
  - Omitted: `['refactor']` — 不需要 AST/LSP 重写级别动作

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: [9] | Blocked By: [1]

  **References** (executor has NO interview context — be exhaustive):
  - Pattern: `auto_memory.py:464-526` — async/thread/request helper 全部定义
  - Test: `tests/test_auto_memory_function_calling.py:709-731` — `_run_coro_in_new_loop()`、`_build_webui_request()` 现有测试
  - Pattern: `auto_memory.py:2185-2261` — `outlet()` 调用 detached/bridged helper 的上下文

  **Acceptance Criteria** (agent-executable only):
  - [ ] `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py -k "run_coro or build_webui_request"` 通过
  - [ ] `uv run python -m py_compile auto_memory.py tests/test_auto_memory_function_calling.py` 通过

  **QA Scenarios** (MANDATORY — task incomplete without these):
  ```
  Scenario: Preserve coroutine runner behavior
    Tool: Bash
    Steps: 运行 `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py -k "run_coro"`
    Expected: 执行成功与异常传播两个测试全部通过
    Evidence: .sisyphus/evidence/task-6-async-bridge.txt

  Scenario: Preserve request helper behavior
    Tool: Bash
    Steps: 运行 `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py -k "build_webui_request"`
    Expected: request helper 测试通过，scope/type/app 结构保持兼容
    Evidence: .sisyphus/evidence/task-6-request-helper.txt
  ```

  **Commit**: NO | Message: `n/a` | Files: [`auto_memory.py`]

## Final Verification Wave (MANDATORY — after ALL implementation tasks)
> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.
> **Do NOT auto-proceed after verification. Wait for user's explicit approval before marking work complete.**
> **Never mark F1-F4 as checked before getting user's okay.** Rejection or user feedback -> fix -> re-run -> present again -> wait for okay.
- [ ] F1. Plan Compliance Audit — oracle
- [ ] F2. Code Quality Review — unspecified-high
- [ ] F3. Real Manual QA — unspecified-high (+ playwright if UI)
- [ ] F4. Scope Fidelity Check — deep

## Commit Strategy
- Commit 1: `test(auto-memory): freeze refactor contracts`
- Commit 2: `refactor(auto-memory): extract planner helpers within single file`
- Commit 3: `refactor(auto-memory): consolidate expiry and action execution helpers`
- Commit 4: `refactor(auto-memory): simplify hook orchestration without behavior changes`
- Commit 5: `chore(auto-memory): final single-file cleanup and section reordering`

## Success Criteria
- 单文件原则得到保持：所有实现仍在 `auto_memory.py`
- 对外契约未变：hook、valves、tool schema、status payload、mutation routing 全部兼容
- `Filter` 主流程职责更清晰，重复逻辑被内聚到命名明确的内部 helper
- 回归测试覆盖关键边界并全部通过
- 没有引入额外依赖、额外文件架构或范围外修改
