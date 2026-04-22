# Auto Memory 三层衰退强化模型落地计划

## TL;DR
> **Summary**: 在 `auto_memory.py` 内把当前滑动续期逻辑重构为“硬过期 + 强度衰减/强化 + 对话内惰性维护”三层模型，并让 `auto_memory_expiry` 成为唯一生命周期状态权威。
> **Deliverables**:
> - `auto_memory.py` 中收敛后的生命周期状态机与兼容迁移逻辑
> - 覆盖硬过期、强度、防刷、缺失记录重建、失败重试清理的测试
> - 删除/合并冗余生命周期分支，保持单文件且缩短脚本长度
> **Effort**: Medium
> **Parallel**: YES - 2 waves
> **Critical Path**: 1 → 2 → 3 → 4 → 5 → 6

## Context
### Original Request
用户要求取回“`auto_memory.py 记忆过期清理问题`”对话中的 3 层记忆衰退强化模型，并据此修改 `auto_memory.py`：补齐硬过期时间写入、`None` 值兼容回填、记忆强度与高频访问防刷机制，同时清理冗余代码，在单文件原则下尽可能缩短脚本。

### Interview Summary
- 历史对话已确认目标模型为：**硬过期 + 强度衰减/强化 + 事件内惰性维护**。
- 约束已明确：**只能动 `auto_memory_expiry` 表的生命周期数据**，不能改其他业务表结构。
- 兼容要求已明确：旧数据通过运行时补列和回填兼容；缺失生命周期记录要能基于 `memory.created_at` 重建，而不是基于当前时间重置寿命。
- Oracle 历史审查已追加规则：cleanup 删除失败需要重试计数；查询限流要下推 SQL；迁移 SQL 需后端兼容且可观测。

### Metis Review (gaps addressed)
- 已补足的 guardrails：冻结常量与公式；禁止访问延长 `hard_expire_at`；要求 TDD 先锁定语义；要求清理 delete/update 与 orphan tracking 的不一致；要求 bootstrap/backfill 可观测且幂等。
- 已消除的歧义：维护只允许在对话事件内执行，但允许每次对话顺手处理一小批 SQL 选出的到期/待复核记录。
- 默认采用的实现决策：不新增文件；测试继续放在 `tests/test_auto_memory_function_calling.py`；保留现有 tool-calling 行为，不重写提示词和记忆提取流程。

## Work Objectives
### Core Objective
将 `auto_memory.py` 当前“命中即滑动续期”的生命周期逻辑，重构为一个可验证、可兼容、不会无限续命的三层 retention 状态机，并保持插件其余行为不变。

### Deliverables
- `auto_memory.py` 中统一的生命周期常量、状态字段访问与 helper 计算逻辑
- `auto_memory.py` 中统一的 lazy maintenance 执行链：命中处理 + SQL 限流清理 + mutation 同步
- `tests/test_auto_memory_function_calling.py` 中新增的生命周期语义测试
- 运行结果证据：语法检查、定向测试、全量回归测试

### Definition of Done (verifiable conditions with commands)
- `auto_memory.py` 中新增记忆时，若 `auto_memory_expiry` 缺失或关键列为 `NULL`，会补全 `hard_expire_at`、`strength`、`access_count`、`last_accessed_at`、`last_decay_at`、`pinned`、`cleanup_fail_count`
- 任意记忆被重复命中后，`hard_expire_at` 不发生变化
- burst 窗口内重复命中，强化收益显著折减，不会无限放大强度
- cleanup 在 vector 删除失败时先累计 `cleanup_fail_count`，达到阈值后才移除 tracking
- 缺失生命周期记录时，重建 `hard_expire_at` 基于原始 `memory.created_at`
- 通过以下命令：
  - `uv run python -m py_compile auto_memory.py tests/test_auto_memory_function_calling.py`
  - `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py -k "expiry or decay or reinforcement or cleanup or bootstrap or lifecycle"`
  - `uv run pytest -q --ignore=open-webui`

### Must Have
- 单文件实现，仅改 `auto_memory.py` 与测试文件
- 生命周期唯一权威为 `auto_memory_expiry`
- 明确常量与公式：
  - `MAX_LIFETIME_DAYS = 90`
  - `INITIAL_STRENGTH = 40`
  - `BASE_DECAY_PER_DAY = 1.0`
  - `ACCESS_GAIN = 12`
  - `GAIN_DAMPING = 0.15`
  - `BURST_WINDOW_MINUTES = 30`
  - `BURST_GAIN_MULTIPLIER = 0.25`
  - `FORGET_THRESHOLD = 15`
  - `DELETE_GRACE_DAYS = 7`
  - `MAINTENANCE_BATCH_SIZE = 20`
  - `MAX_WRITES_PER_EVENT = 50`
  - `CLEANUP_DELETE_AFTER_FAILURES = 3`
