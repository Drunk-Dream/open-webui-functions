# Learnings - Auto Chat Cleanup Plugin

## Conventions
- 根目录单文件 Filter 插件模式,参考 `auto_memory.py`
- 使用 `Valves` / `UserValves` 配置
- 使用 `log()` 方法记录日志
- 使用 `emit_status()` 风格发送状态提示

## Patterns
- 仅在 `outlet` 执行,不引入定时任务或 `inlet` 双触发
- 清理范围仅限当前触发用户自己的对话
- 删除判定为 OR:超过最大闲置时间 **或** 超过最大保留数量
- 默认跳过受保护对话:`folder_id` 非空、`archived=1`、`pinned=1`
- 若实际删除数量大于 0,则发出一次状态提示;无删除时不提示

## Testing
- 使用 `pytest + pytest-asyncio`
- 沿用 `tests/conftest.py` 与 `tests/test_auto_memory_function_calling.py` 的 fixture/patch 风格
- 不接真实数据库,使用 mock

- `outlet()` 应保持返回原始 `body`, 并在临时对话/用户级开关关闭时尽早返回
- 本地 mock `Chats.get_chats_by_user_id()` 需返回带 `.chats` 属性的响应对象, 才能匹配 Open WebUI outlet 调用模式
- 状态提示应按实际删除成功数触发, 不能按候选数触发; `show_status` 关闭或无删除时必须静默
