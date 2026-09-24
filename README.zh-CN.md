# Agentic EDA 执行底座

[English README](README.md)

本仓库是 Agentic EDA 的执行底座。Agent 负责判断应该做什么实验；执行底座负责把这个决定变成一个可持久化、工作区受控、可查询的运行；Toolkit Adapter 负责知道某一种 EDA 工具或工具链应该怎样启动。

三者的边界是：

```text
Agent
  → 意图、能力、参数、脚本、代码或 patch
Execution Foundation（执行底座）
  → 工作区、进程、资源、生命周期、证据和 provenance
Toolkit Adapter
  → 工具命令、环境、解析器和产物提取
EDA 工具
```

这不是一个把 Agent 限制成“只能提交固定任务”的流水线系统。Agent 可以选择 Toolkit 能力、组合多个任务、生成 TCL/Python 脚本、提交源码 patch，也可以选择直接使用低层脚本能力。唯一的约束是：实际工具进程必须进入执行底座管理的生命周期。

Query-Agent 应保持轻量：它负责理解问题、规划查询、比较实验并解释结果。执行底座负责把任务可靠地排队、执行、授权和留存证据；Toolkit 负责具体 EDA 工具、脚本、环境和领域报告。这样 Agent 的计算预算用于推理和决策，而不是重复编写进程监管、文件搬运或工具启动代码。

## 项目做什么

对于每个 Task 或 Plan，平台可以：

- staging 输入并记录 SHA-256；
- 创建独立 attempt workspace；
- 启动外部 Toolkit 进程；
- 管理超时、取消、lease 和有界重试；
- 记录日志、进度、资源和状态转换；
- 登记带 provenance 的产物和指标；
- 区分平台失败、Toolkit 失败和工具失败；
- 失败时保留平台请求、结果、日志和输入清单等可核验现场；
- 通过 kernel API 和 Agent Plan API 查询完整证据。

当前目标有意保持克制：单机、Agent 编写的串行任务清单、本地 SQLite/文件系统状态，以及外部 Toolkit 进程。这里不是 Kubernetes 调度器，不是通用 DAG 引擎，也不替 Agent 做 EDA 策略决策。

## 为什么需要它

EDA 状态分散在 RTL、网表、DEF/GDS/ODB、报告、日志、工具数据库和指标中。Agent 可以设计实验，但不应该每次调用工具时都重新处理工作区、进程监管、产物复制和失败记录。

本项目把这些工程管理工作集中起来，同时把 EDA 决策留给 Agent，把领域解释留给 Toolkit 或 evaluator。

## 从零到一次真实运行

当前支持 Linux、Bash 和 Python 3.9 或更高版本。先运行：

```bash
git clone <repository-url> openroad-platform-v2
cd openroad-platform-v2
tools/install-local.sh
# 安装脚本只安装平台包；如果环境没有 pytest，再安装测试工具
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest -q
```

真实 ORFS 运行需要另外安装 ORFS Toolkit，并提供 ORFS、OpenROAD、Yosys 和 KLayout 路径。平台仓库不会把这些 EDA 工具复制进来。操作步骤见 [`docs/AGENT_OPERATIONS.md`](docs/AGENT_OPERATIONS.md)，完整 GCD 验收见 [`docs/internal/GCD_ACCEPTANCE_2026-09-18.md`](docs/internal/GCD_ACCEPTANCE_2026-09-18.md)。

启动脚本是本机开发配置，只监听 `127.0.0.1`，并使用 `--no-auth`。不要把这个配置直接暴露到网络。团队服务器应使用 SSH/VPN、已有访问控制或经过审查的反向代理。这里的工作区和进程控制是生命周期边界，不是操作系统安全沙箱；Toolkit 必须被信任，且可能访问部署环境允许它访问的主机资源。

## 代码地图

更完整的说明在 [`docs/ARCHITECTURE_OVERVIEW.zh-CN.md`](docs/ARCHITECTURE_OVERVIEW.zh-CN.md)。主要目录如下：

