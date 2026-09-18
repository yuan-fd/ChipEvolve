# 架构总览

[English version](ARCHITECTURE_OVERVIEW.md)

这份文档位于 README 和具体协议之间。README 说明项目是什么；协议说明字段和约束；本文件说明代码地图、数据流、边界以及新功能应该放在哪里。

## 一句话模型

Agent 负责实验意图和策略。Execution Foundation 负责可靠执行和证据。Toolkit 负责某一种工具或工具链的知识。底座不替 Agent 判断下一步应该 placement、route 还是 timing analysis。

```text
Controller / Flow / ECO / Query Agent
                |
                | plan、task、参数、脚本、patch
                v
        +----------------------+
        | Execution Foundation |
        | API + plan executor  |
        +----------+-----------+
                   |
                   | request/result、workspace、生命周期
                   v
        +----------------------+
        | Toolkit Adapter      |
        | OpenROAD / Innovus / |
        | PrimeTime / custom   |
        +----------+-----------+
                   |
                   v
                EDA 工具进程
```

Agent 可以选择 Toolkit、选择 capability、提供任意领域参数、生成脚本或提交 patch。Agent 不能绕过底座自行创建无人管理的工具进程或工作区。Workspace containment 和进程监管是执行控制，不是操作系统沙箱；Toolkit 属于可信代码，本仓库不会为它提供网络或文件系统隔离。

## 代码地图

### `contracts/`：共享词汇

这里放依赖尽量少的公共数据契约：TaskSpec、运行结果、输入、产物、进度和资源需求。它描述边界，不实现某一条 EDA flow。领域参数保持为 mapping，由 Toolkit 自己解释，kernel 不解析。

### `core/runtime/`：执行内核

Runtime 负责一次 attempt 的持久化状态和工程执行机制：

- 状态转换和 SQLite 持久化；
- 输入 staging 和内容摘要；
- 每个 attempt 的 workspace；
- worker lease 和 worker 丢失恢复；
- 进程树监管、timeout 和 cancel；
- 资源预留和采样测量；
- adapter request/result 处理；
- artifact、metric、log 和 timeline 证据。

Runtime 不知道 OpenROAD、Innovus、PrimeTime、placement、CTS、routing 和 QoR 策略。如果新增工具必须在此目录增加领域分支，通常说明边界放错了。

### `core/registry/`：发现外部能力

Registry 发现 Toolkit manifest，验证身份和支持架构，检查 provenance，读取平台拥有的 admission record。它决定外部进程能否被解析，但不导入 Toolkit，也不判断领域参数的含义。

### `core/provenance/`：保存证据关系

Provenance 把指标连接到生成它的产物和 parser。平台从磁盘重新计算产物哈希，不信任 adapter 声称的哈希。

### `core/evaluator/`：领域边界

Evaluator 可以解析报告并给出领域结果，但仍通过外部进程边界运行。kernel 只检查证据契约，不决定物理设计结果是否优秀。

### `core/client/`：轻量 API 客户端

Client 供应用和验收测试提交、查询运行。它不是第二个 runtime，也不能复制生命周期逻辑。

### `gateway/`：组合根和 HTTP 路由

Gateway 构建 kernel、按配置启用认证、暴露 kernel 路由并路由应用路径。它是入口，不是调度器，也不是 Toolkit registry 的替代品。

### `apps/plan_executor/`：Agent 编写的计划

Plan executor 持久化 Agent 编写的有序任务，通过正常 kernel API 一步一步提交任务，在步骤间传递声明的 artifact，并汇总计划状态。它不生成 EDA flow、不改写参数、不替 Agent 选择策略。当前实现有意保持串行。

### `plugins/` 和 `examples/`：边界示例

`plugins/` 是仓库内最小的发现示例。`examples/research-toolkit/` 是更完整的外部进程示例，演示执行 Agent 提供的脚本，以及 patch/build/benchmark。真实 EDA 集成可以单独维护在其他仓库中，通过相同 manifest 协议安装。

### `guardrails/`：可执行架构规则

Guardrails 防止工具名和 adapter 逻辑泄漏进 kernel，防止应用直接打开 kernel 数据库，并控制代码规模和包边界。Negative fixture 是故意违规的样例，必须保持违规，才能证明门禁真的能拦截问题。

## 任务生命周期

```text
1. Agent 创建 TaskSpec 或有序 Plan
2. API 校验结构契约
3. plan executor 持久化 Agent 的计划
4. kernel 登记输入并创建 run
5. worker 预留资源并创建 attempt workspace
6. Runtime 写入 adapter_request.json 并启动 Toolkit Adapter
7. Adapter 调用 EDA 工具并写入 adapter_result.json
8. Runtime 监管进程并记录日志、进度和 timeline
9. Runtime 校验输出并从磁盘计算 artifact 哈希
10. Client/API 暴露 run、artifact、metric、resource 和 failure 证据
11. Plan executor 把选定 artifact 传给下一步 Agent 任务
```

Plan executor 可以执行 Agent 已经声明的步骤依赖和 artifact 传递，但不会解释 artifact，也不会擅自创造下一步任务。下一步怎么做仍然是 Agent 的决策。

## 新代码应该放在哪里

| 变更 | 应放位置 | 原因 |
| --- | --- | --- |
| 公共 task/result 契约字段 | `contracts/` 加契约测试 | 这是边界变化 |
| timeout、lease、workspace、artifact 行为 | `core/runtime/` | 这是执行机制 |
| manifest 校验和 admission | `core/registry/` | 这是外部包发现机制 |
| API 路由 | `gateway/` 或对应 app | HTTP 组合属于边缘层 |
| 计划排序或 artifact 绑定 | `apps/plan_executor/` | 计划是应用职责 |
| 启动新 EDA 工具或解析报告 | 外部 Toolkit 仓库 | 领域知识不能进入 kernel |
| 新 Toolkit 示例 | `examples/` | 示例不应形成平台耦合 |
| 新架构门禁 | `guardrails/` 加 negative fixture | 规则必须可执行 |

## 边界决策和取舍

### 为什么用外部进程，而不是进程内插件 API？

外部进程让 Toolkit 自己管理解释器、依赖和发布节奏，也让 Python adapter、编译程序和 vendor launcher 使用同一套生命周期。代价是需要文件协议、安装步骤和 admission record。

### 为什么底座不统一所有 capability？

不同工具里的 `place`、`route`、`timing`、`build` 并不完全等价。如果 kernel 统一这些概念，执行层就会变成 EDA 策略层，新工具也会被迫适配第一个工具的模型。因此底座只记录 opaque input，Toolkit 自己负责 capability 语义。

### 为什么 plan executor 独立存在？

它是面向 Agent 的应用，负责保存计划和传递 artifact。独立出来后，kernel 可以专注于可靠执行，未来更换规划模型也不需要把规划策略写进内核。

## 当前范围

当前验证范围是单机、本地状态、外部进程和串行计划。并行调度、多节点、硬 cgroup 限制、远程对象存储、生产监控和冻结后的兼容协议属于后续工作，只有在具体实验真正需要时才应以独立契约和验收证据加入。
