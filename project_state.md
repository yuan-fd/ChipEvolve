# OpenROAD Platform — 工作记忆

project_id: openroad-platform
phase: P2：ORFS 标准插件迁移
current_subgoal: 接通 ORFS adapter/ToolchainSnapshot 并完成新 Runtime 真实 RTL→GDS
progress: 5%
last_updated: 2026-08-04

## 已确认事实

- 初始安全基线：`e788a362e27318ee6950db1793bd47040e577d49`。
- 主机：ARM64/openEuler 22.03；Python 3.9.9。
- 2D 工具链：ORFS `51ad1231...`、OpenROAD `63ed2e0...`、Yosys `d3e297f...`。
- 平台已有独立 worker、SQLite queue、ProcessGuardian、六阶段 ORFS runner、分析与 Web demo。
- 测试基线：22 tests passed。
- 历史数据库：6 jobs；45 个登记产物大小与 SHA-256 全部匹配。
- 三个官方插件源码已按 `integrations/plugins.lock.json` 固定到 detached commit。
- P1 实现提交：`e750370d0a95c708cb5f9a0ee297dcb0de609db6`；全量 49 tests passed。

## 架构定版

- 架构草图定义 Agent 产品拓扑。
- Flow Agent/AgenticPD 负责智能实验规划；Workflow Runtime 是进程、资源和持久状态唯一权威。
- 首版插件使用独立环境与版本化子进程 JSON 协议。
- DB 保存状态/关系/摘要；Artifact Store 保存原始大文件。
- retry 创建新 Attempt，不覆盖失败证据。
- Runtime DB schema 与 Event 均固定为 v1；未知版本和未版本化已有 DB 会拒绝打开。
- legacy `jobs` 只能只读投影；不得把 unknown provenance 猜测补齐。
- P1 adapter 面向固定且审查过的代码，不是恶意代码 OS 沙箱。

## 风险与后置门槛

- RTLScout 要求 Python >=3.10，ARM 源码兼容性在 P3 验证。
- AgenticPD 官方仓库未声明许可证；澄清前禁止复制或再分发源码。
- TaiWei 绑定独立 ORFS-Research/OpenROAD commit，不能覆盖内部 2D 工具链。
- 现有高级 evidence/toolchain 代码未接入主链，P1/P2 迁移时需去除旧路径假设。

## 活跃约束

- 所有平台产出在 `~/openroad-platform/`；第三方源码缓存不进入 Git。
- 凭据只通过环境或项目外私有配置注入。
- 不修改共享 ORFS/OpenROAD/PDK，不删除 `var/` 原始证据。
- 不 push、不部署；真实 EDA 与 LLM 必须由阶段任务明确授权和预算。

## handoff_anchor

P0 commit 为 `afdca1ef...`；P1 实现 commit 为 `e750370...`。读取 `memory_snapshots/P1-runtime-core-2026-08-04.md` 恢复事实；下一步等待 P2 明确授权与任务边界。