- 明确公式：
  - `hard_expire_at = created_at + MAX_LIFETIME_DAYS`
  - `decayed_strength = max(0, strength - elapsed_days * BASE_DECAY_PER_DAY)`
  - `gain = ACCESS_GAIN / (1 + access_count * GAIN_DAMPING)`
  - 若 `now - last_accessed_at <= BURST_WINDOW_MINUTES`，则 `gain *= BURST_GAIN_MULTIPLIER`
  - `new_strength = min(100, decayed_strength + gain)`
  - `soft_extension_days = max(1, round(new_strength / 10))`
  - `expired_at = min(now + soft_extension_days, hard_expire_at)`
- 旧数据回填幂等且可观测，不允许静默吞迁移失败

### Must NOT Have (guardrails, AI slop patterns, scope boundaries)
- 不新增模块、表、后台任务、定时器
- 不修改 `open-webui/` 子模块
- 不改变 LLM tool-calling 提示词的业务目标
- 不保留旧的“访问直接相对 now 续命到 max_expiry_days”的竞争逻辑
- 不把 `memory.created_at` 丢失时的缺省硬上限基准设为 `now`
- 不使用模糊验收语句，例如“看起来正常”或“手动验证一下”

## Verification Strategy
> ZERO HUMAN INTERVENTION - all verification is agent-executed.
- Test decision: **TDD** + `pytest` / `pytest-asyncio`
- QA policy: Every task includes agent-executed scenarios and evidence file路径
- Evidence: `.sisyphus/evidence/task-{N}-{slug}.{ext}`

## Execution Strategy
### Parallel Execution Waves
> Target: 5-8 tasks per wave. <3 per wave (except final) = under-splitting.
> Extract shared dependencies as Wave-1 tasks for max parallelism.

Wave 1: 1) 生命周期常量与 bootstrap 收敛，2) 生命周期公式 helper 与 SQL 限流维护，3) 生命周期测试先行补齐
Wave 2: 4) 命中/cleanup 主链替换，5) add/update/delete 与 orphan tracking 对齐，6) 单文件瘦身与全量回归

### Dependency Matrix (full, all tasks)
| Task | Depends On | Blocks |
|---|---|---|
| 1 | none | 2,4,5 |
| 2 | 1 | 4,5,6 |
| 3 | 1 | 4,5,6 |
| 4 | 1,2,3 | 6 |
| 5 | 1,2,3 | 6 |
| 6 | 4,5 | F1-F4 |

### Agent Dispatch Summary (wave → task count → categories)
- Wave 1 → 3 tasks → `quick`, `deep`, `quick`
- Wave 2 → 3 tasks → `deep`, `quick`, `quick`

## TODOs
> Implementation + Test = ONE task. Never separate.
> EVERY task MUST have: Agent Profile + Parallelization + QA Scenarios.