| 目录 | 职责 |
| --- | --- |
| `contracts/` | Task、result、input、artifact、resource 等基础契约 |
| `core/runtime/` | 持久化状态、attempt 工作区、worker、进程和资源生命周期 |
| `core/registry/` | 外部 Toolkit 的发现、manifest 校验和准入 |
| `core/provenance/` | 产物和指标之间的证据关系 |
| `core/evaluator/` | 领域 evaluator 的边界 |
| `core/client/` | kernel API 的轻量客户端 |
| `gateway/` | HTTP 组合根和 API 路由 |
| `apps/plan_executor/` | Agent 编写的有序计划和产物传递 |
| `apps/query_agent/` | 证据优先的 design/run/artifact 查询和原始数据汇报 |
| `plugins/` | 仓库内最小示例；真实 EDA Toolkit 可以放在仓库外 |
| `guardrails/` | 可执行的架构规则和故意违规样例 |

## 如何接入 Toolkit

Toolkit 是外部进程包，不是导入 kernel 的 Python 模块。它需要提供 manifest、adapter 入口和 artifact 规则。Adapter 接收 `--request <path>` 与 `--result <path>`，在工作区中调用自己的工具，并写出协议结果。产物哈希由平台从磁盘重新计算。

可以先运行 [`examples/research-toolkit/`](examples/research-toolkit/)，再阅读：

1. [`docs/ARCHITECTURE_OVERVIEW.zh-CN.md`](docs/ARCHITECTURE_OVERVIEW.zh-CN.md)：平台地图和边界；
2. [`docs/PLUGIN_PROTOCOL.zh-CN.md`](docs/PLUGIN_PROTOCOL.zh-CN.md)：实现协议；
3. [`CONTRIBUTING.zh-CN.md`](CONTRIBUTING.zh-CN.md)：接入步骤；
4. [`docs/FAQ.zh-CN.md`](docs/FAQ.zh-CN.md)：常见问题。

为了兼容现有 wire contract，字段仍叫 `plugin_id`；在架构语义上，它表示外部 Toolkit 包的身份。Capability 名称及其参数由 Toolkit 自己解释，执行底座只传递和记录。

## 当前证据和限制

当前仓库已经验证：

- Agent Plan → kernel → worker → 外部 ORFS Toolkit → GCD `finish` 的完整链路；
- Agent 生成的 script、patch、build 和 benchmark 任务；
- 不同 Toolkit 之间的 artifact 传递；
- cancel、timeout、retry、worker 丢失和幂等行为；
- 旧 adapter 进程的 lease fencing、统一有效资源请求、用户可见范围过滤和
  artifact 授权下载；
- run 可记录 design revision、experiment 及平台根据实际输入字节计算的
  `input_manifest_sha256`；健康接口会报告 worker presence 和队列年龄；
- 失败运行会把可用的请求、结果、日志、输入/协议清单保存为哈希校验的
  runtime evidence，便于定位问题而不必重新执行；
- 可按 `Design → Revision → Run → Attempt/State → Artifact/Metric` 查询，
  并一键生成本机 ZIP 证据包；不引入跨机器存储或复杂调度；
- 最新全量测试与 Guardrails 结果以 CI 为准；当前 Guardrails 为 `40 passed`，应用 smoke 已纳入默认 pytest。

协议当前仍是 `v1alpha1`。跨机器 Toolkit preflight、完整 Design→Revision→Experiment
实体模型、操作系统硬资源限制、远程对象存储和多节点调度仍是后续工作；当前
多用户入口和 artifact/input/run 的授权检查已经闭合，但工作区仍不是 OS 安全沙箱。

## 文档入口

| 读者 | 从这里开始 |
| --- | --- |
| 新人、老师或项目负责人 | 本 README 或 [English README](README.md) |
| 接入 Toolkit 的开发者 | [架构总览](docs/ARCHITECTURE_OVERVIEW.zh-CN.md) |
| Toolkit 作者 | [Toolkit 协议](docs/PLUGIN_PROTOCOL.zh-CN.md) |
| 代码贡献者 | [贡献者指南](CONTRIBUTING.zh-CN.md) |
| 想跑示例的人 | [Research Toolkit 示例](examples/research-toolkit/README.zh-CN.md) |
| 遇到问题的人 | [FAQ](docs/FAQ.zh-CN.md) |

## License

见 [`LICENSE`](LICENSE)。
