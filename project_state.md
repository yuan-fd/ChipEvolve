# OpenROAD Platform — 工作记忆

project_id: openroad-platform
phase: P0：基线封存、安全治理与事实定版
current_subgoal: 建立可回滚基线，固定三平台版本，完成 P0 文档与验收
progress: 10%
last_updated: 2026-08-04

## 已完成
- [done] 项目骨架挂载到正确路径 ~/openroad-platform/
- [done] 知识库分区创建 (project_kb/decisions, project_kb/pitfalls)
- [done] 服务器环境全面盘点

## 进行中
- [ ] LLM Agent 层架构设计（双模型：GPT-5.6-sol 主力 + deepseek-v4-pro 推理）
- [ ] 阅读 packages/ 源码，理解现有 API/job/workspace 协议
- [ ] Optuna 调参模块接口设计（暂不写代码）

## 待办
- [ ] 创建 openroad-platform skill（Hermes 过程记忆）
- [ ] RAG 知识库首次入库
- [ ] ReAct 纠错流程设计
- [ ] 代理配置（Clash 就绪后）

## 阻塞项
（无）

## 现有平台总结（2026-07-27 服务器盘点）
- Python 3.9.9, ORFS commit 51ad1231a, OpenROAD 26Q1, Yosys 0.63
- 15 个测试全通过，真实 ORFS 合成 8.5 秒
- 完整 6 阶段流程跑通（synth→route, PSM 因 PDN 简化设计预计失败）
- GDS 导出通过：53,860 bytes, SHA-256 已验证
- PDK: Nangate45(16 designs) + Sky130HD(7 designs)
- 已有 Circuit Studio(NL→RTL) + RTL-to-GDS Flow(web UI + scheduler)
- Flow optimization / Spec-to-GDS: 未实现，正是我要做的
- 连通性: 无代理，需 Clash 配置后才有外网

## 活跃约束
- 所有产出在 ~/openroad-platform/ 下
- GPT-5.6-sol 当主力，deepseek-v4-pro 处理推理/架构/QA
- 架构决策需双模型协作讨论后由用户审核
- 代理凭据只允许通过环境变量或项目外私有配置注入；项目记忆、日志和 Git 不保存凭据值

## handoff_anchor
P0 已获用户批准；先完成安全基线、事实审计、版本锁定与 Proposed ADR，再进入 P1。
