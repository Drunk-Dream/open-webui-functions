# Decisions - Auto Chat Cleanup Plugin

## Architectural Choices
- 过滤顺序固定:仅当前用户 → `updated_at desc` → 排除当前活跃对话 → 排除受保护对话 → 年龄候选 → 数量候选 → 并集去重 → 调用删除接口
- 失败策略固定:单条删除失败只记录日志并继续后续候选
- 保留数量固定按"可删除池"计算,不把受保护对话与当前活跃对话纳入超量窗口
- `updated_at` 缺失或非法时保守跳过并记录 warning

## API Choices
- 使用 `Chats.delete_chat_by_id_and_user_id(id, user.id, db=db)` 删除对话
- 不通过 HTTP 调路由,不 direct SQL delete

- `outlet()` 使用 `Chats.get_chats_by_user_id(user.id).chats` 拉取候选数据, 再将当前 `chat_id` 与 `int(time.time())` 传给 `_cleanup_chats()`
- 删除流程在 `get_db()` 上下文中执行, 并对单条删除异常记录 warning 后继续后续候选
- 删除状态上报固定在 `_cleanup_chats()` 末尾执行, 仅针对实际成功删除的对话数量发出一次完成状态
- 单条删除异常日志级别固定为 error, delete 返回 False 维持 warning
