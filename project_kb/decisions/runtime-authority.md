# Runtime 权威边界

status: validated_rule
source: `docs/adr/ADR-0001-scheduler-authority.md`

Agent 可以规划实验、参数和停止建议；只有 Workflow Runtime 可以启动/终止进程并写 Run、StageRun、Attempt 的权威状态。
