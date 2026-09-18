# AgenticEDA Toolkit / Plugin 协议 v1alpha1

[English normative protocol](PLUGIN_PROTOCOL.human.md) · [协议代理规范](PLUGIN_PROTOCOL.agent.md)

这份文档是给第三方 Toolkit 开发者看的中文说明。字段名、命令行参数和拒绝条件以英文规范为准；两份文档有冲突时，英文规范优先。协议当前是 `v1alpha1`，适合研究和联合调试，尚未承诺长期兼容。

## 先理解它是什么

Toolkit 不是导入平台的 Python 包，也不是 kernel 里的一个类。它是一个独立目录和一个独立进程。平台通过 request/result 文件与它通信：

```text
平台创建 attempt workspace
  → 写 adapter_request.json
  → 启动 Toolkit Adapter
  → Adapter 在 workspace 中调用真实工具
  → Adapter 写 adapter_result.json
  → 平台校验结果、计算哈希并保存证据
```

这样做有三个目的：Toolkit 可以有自己的解释器和依赖；Toolkit 可以独立发布；平台可以用同一套 timeout、cancel、artifact 和 failure 逻辑管理不同工具。

## 最小目录结构

```text
my-toolkit/
├── my-toolkit.plugin.json   必需：manifest
├── provenance.json          可选：来源信息
├── adapter.py               入口，可以是任何可执行程序
└── ...                      工具脚本和依赖
```

平台从 `plugins_root` 的直接子目录中寻找 `*.plugin.json`。插件目录不能通过 import 使用平台包；adapter 应该只依赖自己的运行环境。

## Manifest 必须写什么

最小示例：

```json
{
  "schema_version": 3,
  "plugin_id": "my-capability",
  "plugin_version": "1.0.0",
  "adapter_entry": ["/opt/team/venv/bin/python", "./adapter.py"],
  "capabilities": ["eda.rtl_to_gds"],
  "supported_arch": ["aarch64", "x86_64"],
  "artifact_rules": [
    {"kind": "report", "required": true},
    {"kind": "gds", "required": false}
  ]
}
```

主要字段：

| 字段 | 要求 | 含义 |
| --- | --- | --- |
| `schema_version` | 必须为 `3` | manifest、task、result 使用的协议版本 |
| `plugin_id` | 稳定、唯一 | 外部 Toolkit 的 wire identity；字段名为兼容保留 |
| `plugin_version` | 必填 | Toolkit 自己的版本；多个版本同时存在时调用方必须选择 |
| `adapter_entry` | 非空参数数组 | 平台按原样执行；`./adapter.py` 只能指向 Toolkit 目录内部 |
| `capabilities` | 非空数组 | Toolkit 提供的能力名，由 Toolkit 自己解释 |
| `supported_arch` | 非空数组 | 支持的 `platform.machine()` 值 |
| `artifact_rules` | 可选 | 允许 adapter 声明的 artifact 类型及是否必需 |
| `default_timeout_seconds` | 正整数，可选 | 单次 attempt 的 Toolkit 默认超时 |
| `environment` | 字符串到字符串，可选 | 传给 adapter 的额外环境变量 |
| `requirements` | 可选 | 请求 receipt、protected evaluation 或 resumable 等平台行为 |
| `progress_marker` | 非空字符串，可选 | 进度 envelope 的行前缀，默认 `[progress]` |

未知字段会被拒绝，不会静默忽略。`adapter_entry` 的第一项最好是 Toolkit 自己的绝对解释器或可执行文件；平台不会替 Toolkit 选择 Python。

`requirements` 可以包含 `require_protocol_receipt`、`environment_receipt_variable`、`require_experiment_protocol`、`require_protected_evaluation` 和 `resumable`。`resumable` 表示 worker 丢失后可以继续使用已有 workspace；不能从已有 workspace 继续的 Toolkit 不应声明它。`require_protocol_receipt` 需要合法的环境变量名，`require_experiment_protocol` 要求 task input 中有实验协议对象。平台只在这些字段有对应运行行为时才接受它们。

## Admission 和 provenance

`provenance.json` 可以写 license、source URL、source commit 和说明，但它不会自动获得执行权限。平台自己的准入记录放在 Toolkit 目录之外：

```text
admissions/my-capability.json
```

```json
{
  "plugin_id": "my-capability",
  "status": "admitted",
  "license_review": "green",
  "approved_commit": "<reviewed-commit>",
  "reviewer": "team-name",
  "reason": "License and source revision reviewed"
}
```

正常运行要求 `status` 为 `admitted`，同时有 `green` 或 `yellow` 的 license review 和非空 `approved_commit`。新 Toolkit 没有 admission 时可以被列出，但默认不会被执行。这是平台的审查门槛，不是对 checkout 内容做密码学完整性验证：当前实现比较 admission 中的 revision 和 Toolkit 自己声明的 provenance revision，不会自动验证实际目录的 Git commit 或整个文件树哈希。Toolkit 自己不能给自己盖章。

## Adapter 的命令行接口

平台一定追加两个参数，并且顺序固定：

```text
--request <absolute path to adapter_request.json>
--result  <absolute path to adapter_result.json>
```

Adapter 必须接受它们。request 的实际外层只有 `protocol_version`、`plugin` 和 `task`；attempt workspace 是进程的当前工作目录，staged 文件已经由平台放入其中，输入清单在根目录的 `input_manifest.json`。不要假设 request 的具体领域字段，先按照自己 capability 的约定读取 `task.inputs` 和 `task.parameters`。

最小 request 形状如下：

