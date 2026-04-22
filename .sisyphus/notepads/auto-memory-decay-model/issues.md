1. 本次 bootstrap/backfill 仍依赖 import-time 执行；如果运行环境在 import 时数据库不可用，会记录异常并返回失败，但不会自动重试。后续如果要更强恢复能力，需要另行设计重试触发点。
- 迁移过程中发现多条既有生命周期测试仍绑定旧的 sliding TTL / `initial_expiry_days` 语义，已在本任务内同步改为冻结硬上限 + strength 驱动软过期的断言；当前没有遗留 blocker。

2. 本次验证中出现一次 SQLAlchemy declarative class reload 警告（测试里触发 `auto_memory` reload 时的重复类名提示），不影响断言结果；后续若继续保留 import-time reload 测试，可接受该噪声。
- 本轮返工确认上一版 task 2 实现有两个具体偏差：soft expiry 错写成 `ceil(strength)` 天，以及 `new_strength` 未在派生 soft expiry 前 clamp 到 100；现已连同对应测试断言一起修正，无新增 blocker。

6. 这次 cleanup 行为补丁只覆盖行为缝，不展开 task 5 的 maintenance 阶段重构；后续若需要真正的 maintenance candidate lifecycle 收敛，可在同一 seam 上继续。
- 任务 4 无新增 blocker；整文件 pytest 仍出现既有 `importlib.reload(auto_memory_module)` 触发的 SQLAlchemy declarative reload warning（`MemoryExpiry` 重复类名），不影响当前断言与结果。

- 本次 task 5 没有新增 blocker；验证中仍只有既有的 SQLAlchemy declarative reload warning，来自 import-time reload 测试，不影响结果。
- 本轮 task 6 验证同样只有既有的 SQLAlchemy declarative reload warning（`test_import_initialization_calls_create_all_non_intrusive` 触发），`py_compile`、定向 pytest、全量 pytest 全部通过；该噪声不需要额外处理。
