
2026-03-27:
- 风险点：`build_memory_action_tools()` 的动态 ID literal schema 依赖当前已有 memory ID 集合；重构时如果改成更宽松的校验，相关契约测试会立刻暴露行为漂移。
- 风险点：cleanup/boost 边界测试目前主要冻结“空输入/空结果/硬上限”语义，未覆盖所有内部日志分支；这是可接受的，因为目标是冻结外部可观察行为。
- 范围纠偏：本轮只应保留 `tests/test_auto_memory_function_calling.py` 与 notepad 追加；任何对 `auto_memory.py` / `uv.lock` 的改动都不在此任务范围内。
- 需要二次范围纠偏：曾误把 `auto_memory.py` 和 `uv.lock` 带入工作区，已恢复为仅保留测试文件改动。
- 这次 scope 修正后仅保留测试契约收窄；`uv.lock` 已恢复，避免将依赖锁文件误算入任务 1 diff。
- 导入初始化回归测试使用 `importlib.reload(auto_memory)` 会触发 SQLAlchemy declarative 重复类名告警（`MemoryExpiry` 被重新注册）；这是当前实现下可接受副作用，测试仅锁定“调用 create_all 且 checkfirst=True”语义。
- 任务2范围纠偏：已明确回退 `uv.lock`，确保最终 diff 仅保留测试文件与 notepad（及编排状态文件）。
- 本次新增的 AST 顺序测试不会修改导入行为，但仍会读取模块源码；如果未来重排 bootstrap 代码，测试会直接失败，这正是想要的回归信号。
- 任务4 gotcha：`lsp_diagnostics` 会同时暴露文件里原有与新改动无关的问题；这次实际需要处理的是文件尾部 `body.get("messages", [])` 传给 `auto_memory()` 的类型不明确问题，用显式 `cast(list[dict[str, Any]], ...)` 才能把 error 级诊断清零。
- 任务5 gotcha：`lsp_diagnostics` 对 `auto_memory.py` 会报大量既有 warning（deprecated/Any/redeclaration），不应被误判为本次 refactor 失败；本次只要求 error 级诊断清零，且已经做到。
- 任务5 中若继续提炼 helper，优先保持“纯函数 + 原地调用”模式；不要把 query/inlet 规则再拆到跨文件模块，否则会超出单文件重构边界。
- 任务6 gotcha：`_start_daemon_thread()` 只是抽取线程创建/启动的重复代码，不能把异常传播或日志处理挪进去；否则会改变 `_run_async_in_thread()` / `_run_detached()` 的边界。
- 任务6范围保持：`_build_webui_request()` 仅保留最小 scope 构造，不能顺手补充 headers、cookies 或 request state，否则会影响 handler 契约。
- 任务8 gotcha：即使只做 apply-layer helper 提取，也要补跑整份 `tests/test_auto_memory_function_calling.py`，因为同文件里同时冻结了 planner/tool/action 的相邻契约；仅跑 failure/blank/order 子集不足以覆盖摘要日志与状态 emit 的兼容性回归风险。
- 任务7 gotcha：`boost_memories()` 的外部统计虽然简单，但内部时间公式同时承担“从 existing expiry 延长”“至少从 now 保底延长”“再受 max_expiry_days 限制”三层职责；抽 helper 时必须完整保留这个顺序，不能提前简化成单个 `min/max` 表达式后改掉可读语义。
- 任务7 gotcha：当前 targeted/full-file 回归都通过；唯一 warning 仍是 reload 测试触发的 SQLAlchemy declarative 重复类名告警，属于既有测试副作用，不是这次 lifecycle 重构引入的新问题。
