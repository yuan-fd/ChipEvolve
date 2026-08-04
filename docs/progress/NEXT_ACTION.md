# 下一步

updated_at: 2026-08-05

P8-Real 与平台系统验收已收口。下一路线是 P11：EDACraft / IC Craft 等新平台扩展。扩展必须复用 PluginManifest、TaskSpec、Workflow Runtime、Attempt 隔离、artifact SHA 和 Campaign 关系，不得引入第二状态权威。

首条恢复命令：

```bash
cd ~/openroad-platform && git status --short && sed -n '1,280p' docs/evidence/P8_REAL_ACCEPTANCE.md
```
