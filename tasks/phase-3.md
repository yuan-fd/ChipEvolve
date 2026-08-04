# P3 任务：三平台兼容性准入

status: completed
phase: P3
started_at: 2026-08-04
completed_at: 2026-08-04
base_commit: 6d9d93c

## 目标与允许范围

核验 `plugins.lock.json` 固定的 RTLScout、AgenticPD、TaiWei-Pin-3D 源码，记录 license、ARM/Python、入口、输入输出、工具链与 smoke 结论。允许修改 `docs/`、`tasks/`、`project_state.md`、`memory_snapshots/`；临时副本和 wheels 仅放 `/tmp`。

## 禁止范围

- 不修改 `.external-src`、共享 ORFS/OpenROAD/Yosys/PDK。
- 不运行付费 LLM，不读取凭据，不把 mock/CLI help 称为真实能力。
- AgenticPD 无许可证，不复制、修改或再分发其源码。
- 不覆盖平台 2D 工具链，不 push、不部署。

## 验收与停止条件

- 三仓库 HEAD 与 lock 一致且 clean。
- 每个平台有真实命令、退出码和条件准入结论。
- 同一根因最多尝试三次；需要凭据、Python 3.10+ 或固定 3D toolchain 时记录后续门槛，不盲目重试。
- `python3 -m pytest -q` 保持通过。

## 结果

三平台均为条件准入。AgenticPD `make check` 全过；TaiWei help 通过；RTLScout 的系统 Python smoke 因环境依赖/Python 版本受阻，但 ARM 主依赖 wheels 可获得。完整证据见 `docs/evidence/P3_PLUGIN_ADMISSION.{md,json}`。
