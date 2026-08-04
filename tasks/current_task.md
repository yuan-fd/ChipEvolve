# 当前任务：P0 基线封存、安全治理与事实定版

status: approved
phase: P0
approved_at: 2026-08-04

## 1. 用户原始目标

审计并搭建 OpenROAD 自演化平台。P0 完成仓库与环境基线、三平台版本锁定、架构和数据模型草案、长期记忆与阶段计划；P0 汇报后，如无重大架构或安全分歧，自动进入 P1。

## 2. 仓库与工作范围

- 仓库：`~/openroad-platform`
- 分支：`main`
- 当前没有可用历史 commit，P0 先建立本地可回滚基线。
- 第三方源码仅缓存于 `.external-src/`，不提交到平台仓库。

## 3. P0 验收标准

1. 移除项目记忆中的明文凭据，secret scan 不发现真实凭据。
2. 建立本地初始 Git commit；不 push、不创建远程仓库。
3. 固定 RTLScout、AgenticPD、TaiWei-Pin-3D 的官方仓库和 commit。
4. 产出环境、仓库、架构、数据模型、Charter、Roadmap、ADR、进度和 P1 计划。
5. `python3 -m pytest -q` 全部通过。
6. 现存登记产物的大小和 SHA-256 复核通过；不删除或修改 `var/`。
7. 审查最终 diff、Git 状态和范围合规性。

## 4. 允许修改范围

- `.gitignore`
- `project_state.md`
- `project_kb/**`
- `docs/**`
- `tasks/**`
- `memory_snapshots/**`
- `integrations/**`
- P0 为验证文档事实所必需的测试或脚本；若无需修改则保持不动。
- `.external-src/**` 仅可作为被忽略的只读第三方源码缓存。

## 5. 禁止范围

- 不修改或删除 `var/**`、现有运行日志和 EDA 产物。
- 不修改第三方源码内容，不读取 `.env`、token、密钥或私有代理配置。
- 不执行 sudo、系统配置、共享 OpenROAD/ORFS/PDK 修改。
- 不运行长时间真实 EDA、真实 LLM 或大规模并发实验。
- 不 push、不部署、不创建远程仓库。
- P0 不实现 Plugin Runtime、数据库迁移、Web 功能或 Agent 循环。

## 6. 已授权操作

- 下载三个官方仓库的固定源码版本。
- 移除项目文件中的明文凭据记录。
- 建立本地初始 commit。
- 在项目范围内创建 P0 文档和长期记忆结构。

## 7. 已知事实与待核验假设

### 已知事实

- 主机为 ARM64 openEuler 22.03。
- 当前平台已有独立 worker、SQLite 队列、ORFS runner、产物硬门禁和 Web demo。
- 当前测试基线为 22 项通过。
- 数据库有 6 次任务记录，其中有一次完整六阶段成功。
- 三个平台均有公开 GitHub 仓库，补充文档包含先前验证记录。

### 待核验

- 三个远程 commit 与报告描述的精确对应关系。
- RTLScout 在当前 ARM64 环境的源码级可安装性。
- AgenticPD 最新源码与平台唯一调度权威的适配边界。
- TaiWei 完整源码在当前工具链/PDK下的运行条件。

上述兼容性真实运行不属于 P0；P0 只形成可执行核验计划。

## 8. 必须读取的记忆与资料

- `project_state.md`
- `plan/平台架构草图.png`
- `plan/文档/OpenROAD自演化平台-总施工提示词与执行方案.md`
- `plan/文档/` 中三平台补充材料
- 现有 `README.md`、`docs/`、`packages/`、`tests/`、`workflows/`

## 9. 验证、资源与停止条件

- 测试：平台 pytest、Git/secret/范围审计、数据库与产物只读复核。
- 网络：仅用于固定官方 GitHub 源码；禁止执行远程脚本。
- 资源：P0 不运行真实 EDA；下载和静态审计保持有界。
- 同一根因最多尝试三次，每次必须改变措施。
- 若需要改变唯一调度权威、数据主键语义、安全边界或修改项目外共享工具链，立即暂停并提交方案。
