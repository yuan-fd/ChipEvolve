# 仓库审计

审计日期：2026-08-04

## 基线状态

- 项目根：`~/openroad-platform`
- 分支：`main`
- 初始安全基线 commit：`e788a362e27318ee6950db1793bd47040e577d49`
- remote：未配置；P0 不创建、不 push。
- 运行目录 `var/`、第三方源码 `.external-src/`、缓存和日志均被 Git 忽略。
- 原工作树中的源代码、测试、参考计划和报告已纳入本地基线；明文代理凭据在首个 commit 前已移除。

## 已实现能力

| 能力 | 当前实现 | 结论 |
| --- | --- | --- |
| 契约 | ORFS 专用 `RunRequest/RunResult` | 可作为迁移输入，不是通用 TaskSpec |
| 队列 | SQLite `jobs` + `job_events` | 可持久化，但无 StageRun/Attempt/lease recovery |
| worker | 独立进程 | 边界正确，但硬编码 ORFSRunner |
| 进程控制 | ProcessGuardian | 支持静默超时、取消、进程组清理，应复用 |
| ORFS runner | 六阶段、硬产物门禁、GDS 恢复 | 真实可用，应迁移成首个插件 |
| 工具链配置 | `ToolchainConfig/Catalog` | 尚未接入实际 ORFSRunner |
| evidence/gray-box | 多个版本化分析模型 | `write_run_evidence` 未接入主链，存在旧路径假设 |
| API/Web | 本地 demo | 查询/提交可用；生成和综合仍可能在 HTTP 进程中执行 |
| 插件系统 | 仅 README 边界 | 无 Registry、Manifest、JSON 子进程协议 |

## 自动化测试事实

`python3 -m pytest -q` 在 P0 审计前通过 22 项测试。覆盖契约 round-trip、队列持久化与取消、静默进程超时、进程组清理、模拟 ORFS 六阶段、产物门禁、GDS 恢复、基础分析、可视化和 Web 提交。

主要缺口：没有通用插件契约、状态机迁移、worker lease/recovery、retry attempt、artifact 下载 allowlist、HEAD 路由、工具链 catalog 主链和高级 evidence 主链测试。

## 现存运行证据

SQLite 中有 6 个 job：2 succeeded、4 failed。登记了 45 个产物；P0 对所有登记路径重新计算大小与 SHA-256，结果为 45 个匹配、0 缺失、0 不一致。

其中：

- 1 次 synthesis-only 成功；
- 1 次完整 synth→finish 六阶段成功，`implementation_valid=true`、`gds_complete=true`；
- 1 次 finish 失败但 GDS 恢复成功，平台保持 `implementation_valid=false`；
- 其余失败分别停在 floorplan 或 place，并保留已完成阶段和错误证据。

这些运行数据位于被忽略的 `var/`，不进入 Git，不在 P0 删除或迁移。P2 必须使用新运行重验平台真实性，不能只引用历史摘要。

## 与总施工方案的差异

原路线把 P0/P1/P2 视为从零开始。真实状态是：P1 和 P2 的部分原型已存在，但没有通用数据模型、插件边界和正式基线。因此后续采用迁移加固，而不是重写：

1. P1 建立版本化契约、Runtime、Registry 和 Attempt 模型。
2. P2 将现有 ORFSRunner 迁入新边界并做真实回归。
3. P3 之后再接入外部插件。

## 高优先级风险

1. 当前 `Worker` 直接构造 ORFSRunner，外部插件无法接入。
2. retry 没有独立 Attempt，失败历史可能无法表达。
3. heartbeat 没有租约与失联恢复语义。
4. ORFSRunner 使用完整宿主环境，而已实现的受控 Toolchain environment 未接线。
5. 高级证据代码与主链脱节，且含原型遗留路径名称。
6. API 没有认证，返回内容含本机路径，只能用于可信内网。
7. AgenticPD 未声明许可证；TaiWei 工具链与内部 ORFS 基线不同。

## P0 结论

现有代码是可信的 2D demo 基线，而非通用自演化平台。核心可复用资产是 ProcessGuardian、ORFS runner、SQLite queue、分析器和 UI 原型；P1 必须先稳定通用契约和唯一 Runtime 权威。
