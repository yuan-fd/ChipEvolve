# P6 里程碑：Campaign 与恢复查询

captured_at: 2026-08-04
status: completed

- CampaignStore 只保存实验组元数据和 member→run 映射，RuntimeStore 仍是执行状态唯一权威。
- 单机绑定用文件锁；崩溃窗口通过唯一 task_id 查回 Runtime run，实现不重复提交。
- `max_parallel` 会扣除已 active 成员；测试证明并发 2 和 workspace 隔离。
- worker lease 过期产生 `lost` Attempt，新 Attempt 重试；失败 workspace 与事件保留。
- API 新增 Runtime/Campaign 查询和取消；API/Web 不启动执行进程。
- 证据：`docs/evidence/P6_CAMPAIGN_ACCEPTANCE.{md,json}`；全量 66 passed。
