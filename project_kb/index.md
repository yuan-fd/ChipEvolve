# 知识库索引

## 分区

| 分区 | 内容 | 规则 |
|------|------|------|
| decisions/ | 技术决策 + 备选方案 + why | 原始证据只读，AI 改需确认 |
| pitfalls/ | 踩坑记录 + 复现条件 | 每次失败后自动追加 |
| specs/→~/openroad-platform/docs/ | 架构/接口定义 | 已有 architecture.md, validation.md |
| eferences/ | 论文/外部资料 | 可追加不修改 |

## 已有文档（不重复建）
- docs/architecture.md — 平台架构基线
- docs/validation.md — 2026-07-24 验证记录
- docs/migration-plan.md, migration-inventory.md
- workflows/*/README.md — 各工作流规划

## 核心约束
- 原始证据不允 AI 随意修改
- 快照只追加（Git commit 式）
- 按需检索，不全部灌 prompt
