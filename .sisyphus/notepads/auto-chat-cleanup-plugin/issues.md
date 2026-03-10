# Issues - Auto Chat Cleanup Plugin

## Known Gotchas
- 必须排除当前活跃对话,避免删除正在使用的对话
- 必须排除受保护对话,避免误删用户重要对话
- 删除失败时不能中断整个 `outlet`,必须继续处理后续候选
- 插件必须返回原始 `body`,不能修改响应内容