- [x] 1. 收敛生命周期字段与 bootstrap/backfill

  **What to do**: 在 `auto_memory.py` 中统一声明并使用生命周期字段：`hard_expire_at`、`last_accessed_at`、`last_decay_at`、`access_count`、`strength`、`pinned`、`cleanup_fail_count`。重写 `_ensure_lifecycle_columns()` 和相关 bootstrap helper，使其成为幂等、可观测的运行时补列/回填入口；移除或合并静默吞异常与重复默认值分配逻辑；若 ORM 模型未显式声明这些列，则明确采用一致的属性访问策略（例如集中 `getattr/setattr` 封装或补充 ORM 声明），避免散落访问。
  **Must NOT do**: 不新增表；不把回填逻辑扩展到 `auto_memory_expiry` 之外；不在 import-time 做全表重算；不保留 `except Exception: pass` 这类无观测分支。

  **Recommended Agent Profile**:
  - Category: `quick` - Reason: 主要是单文件 schema/bootstrap 收敛与明确字段访问边界。
  - Skills: `[]` - 无额外技能依赖。
  - Omitted: `['Skill Development']` - 与插件技能编写无关。

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: [2, 4, 5] | Blocked By: [none]

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `auto_memory.py:579-588` - `MemoryExpiry` ORM 当前仅声明基础列，需处理 ORM/实际表列不完全对齐的问题。
  - Pattern: `auto_memory.py:683-736` - import-time `_ensure_table_exists()` / `_ensure_lifecycle_columns()` 现有 bootstrap 入口。
  - Pattern: `auto_memory.py:1124-1148` - `_add_memory_with_expiry()` / `_initialize_memory_expiry()` 是新增记忆时写生命周期状态的现有路径。
  - Pattern: `auto_memory.py:1536-1568` - `_cleanup_expired_memory_record()` 体现 cleanup 与 lifecycle tracking 的耦合点。
  - Test: `tests/test_auto_memory_function_calling.py:42-78` - 现有 fixture 风格与 `Memory` 构造方式。

  **Acceptance Criteria** (agent-executable only):
  - [ ] `auto_memory.py` 中所有生命周期字段默认值与回填逻辑仅存在一个集中入口
  - [ ] 缺列/NULL 值场景下，bootstrap 会幂等补齐字段并记录可观测日志或状态
  - [ ] `uv run python -m py_compile auto_memory.py tests/test_auto_memory_function_calling.py` 通过

  **QA Scenarios** (MANDATORY - task incomplete without these):
  ```
  Scenario: Legacy lifecycle row bootstrap backfill
    Tool: Bash
    Steps: 运行 `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py -k "bootstrap or lifecycle"`
    Expected: 新增测试断言缺列/NULL 生命周期数据被幂等回填，测试通过
    Evidence: .sisyphus/evidence/task-1-bootstrap.txt

  Scenario: Bootstrap observability on failure path
    Tool: Bash
    Steps: 运行同一组测试，包含模拟 bootstrap 异常的测试用例
    Expected: 测试断言失败不会被静默吞掉，且存在可观测输出/分支行为
    Evidence: .sisyphus/evidence/task-1-bootstrap-error.txt
  ```

  **Commit**: YES | Message: `refactor(auto-memory): centralize lifecycle bootstrap state` | Files: [`auto_memory.py`, `tests/test_auto_memory_function_calling.py`]

- [x] 2. 冻结三层模型公式与惰性维护 helper

  **What to do**: 在 `auto_memory.py` 中集中定义三层模型常量与纯计算 helper：硬过期计算、强度衰减、递减收益强化、burst 防刷折减、soft expiry 夹到 hard cap 的规则，以及 SQL 层筛选/排序/limit 的惰性维护候选选择。将现有 `_calculate_boosted_expired_at()` 和相关散落逻辑收敛为这一组 helper，确保访问只改变 `strength`、`expired_at`、计数与时间戳，不改变 `hard_expire_at`。
  **Must NOT do**: 不把公式留成占位注释；不继续沿用“`now + max_expiry_days` 即 hard cap”的旧语义；不在 Python 层先全量拉取再切片。

  **Recommended Agent Profile**:
  - Category: `deep` - Reason: 涉及状态机规则冻结、计算一致性和 SQL 约束统一。
  - Skills: `[]` - 无额外技能依赖。
  - Omitted: `['Skill Development']` - 无关。

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: [4, 5, 6] | Blocked By: [1]

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `auto_memory.py:1650-1689` - `boost_memories()` 当前逐条 lookup/update，是三层模型替换的主要入口。
  - Pattern: `auto_memory.py:621-676` - `MemoryExpiryTable.get_expired()` 现有 SQL 查询入口，需要支持稳定排序与 limit 下推。
  - Pattern: `auto_memory.py:1573-1616` - `cleanup_expired_memories()` 当前维护流程与批量处理位置。
  - Pattern: `auto_memory.py:1790-1952` - `auto_memory()` 现有 orchestration spine，新的 helper 必须嵌入这里而不是另起流程。
  - External: `session:ses_2d07bba75ffetgHudGsqHe21iC` - 历史对话已明确默认常量与 burst 机制。

  **Acceptance Criteria** (agent-executable only):
  - [ ] `hard_expire_at` 创建后不会因任何访问路径被修改
  - [ ] burst 窗口内重复访问时，强化收益按 `BURST_GAIN_MULTIPLIER` 折减
  - [ ] maintenance 相关查询在 SQL 层完成过滤、排序和 `LIMIT`

  **QA Scenarios** (MANDATORY - task incomplete without these):
  ```
  Scenario: Hard cap remains immutable after repeated accesses
    Tool: Bash
    Steps: 运行 `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py -k "hard_expire or reinforcement or decay"`
    Expected: 测试断言多次命中后 `hard_expire_at` 不变，而 `strength`/`expired_at` 按公式变化
    Evidence: .sisyphus/evidence/task-2-hard-cap.txt

  Scenario: Burst anti-gaming reduces gain within window
    Tool: Bash
    Steps: 运行同一组测试，包含短窗口重复命中的测试用例
    Expected: 第二次及后续 burst 命中获得的强化收益显著低于首次，且不超过上限
    Evidence: .sisyphus/evidence/task-2-burst.txt
  ```

  **Commit**: YES | Message: `refactor(auto-memory): freeze lifecycle formulas and lazy maintenance` | Files: [`auto_memory.py`, `tests/test_auto_memory_function_calling.py`]

