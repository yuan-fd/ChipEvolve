# P3 三平台兼容性准入证据

status: completed
captured_at: 2026-08-04

## 结论

三个固定源码版本均可进入“版本化子进程 Adapter”建设，但都是条件准入。该结论只批准接口开发，不把入口解析、mock 或单元测试称为真实 LLM/EDA 成功。

| 平台 | 固定 commit | 许可证 | ARM/入口实测 | 准入结论 |
| --- | --- | --- | --- | --- |
| RTLScout | `87a00edf...` | BSD-3-Clause-Clear | ARM wheels 可获得；系统 Python 3.9 不满足 `>=3.10`，入口因未安装 `dotenv` 退出 1 | 条件准入；P4 可做标准 Adapter/fake，真实 LLM 需独立 Python、依赖和凭据 |
| AgenticPD | `4322a25c...` | 官方仓库未声明 | 临时副本 `make check` 退出 0：87+17+167 tests，三个 CLI help 通过 | 条件准入；只允许黑箱调用和 proposal 转换，不复制/修改/再分发源码 |
| TaiWei-Pin-3D | `db201367...` | BSD-3-Clause | `run_experiments.py --help` 退出 0 | 条件准入；P8 必须隔离固定 3D 工具链后再跑 gcd |

机器可读记录见 `P3_PLUGIN_ADMISSION.json`。

## 环境事实

- 主机为 aarch64/openEuler 22.03，系统 Python 3.9.9；未发现 Python 3.10+、Conda、micromamba 或 uv。
- Docker CLI 存在，但当前用户不能连接 daemon，因此容器不是本机可用的执行后端。
- 已验证 Python 3.11/aarch64 可解析并下载 RTLScout 的主要依赖 wheels，包括 numpy、matplotlib、pydantic-core、jiter 与 contourpy。这证明主要 PyPI 二进制依赖有 ARM 构件，不等于完整 RTLScout 已安装或 EDA smoke 已通过。
- 三个 `.external-src` 固定仓库在准入前后均保持 clean，平台没有修改第三方源码。

## 输入、输出与隔离边界

### RTLScout

- 入口：`run_benchmark.py`。
- 最小输入：benchmark、`provider:model`、运行预算；真实模型凭据只能从环境注入。
- 权威输出：`result.json` 与 `best_design/`，Adapter 只登记 workspace 内 allowlist 产物。
- P4 门槛：协议/fake smoke 和 RTL→ORFS 组合链必须通过；若没有合适 Python/凭据，真实 LLM 明确记为外部阻塞。

### AgenticPD

- 入口：`main.py`、`multi_agent_gwtw.py`。
- 输入：基线、阶段报告、候选/预算；输出需转换为 `ExperimentPlan`/`ActionProposal`。
- 上游内部调度状态不是平台事实。只有 Workflow Runtime 能创建、启动、取消和结束 child run。
- 在许可证澄清前，平台 Adapter 不 import、不 vendor、不派生上游代码。

### TaiWei-Pin-3D

- 入口：`run_experiments.py`，第一版整体黑箱调用。
- 预期输出：`openroad_eval.json`、`final_summary.txt`、GDS、DEF/ODB、3D views 和日志。
- 官方固定 ORFS-Research `568eb04...`、bundled OpenROAD `305d3ba...` 与平台 2D 基线不同，必须在独立 prefix/workspace 中运行。

## 实测命令与退出码

```text
AgenticPD temporary copy: make check                         exit 0
  schemas/trial.py                                           87/87
  orchestrator.py                                            17/17
  multi_agent_gwtw_orchestrator.py                          167/167
  main.py, multi_agent_gwtw.py, session_visualize.py --help  passed

RTLScout temporary copy: python3 run_benchmark.py --help     exit 1
  ModuleNotFoundError: No module named 'dotenv'

TaiWei temporary copy: python3 run_experiments.py --help     exit 0
```

## 未被本阶段证明的能力

- RTLScout fake/simple_adder 和真实 LLM 均尚未在本机合适 Python 环境完成。
- AgenticPD 尚未执行真实模型或真实 ORFS 候选对比。
- TaiWei 尚未有固定 3D toolchain，故尚未执行 gcd 真实 3D 流程。

这些是后续阶段的显式验收项，不影响 P3 兼容性准入完成。