~~~json
{
  "protocol_version": 1,
  "plugin": {"plugin_id": "my-capability", "plugin_version": "1.0.0"},
  "task": {
    "schema_version": 3,
    "task_id": "task-1",
    "project_id": "project",
    "design_id": "design",
    "plugin_id": "my-capability",
    "inputs": {"capability": "eda.rtl_to_gds"},
    "parameters": {},
    "staged_inputs": [],
    "resources": null,
    "timeout_seconds": 3600,
    "max_attempts": 1,
    "expected_artifacts": []
  }
}
~~~

`protocol_version` 是 request 外层协议版本；`task.schema_version` 是 TaskSpec payload 版本，两者不是同一个字段。`staged_inputs` 描述平台要复制进 workspace 的文件；`resources` 是调用方申请的 CPU、内存、CPU time 或进程数边界；`timeout_seconds` 是单次运行时限；`max_attempts` 是可重试失败的 attempt 上限；`expected_artifacts` 是调用方要求成功时必须出现的 artifact kind。这些字段的详细形状见[执行计划协议](EXECUTION_PLAN_PROTOCOL.md)。

Adapter 应该：

1. 在当前 attempt workspace 中运行工具；
2. 不写 workspace 之外的实验产物；
3. 捕获工具输出并形成自己的报告；
4. 在退出前写 result 文件；
5. 让进程退出码和 result 的 `exit_code` 一致。

## Result 必须满足什么

成功结果的最小形状：

```json
{
  "schema_version": 3,
  "status": "succeeded",
  "exit_code": 0,
  "started_at": "2026-09-18T10:00:00+00:00",
  "ended_at": "2026-09-18T10:01:00+00:00",
  "artifacts": [
    {"kind": "report", "path": "report.json"}
  ],
  "metrics": [
    {
      "name": "wns",
      "value": -0.12,
      "unit": "ns",
      "context": {
        "source_artifact_store_key": "report.json",
        "parser_id": "my-parser",
        "parser_version": "1"
      }
    }
  ]
}
```

`status` 可以是 `succeeded`、`failed`、`cancelled` 或 `timed_out`。成功必须是退出码 0；result 中的退出码必须等于真实进程退出码。Adapter 如果不写 result，平台会记录 protocol error。

平台不接受 adapter 传入的 artifact 哈希，而是进程退出后从磁盘读取文件并计算 SHA-256。成功时，manifest 中 `artifact_rules` 的 `required: true` 类型和 task 的 `expected_artifacts` 都必须存在且非空；result 里的 artifact 对象没有控制必需性的 `required` 语义。若 manifest 没有 artifact rules，则不会因为 allowlist 缺失而拒绝 kind，但 task 的 `expected_artifacts` 仍会在成功时检查。失败时可以保留部分证据。

## Artifact 和 Metric 规则

Artifact path 必须是相对于 attempt workspace 的路径：

- 不能是绝对路径；
- 不能含 `..` 路径段；
- 文件必须存在且大于 0 字节；
- 如果 manifest 声明了 `artifact_rules`，`kind` 必须出现在其中；空的 rules 表示没有额外的 kind allowlist；
- 平台保留的 `runtime_input_manifest` 等类型不能由 adapter 伪造。

Metric value 必须是 JSON scalar，不能是 NaN。若提供 `context.source_artifact_store_key`，它必须指向同一 result 里声明并成功登记的 workspace-relative artifact，否则运行失败。没有 source key 的 metric 可以保存，但会是未追溯 metric；需要作为实验证据比较的指标应始终带 source。

平台还会登记日志、进度、资源使用和状态 timeline。工具版本、动态库和 toolchain snapshot 不是这个通用协议自动生成的字段；需要它们的 Toolkit 应自行写入 artifact 或 provenance，并在自己的验收测试中证明内容来源。平台不会把任意 provenance 当成可信测量。

## 失败怎么表达

Toolkit 应该把领域失败写入 result 的 `failure`，例如：

```json
{
  "category": "timing_violation",
  "message": "setup slack is below the experiment threshold",
  "retryable": false
}
```

建议区分配置、工具和评估阶段：

- `configuration_error`：参数、输入或本机工具配置不能执行；
- `tool_error`：工具启动后失败；
- `build_error`：构建或编译失败；
- `benchmark_error`：benchmark 执行失败；
- `evaluation_error`：领域评估失败。

平台失败（例如 workspace、timeout、lease、协议或资源问题）由平台记录，Toolkit 不应把它伪装成 QoR 失败。只有标记为 retryable 的失败才可能进入平台的有界重试。

## 不要做的事情

- 不要让 Agent 或 Toolkit 绕过平台直接创建未记录的 EDA 进程；
- 不要把产物写到 attempt workspace 之外；
- 不要把 workspace 当成 OS 安全沙箱；adapter 是可信代码，可能访问平台没有隔离的主机资源；
- 不要在 kernel 里增加某个工具名、flow 顺序或参数语义；
- 不要为了“兼容”静默接受未知 manifest 字段；
- 不要报告一个没有实际文件支撑的成功 artifact；
- 不要把密码、license token 或其他 secret 写进 manifest、日志或 result。

## 接入前检查表

- [ ] manifest 的 `schema_version`、ID、版本和架构正确；
- [ ] adapter 可接受 `--request` 和 `--result`；
- [ ] adapter 在独立 workspace 中运行并写 result；
- [ ] artifact 只使用相对路径且全部在规则中声明；
- [ ] 成功、工具失败、配置失败和缺失产物都有测试；
- [ ] metrics 能追溯到已登记的 artifact；
- [ ] provenance 和 admission 的 source commit 一致；
- [ ] 能通过真实 kernel/plan executor 测试，而不是只直接运行 adapter；
- [ ] 已阅读 [贡献者指南](../CONTRIBUTING.md) 和 [FAQ](FAQ.zh-CN.md)。
