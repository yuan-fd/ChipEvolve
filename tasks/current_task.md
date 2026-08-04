# 当前任务：P6 Campaign、并发、恢复与查询

status: completed
phase: P6
approved_at: 2026-08-04
started_at: 2026-08-04
base_commit: 38208ae
completed_at: 2026-08-04

## 结果

- 持久 Campaign/member 映射、并发上限、取消和重启幂等恢复已实现。
- Runtime/Campaign API 与基础 Web 查询入口已实现。
- 租约过期保留 lost Attempt 并由新 Attempt 成功恢复；全量 66 passed。

## 恢复锚点

- P5 提交：`38208ae`。
- P6 live SQLite 和 workspace 只使用 `/tmp`。
- 下一阶段：P7 NL→TaskSpec 与白名单有限 ReAct。
