# 新服务器项目基线

## Checkout

- remote：`https://github.com/yuan-fd/ChipEvolve.git`
- branch：`main`
- HEAD：`5343acea0a916bd5b1542672bbd9b569566c9483`
- 初始工作区：干净；没有覆盖已有用户修改。

## 安装

`tools/install-local.sh` 首次在 Rocky 8 的 Python 3.12 venv 中因缺少 `setuptools` 失败；在项目 `.venv` 安装 `setuptools`/`wheel` 后，脚本成功完成 9 个 editable package 安装。系统 Python 没有 pytest，因此检查工具只安装到项目 `.venv`，没有修改系统 Python。

随后修复了安装入口：脚本恢复可执行位，并在 venv 内自动 bootstrap `setuptools/wheel`。用全新临时 venv 复跑 `OPENROAD_PLATFORM_VENV=<tmp> tools/install-local.sh` 成功；不要求修改系统 Python。

## 测试与门禁

| 命令 | 结果 | 说明 |
| --- | --- | --- |
| `.venv/bin/python -m pytest -q` | 初始基线 `467 passed, 1 skipped, 3 failed`；兼容性夹具和 Toolkit smoke 加入后 `471 passed, 1 skipped` | Rocky 8 的 SQLite 3.26 不支持 `ALTER TABLE ... DROP COLUMN`；测试夹具改为兼容的表重建，未改变运行时代码 |
| `.venv/bin/python -m pytest -q guardrails` | `40 passed` | 架构边界和规模门禁通过 |
| `.venv/bin/mypy ...` | `RC 0` | contracts/core/gateway/apps 正式源码通过 |
| `.venv/bin/ruff check ...` | 未通过 | 当前 Ruff 0.16 报 94 个历史/测试/故意夹具问题，未全局 autofix |
| `.venv/bin/black --check ...` | 未通过 | 83 个文件有历史格式差异；全局格式化会触碰负面夹具和规模门禁 |
| `git diff --check` | 应在提交前复跑 | 本阶段不提交生成 workspace、日志或许可证值 |

## 已修复/新增

- `PluginManifest.toolkit`：原样携带 Toolkit 的 executable、environment、input/output、script、artifact/metric、retry、preflight 和 version capture 声明；底座不解释领域语义。
- registry/manifest 保留 toolkit metadata；adapter 仍是外部进程，底座不解释这些字段。
- `plugins/cadence-innovus/`：最小 `preflight`/`script` Adapter，捕获工具链和日志，按 license/tool launch/tool error 分类失败。
- 服务器审计、工具链复现和基线报告。

## 不应误读

- ORFS 独立 GCD 成功不等于仓库已有 ORFS plugin 已在本机完成接入；本仓库只保留通用底座和商业 Innovus 最小 adapter。
- Innovus 脚本成功不等于 placement/CTS/route 策略或 QoR 达标。
- 许可证 checkout 结果只在当次 preflight 有效，不能写成永久可用承诺。
