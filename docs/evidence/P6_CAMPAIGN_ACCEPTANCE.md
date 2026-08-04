# P6 Campaign、恢复与查询验收

status: completed
captured_at: 2026-08-04

## 结论

平台已在 Workflow Runtime 上增加持久 Campaign 控制层。Campaign 只负责成员、预算和调度窗口；每个 member 的真实状态、Attempt、产物和事件仍由 RuntimeStore 唯一写入。

## 已证明行为

- Campaign 和成员 TaskSpec 独立持久化，member 与 run 一一绑定。
- 单机文件锁串行化绑定窗口；若进程在 Runtime submit 后、member bind 前崩溃，重启按唯一 task_id 找回原 run，不重复提交。
- 两个 500 ms 独立 Adapter 在 `max_parallel=2` 下并行完成，且 workspace 路径分别落在各自 run/stage/attempt 下。
- 已存在 active member 时会从并发额度扣除，不会超发。
- 模拟 worker lease 过期后，Attempt 1 保留为 `lost`，Attempt 2 在新 workspace 成功；没有覆盖失败记录。
- queued Campaign 整体取消后所有成员均成为 `cancelled`。
- API/Web 进程只查询或写取消请求，不拥有执行子进程。

## 查询入口

- `GET /api/runtime/runs`、`GET /api/runtime/runs/:id`。
- `GET /api/campaigns`、`GET /api/campaigns/:id`。
- Runtime run 和 Campaign cancel POST。
- Web 首页展示 Campaign 查询契约；现有 flow monitor 保持基础状态/阶段/证据视图。

## 验证

```text
python -m pytest -q
66 passed
```

测试使用真实独立子进程、两个 SQLite live DB、并发线程、租约时间推进和实际文件产物。P6 不重复跑 EDA；P2/P4/P5 已分别证明 Runtime 对真实 ORFS 的执行、取消边界和 GDS 产物链。