- [x] 3. 先补生命周期语义测试

  **What to do**: 在 `tests/test_auto_memory_function_calling.py` 中新增生命周期测试，锁定以下行为：新增记忆初始化 hard cap；NULL/缺失生命周期数据回填；缺失 lifecycle row 用 `memory.created_at` 重建；重复访问不延长 hard cap；burst 防刷；cleanup 失败重试计数；达到失败阈值再丢弃 tracking；SQL limit 下推；零候选时 no-op。沿用现有 fixture 风格与 `@pytest.mark.asyncio` 约定，不新增测试文件。
  **Must NOT do**: 不把新测试拆到新文件；不只测 happy path；不依赖手工 DB 检查。

  **Recommended Agent Profile**:
  - Category: `quick` - Reason: 单文件测试扩展，目标清晰。
  - Skills: `[]` - 无额外技能依赖。
  - Omitted: `['Skill Development']` - 无关。

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: [4, 5, 6] | Blocked By: [1]

  **References** (executor has NO interview context - be exhaustive):
  - Test: `tests/test_auto_memory_function_calling.py:170-243` - 现有 async 测试风格、patch 模式、mutation call 断言模式。
  - Test: `tests/test_auto_memory_function_calling.py:245+` - 参数化、ValidationError 与 no-op 测试风格。
  - Pattern: `tests/test_auto_chat_cleanup.py` - 可参考其 async cleanup 测试组织方式。
  - Pattern: `open_webui.internal.db.get_db` patch 用法已在现有测试中出现，应复用该模式。

  **Acceptance Criteria** (agent-executable only):
  - [ ] 新增测试先失败后通过，且覆盖硬过期、强度、防刷、重建、重试清理、SQL limit
  - [ ] 所有新增生命周期测试都可通过单文件 pytest 命令执行
  - [ ] 不影响现有 tool-calling 测试通过

  **QA Scenarios** (MANDATORY - task incomplete without these):
  ```
  Scenario: Lifecycle-focused test subset runs deterministically
    Tool: Bash
    Steps: 运行 `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py -k "expiry or decay or reinforcement or cleanup or bootstrap or lifecycle"`
    Expected: 生命周期相关测试全部通过，输出稳定
    Evidence: .sisyphus/evidence/task-3-lifecycle-tests.txt

  Scenario: Existing tool-calling tests remain intact
    Tool: Bash
    Steps: 运行 `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py`
    Expected: 原有 tool-calling 用例与新增生命周期用例一起通过
    Evidence: .sisyphus/evidence/task-3-full-file-tests.txt
  ```

  **Commit**: YES | Message: `test(auto-memory): codify lifecycle retention semantics` | Files: [`tests/test_auto_memory_function_calling.py`]

