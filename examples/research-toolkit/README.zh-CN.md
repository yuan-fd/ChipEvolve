# Research Toolkit 示例

[English version](README.md)

这个目录是一个外部进程 Toolkit 示例。它故意不依赖 EDA 工具，目的是展示真正的 OpenROAD、Innovus 或 PrimeTime Toolkit 应该怎样接入，而不需要导入平台代码。

它提供两个由 Toolkit 自己定义的 capability：

- `script`：执行 Agent 提供的 Python 脚本；
- `patch_benchmark`：应用 Agent 提供的源码 patch，构建 baseline 和 candidate，运行两者并写比较报告。

平台不解释这些 capability 名称或参数，只负责 staging 文件、启动 adapter、监管进程、计算 artifact 哈希和保存证据。

## 运行端到端示例

在仓库根目录执行：

~~~bash
tools/install-local.sh
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest -q \
  apps/plan_executor/test_against_kernel.py \
  -k test_agent_generated_code_is_executed_and_measured
~~~

这是实际的本地集成测试，不是直接启动 `adapter.py`。它会创建 admission record，启动 gateway、worker 和 plan executor，提交 Agent 编写的计划，并通过平台边界查询结果。

三种参数化情况：

| 情况 | Agent 输入 | 预期证据 |
| --- | --- | --- |
| `script` | staged Python 脚本和 `parameters.seed` | report artifact 与 `result_value` metric |
| `patch_benchmark` | source、patch、compiler、patch tool | baseline/candidate 报告与 metric |
| `build_failure` | 产生非法 C 的 patch | Toolkit `build_error`、失败计划和日志 |

## 包结构

~~~text
research-example/
├── research-example.plugin.json
├── provenance.json                 可选
└── adapter.py                      接收 --request 和 --result
~~~

Manifest 声明协议版本、身份、架构、能力和 artifact 规则。Adapter 在 attempt workspace 中启动，读取 task，以相对 workspace 的路径写输出，并让 result 退出码和真实进程退出码一致。

平台拥有的 admission record 放在 Toolkit 目录之外。测试会自动创建它；部署真实 Toolkit 时，需要在配置的 admissions root 下放经过审核的记录。

## 改造成 EDA Toolkit

1. 把 `capabilities` 换成工具链真正提供的能力名；
2. 把领域参数放进 `task.inputs` 和 `task.parameters`；
3. 在 Toolkit 环境中解析工具可执行文件和库，不要把工具名加入 kernel；
4. 把工具输出转换成声明过的 artifact 和 metric；
5. 让每个 metric 指向支撑它的报告 artifact；
6. 为输入错误、工具失败、构建失败和评估失败返回结构化 failure；
7. 添加至少一个成功和一个失败的 Plan 级验收测试；
8. 在 provenance 中记录工具和 Toolkit revision。

准确拒绝规则见[协议](../../docs/PLUGIN_PROTOCOL.zh-CN.md)，职责边界见[架构总览](../../docs/ARCHITECTURE_OVERVIEW.zh-CN.md)。
