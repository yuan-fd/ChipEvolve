# P4 RTLScout 插件验收

status: completed
captured_at: 2026-08-04

## 结果

固定 RTLScout `87a00edf...` 已通过标准子进程 Adapter 接入 Workflow Runtime。官方离线模型 `fake:simple_adder_pass` 在 aarch64 本机真实执行了 Agent 循环、Verilator 正确性验证和 Yosys 成本评估；生成 RTL 随后作为带 SHA-256 的新 TaskSpec 输入，由同一 Runtime 提交给固定 2D ORFS，真实完成 Nangate45 RTL→GDS。

这证明的是 Adapter、官方离线 Agent 路径、EDA gate 和组合链，不是付费/真实 LLM 能力。没有用户提供的 provider 凭据和付费调用预算，因此真实 LLM 项明确保留为 external blocker。

## 关键证据

- 隔离环境：Python 3.12.4、Verilator 5.040、Yosys 0.63；版本/源码哈希见 `integrations/rtlscout/environment.lock.json`。
- RTLScout run `e3301eb...`：3/3 correctness checks，310 transistors；4 artifacts、4 metrics、13 events。
- 生成 RTL：82 bytes，SHA-256 `4b4fe1e2f61672c0fb5b440e4361881a07cbc0c51172d1a0fee1acc5c2010e7d`。
- ORFS run `c1fff2b2...`：6/6 stages、17 artifacts、7 metrics、29 events。
- GDS：164,296 bytes，SHA-256 `64ea359ed4307aa79e51d2f5259270f6501be74bfc3dbf1b78263d9011f584b3`。
- 组合验收用时 521.94 秒；RTLScout 固定源码及 submodule 前后 clean/不变。
- live SQLite 位于节点本地 `/tmp`；checkpoint 快照 SHA-256 `50c8eab252f5e825456bd3324ccba97a7143af6a9478e6c1b9917cc5233a9039`。

机器可读摘要见 `P4_RTLSCOUT_ACCEPTANCE.json`，完整原始验收目录为被 Git 忽略的 `runs/p4-acceptance-20260804-03/`。失败的 `-01`、`-02` 目录保留了 venv symlink 根因证据，成功记录没有覆盖旧失败。

## 安全与权威边界

- TaskSpec 只保存 provider/model 和所需凭据变量名，从不保存 token；真实 provider 缺凭据时 fail closed。
- Adapter 只接受 allowlist provider、仓库内 benchmark、1–100 steps，并校验固定 commit。
- 只有 workspace 内 allowlist 产物会登记；成功结果仍必须 `passed=true` 且存在非空 `best_design/design.sv`。
- RTLScout 不写平台状态。RTLScout 和 ORFS 是两个独立 Runtime child runs；组合 helper 不直接写终态。
- `.tools/` 被 Git 忽略，系统 Python 与共享 ORFS/OpenROAD/Yosys/PDK 没有被修改。