- [x] 4. 替换命中与 cleanup 主链为三层模型

  **What to do**: 重构 `auto_memory()`、`boost_memories()`、`cleanup_expired_memories()` 及其直接 helper，使对话事件内的顺序变为：读取相关记忆 → 对本次命中项执行“先衰减后强化”的生命周期更新 → 处理一小批 overdue/待清理项 → 再进入 `_plan_memory_actions()` 和 `apply_memory_actions()`。将原“访问即延长到未来”的逻辑删除；cleanup 使用 `expired_at` 或 `hard_expire_at` 命中删除条件，并尊重 `MAX_WRITES_PER_EVENT` 与 `MAINTENANCE_BATCH_SIZE`。
  **Must NOT do**: 不更改 tool-calling 顺序 `delete -> update -> add`；不把 maintenance 放到单独线程/定时器；不保留旧 boost 语义作为 fallback。

  **Recommended Agent Profile**:
  - Category: `deep` - Reason: 需要在不破坏主业务流的前提下替换生命周期主链。
  - Skills: `[]` - 无额外技能依赖。
  - Omitted: `['Skill Development']` - 无关。

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: [6] | Blocked By: [1, 2, 3]

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `auto_memory.py:1841-1952` - `auto_memory()` 当前整条 orchestrator。
  - Pattern: `auto_memory.py:1650-1689` - `boost_memories()` 当前命中续期实现。
  - Pattern: `auto_memory.py:1573-1616` - `cleanup_expired_memories()` 当前清理实现。
  - Pattern: `auto_memory.py:1928-1952` - `apply_memory_actions()` 的既有调用顺序必须保持。
  - API/Type: `auto_memory.py:343-354` - `Memory` 类型中的 `created_at` / `update_at` 是缺失记录重建的关键时间来源。

  **Acceptance Criteria** (agent-executable only):
  - [ ] `auto_memory()` 主链中不再存在旧的滑动 TTL 续命路径
  - [ ] 命中处理、清理处理与 mutation 执行顺序符合计划定义
  - [ ] 单文件测试与全量回归通过

  **QA Scenarios** (MANDATORY - task incomplete without these):
  ```
  Scenario: Conversation event runs lazy maintenance before planning actions
    Tool: Bash
    Steps: 运行 `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py -k "lifecycle or cleanup or reinforcement"`
    Expected: 测试断言命中更新与 cleanup 在 memory action planning 前执行且输出正确
    Evidence: .sisyphus/evidence/task-4-main-flow.txt

  Scenario: No eligible maintenance rows remains a no-op
    Tool: Bash
    Steps: 运行同一组测试，包含零候选场景
    Expected: 无异常、无多余写入、无错误删除
    Evidence: .sisyphus/evidence/task-4-noop.txt
  ```

  **Commit**: YES | Message: `refactor(auto-memory): replace sliding ttl flow with lifecycle state machine` | Files: [`auto_memory.py`, `tests/test_auto_memory_function_calling.py`]

- [x] 5. 对齐 add/update/delete 与 orphan tracking

  **What to do**: 统一 `_add_memory_with_expiry()`、更新路径、删除路径与 cleanup 路径对 `auto_memory_expiry` 的写入/删除策略，确保 lifecycle tracking 不会因主记忆删除、vector 删除失败、或生命周期记录缺失而失真。实现 `cleanup_fail_count` 递增与阈值清除；对于已删除主记忆但未过期的 orphan lifecycle row，也要在 lazy maintenance 中可识别并按策略清掉。
  **Must NOT do**: 不在 vector 删除第一次失败时立即删除 tracking；不让 delete/update 路径继续与 expiry 状态脱节。

  **Recommended Agent Profile**:
  - Category: `quick` - Reason: 以路径对齐和失败策略为主，边界明确。
  - Skills: `[]` - 无额外技能依赖。
  - Omitted: `['Skill Development']` - 无关。

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: [6] | Blocked By: [1, 2, 3]

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `auto_memory.py:1124-1148` - 新增记忆初始化 expiry 的当前路径。
  - Pattern: `auto_memory.py:1169-1197` - 更新/删除附近 helper，需检查生命周期同步策略。
  - Pattern: `auto_memory.py:1536-1568` - `_cleanup_expired_memory_record()` 是重试计数与强制移除 tracking 的关键点。
  - Pattern: `auto_memory.py:621-676` - 过期/候选查询需要支撑 orphan row 检出与 SQL limit。
  - External: `session:ses_2d07bba75ffetgHudGsqHe21iC` - Oracle 已确认 cleanup 失败计数 + 上限后清除的策略。

  **Acceptance Criteria** (agent-executable only):
  - [ ] 删除失败时 `cleanup_fail_count` 递增，未达阈值不删 tracking
  - [ ] 达阈值后 tracking 被清掉，后续维护不会反复卡死在同一孤儿记录
  - [ ] 新增/更新/删除路径与 lifecycle state 保持一致

  **QA Scenarios** (MANDATORY - task incomplete without these):
  ```
  Scenario: Cleanup retry counter retains tracking before threshold
    Tool: Bash
    Steps: 运行 `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py -k "cleanup and retry"`
    Expected: 第一次/前几次失败只增加 `cleanup_fail_count`，tracking 仍存在
    Evidence: .sisyphus/evidence/task-5-retry.txt

  Scenario: Cleanup drops orphan tracking after retry threshold
    Tool: Bash
    Steps: 运行同一组测试，包含达到失败阈值的用例
    Expected: 达阈值后 tracking 被删除，测试断言通过
    Evidence: .sisyphus/evidence/task-5-threshold.txt
  ```

  **Commit**: YES | Message: `fix(auto-memory): align lifecycle tracking with cleanup retries` | Files: [`auto_memory.py`, `tests/test_auto_memory_function_calling.py`]

