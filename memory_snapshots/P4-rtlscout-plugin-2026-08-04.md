# P4 里程碑：RTLScout 插件与 RTL→ORFS

captured_at: 2026-08-04
status: completed

- 标准插件：`rtlscout@1.0.0`，capabilities `agent.rtl.generate`/`agent.rtl.optimize`；固定上游 `87a00edf...`。
- 上游顶层写 Python >=3.10，但固定 Spire `448f393...` 实际要求 >=3.12；项目内从源码固定 Python 3.12.4，并固定 Verilator 5.040。
- 官方 `fake:simple_adder_pass` 真实通过 3/3 仿真与 Yosys 评估（310 transistors），生成 RTL SHA `4b4fe1e2...`。
- 该 RTL 由 Runtime 作为哈希输入提交 ORFS，Nangate45 6/6 成功，GDS SHA `64ea359e...`。
- 真实 LLM 未执行：无用户提供凭据/付费预算；不得把 fake 冒充真实 LLM。
- venv executable 必须保留符号链接路径，不能 `.resolve()` 到基础 Python，这是已验证坑点。
- 证据：`docs/evidence/P4_RTLSCOUT_ACCEPTANCE.{md,json}`；完整 ignored run `runs/p4-acceptance-20260804-03/`。
