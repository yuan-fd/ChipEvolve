# 下一步

updated_at: 2026-08-04

## 当前首项动作

P1 已完成，等待用户验收。本轮不自动进入 P2，因为 P2 将首次把现有 ORFSRunner 迁入新 Runtime，并包含真实 Nangate45 RTL→GDS 验收，需要单独确认运行预算和工具链只读边界。

验收后先编写 `tasks/phase-2.md`，明确 legacy API 共存、ORFS plugin manifest、ToolchainSnapshot/配置哈希、artifact store key、真实运行预算和回滚方式，再开始实现。

## 重大问题暂停条件

P2 若需要修改共享 ORFS/OpenROAD/PDK、改变 Runtime 唯一权威、把旧数据库原地迁移，或无法维持旧 API 回归，必须暂停请求用户决策。