- [x] 6. 单文件瘦身、命名收敛与回归验证

  **What to do**: 在不改变外部行为的前提下，删除生命周期重构后残留的旧 helper、重复默认值、已失效的 compatibility 分支和重复注释；把生命周期相关命名收敛为一致风格，尤其处理 `update_at` 与 `updated_at` 的兼容读取策略，避免测试或重建时基准时间混乱。最后运行语法检查、定向测试、全量回归，并记录证据路径。
  **Must NOT do**: 不进行与生命周期无关的大规模重排；不把 `Memory` 对外契约粗暴改名导致现有测试/调用方破坏；不修改 `auto_chat_cleanup.py`。

  **Recommended Agent Profile**:
  - Category: `quick` - Reason: 以收尾瘦身、命名收敛和验证为主。
  - Skills: `[]` - 无额外技能依赖。
  - Omitted: `['Skill Development']` - 无关。

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: [F1, F2, F3, F4] | Blocked By: [4, 5]

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `auto_memory.py:344-349` - `Memory` 使用 `update_at`，与生命周期表 `updated_at` 不一致，需要兼容策略。
  - Pattern: `auto_memory.py:707-708` - bootstrap 回填中已涉及 `updated_at` 相关默认来源。
  - Pattern: `tests/test_auto_memory_function_calling.py:63-76` - 现有测试 fixture 使用 `update_at`。
  - Pattern: `AGENTS.md` - 要求最小 diff、遵循单文件插件风格、验证命令需忽略 `open-webui` 子模块。

  **Acceptance Criteria** (agent-executable only):
  - [ ] 生命周期旧 helper 与竞争逻辑被删除或合并，脚本总复杂度下降
  - [ ] `update_at` / `updated_at` 兼容读取不影响测试与运行时逻辑
  - [ ] 下列命令全部通过：
    - `uv run python -m py_compile auto_memory.py tests/test_auto_memory_function_calling.py`
    - `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py`
    - `uv run pytest -q --ignore=open-webui`

  **QA Scenarios** (MANDATORY - task incomplete without these):
  ```
  Scenario: Focused auto_memory regression passes after cleanup
    Tool: Bash
    Steps: 运行 `uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py`
    Expected: auto_memory 单文件测试全部通过
    Evidence: .sisyphus/evidence/task-6-auto-memory-regression.txt

  Scenario: Root regression passes without submodule tests
    Tool: Bash
    Steps: 运行 `uv run pytest -q --ignore=open-webui`
    Expected: 根目录测试全部通过，未触发 `open-webui/` 子模块测试
    Evidence: .sisyphus/evidence/task-6-root-regression.txt
  ```

  **Commit**: YES | Message: `chore(auto-memory): prune redundant lifecycle paths` | Files: [`auto_memory.py`, `tests/test_auto_memory_function_calling.py`]

## Final Verification Wave (MANDATORY — after ALL implementation tasks)
> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.
> **Do NOT auto-proceed after verification. Wait for user's explicit approval before marking work complete.**
> **Never mark F1-F4 as checked before getting user's okay.** Rejection or user feedback -> fix -> re-run -> present again -> wait for okay.
- [ ] F1. Plan Compliance Audit — oracle
- [ ] F2. Code Quality Review — unspecified-high
- [ ] F3. Real Manual QA — unspecified-high (+ playwright if UI)
- [ ] F4. Scope Fidelity Check — deep

## Commit Strategy
- Commit 1: `test(auto-memory): lock lifecycle semantics and compatibility` — 先加入失败测试，锁定三层模型语义
- Commit 2: `refactor(auto-memory): consolidate lifecycle retention model` — 收敛 `auto_memory.py` 生命周期逻辑并移除滑动 TTL 旧路径
- Commit 3: `chore(auto-memory): prune redundant lifecycle code` — 清理重复 helper、吞异常分支与多余状态路径

## Success Criteria
- 用户历史对话里约定的三层模型在 `auto_memory.py` 中有唯一、清晰、可测的实现
- 高访问频率不再导致无限续命
- 旧数据与缺列/缺记录场景仍能兼容运行
- 测试能稳定证明硬过期、强度衰减、防刷、失败重试与 SQL 限流维护均按预期工作
