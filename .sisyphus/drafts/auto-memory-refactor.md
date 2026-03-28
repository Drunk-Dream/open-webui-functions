# Draft: Auto Memory Refactor

## Requirements (confirmed)
- 重构 auto_memory.py整个文件
- 优化脚本架构
- 清楚冗余代码
- 保持单一脚本文件原则

## Technical Decisions
- 单文件重构作为硬约束：不拆分为多模块
- 需保持外部契约稳定：`Filter.inlet/outlet`、`Valves/UserValves` 语义、`ACTION_ORDER`、memory action 模型与 status payload 结构
- 首要目标：行为完全不变，优先可维护性
- 测试策略：测试后补，先复用现有回归命令，再补受重构影响的断言
- 允许修改范围：`auto_memory.py` + 对应测试文件；不扩展到多文件架构拆分
- 计划文件已生成：`.sisyphus/plans/auto-memory-full-file-refactor.md`

## Research Findings
- `auto_memory.py` 当前同时承载配置、Pydantic 模型、OpenAI tool-calling、DB/迁移、memory 生命周期、inlet/outlet 编排，职责交叠明显
- 主要耦合热点为 `auto_memory()`、`query_openai_sdk()`、`apply_memory_actions()`、`boost_memories()`、`cleanup_expired_memories()`
- 现有测试集中在 `tests/test_auto_memory_function_calling.py`，已覆盖动作顺序、schema 严格校验、inlet 注入、cleanup/boost、辅助函数等关键行为
- 仓库已有隔离验证命令：`uv run pytest -q --ignore=open-webui tests/test_auto_memory_function_calling.py` 与全量 `uv run pytest -q --ignore=open-webui`
- 语法检查基线存在：`uv run python -m py_compile auto_memory.py auto_chat_cleanup.py tests/test_auto_memory_function_calling.py`
- Metis 要求已吸收：显式保留导入时初始化、副作用顺序、dict-mode 与 single-model-mode 差异、局部失败继续执行、inlet/outlet 短路顺序

## Open Questions
- 无阻塞问题，已具备生成计划条件

## Scope Boundaries
- INCLUDE: auto_memory.py 全文件架构梳理与冗余清理计划
- INCLUDE: 为重构同步更新 `tests/test_auto_memory_function_calling.py`
- EXCLUDE: 多文件拆分方案
