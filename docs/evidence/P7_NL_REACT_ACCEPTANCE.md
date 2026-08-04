# P7 NL→TaskSpec 与有限 ReAct 验收

status: completed
captured_at: 2026-08-04

## 结论

自然语言入口现在只能输出经过 schema 和策略双重校验的 TaskSpec 预览，不会在 HTTP 请求内提交或执行任务。失败修复层只能生成四种结构化 `RepairAction`，且动作本身仍是待 Runtime/策略批准的数据。

## 自然语言边界

- 支持 ORFS 和 RTLScout 两类明确意图；平台首版只允许 Nangate45。
- ORFS 的 RTL 路径和 top 来自已登记 DesignService 上下文，不从用户文本提取本机路径。
- stage、时钟周期、利用率、RTLScout benchmark/model/step 均白名单和数值限界。
- `;`、管道、反引号、重定向、命令替换和常见 shell 下载/执行词被拒绝。
- `POST /api/tasks/compile` 返回 `execution_started=false`，Runtime DB 没有新增 run。

## RepairAction 边界

- 动作只有 `retry`、`increase_timeout`、`lower_core_utilization`、`stop`。
- 每类动作的参数集合是精确模板；未知字段（包括 `shell`/`command`）反序列化失败。
- 每次决定必须携带 evidence refs；模型文字不能替代日志或事件证据。
- 默认最多两个非 stop 修复、同类失败最多两次、Task 最多三个 attempts。
- 不可修复错误、预算耗尽或达到参数上限会生成 `stop`，不能创建下一 TaskSpec。

## 验证

```text
python -m pytest -q
75 passed
```

覆盖中文 ORFS 意图、RTLScout 离线意图、恶意命令注入、非白名单平台/插件、无证据修复、参数模板、预算停止和 API 不提交行为。P7 没有调用真实外部 LLM；这是本阶段有意的确定性安全基线，不把规则编译器称为模型能力。
