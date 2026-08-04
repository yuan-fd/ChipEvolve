# P6 任务：Campaign、并发、恢复与查询

status: completed
phase: P6
started_at: 2026-08-04
completed_at: 2026-08-04
base_commit: 38208ae

## 白名单

- `packages/scheduler/`：CampaignStore/Manager 与 Runtime 幂等恢复查询。
- `apps/api/`、`apps/web/`：Runtime/Campaign 只读查询和取消入口。
- `tests/test_campaign.py`、P6 任务、证据、进度和 memory snapshot。
- 验收生成物使用 `/tmp/openroad-platform-p6-*`。

## 禁止范围

- Web/API 不启动或持有 EDA 子进程；只有 worker/Runtime 执行。
- 不引入分布式队列、Kubernetes 或生产部署。
- 不把 legacy JobStore 状态写回 Runtime 表，不在 GlusterFS 放 live WAL。

## 验收门

- Campaign/member 映射和 TaskSpec 持久化；相同 task_id 恢复不重复提交。
- 有限并发不超过 Campaign 上限，每个 run workspace 隔离。
- worker lease 过期后创建新 Attempt，原失败证据保留。
- queued/running cancel、查询 API 与 Web 入口可测试。
- 全量 pytest、diff、越界审计通过。

## 预算与停止条件

- 测试 Campaign 最多 4 members、并发最多 2、单测试 30 秒。
- 同根因最多 3 次；不运行新的真实 EDA，因为 P6 验收针对控制面并复用 P2/P4/P5 真实执行证据。
