# Review fixes — 2026-09-18

本轮修复针对代码审核中已经有明确证据、且不依赖商业 EDA 服务器的问题。没有改变 Agent / Execution Foundation / Toolkit 的职责边界，也没有引入 Kubernetes、多节点调度或工具专用逻辑。

## 已修复

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| G4 检查错误的 `openroad_core` 命名空间 | 改为检查真实的 `openroad_platform_` 包；保留 contracts/client 白名单；增加真实包名回归测试 | `d43ceaf`；G4 通过 |
| Guardian 日志队列无界、终止时可能丢尾部 | raw 输出由 reader 直接流式写入日志；内存 telemetry channel 固定 256 行；丢弃的仅是 telemetry，并明确记录数量；timeout/cancel 轮询不变 | `7256aab`；guardian 17 项测试通过 |
| Toolkit 版本无法从公共 Task/Plan/API 指定 | `TaskSpec.plugin_version` 可选；KernelClient 支持便捷参数；runtime/registry 解析指定版本；plan 原样传递；未指定时保留原有歧义拒绝规则 | `fc8a0f7`；契约、runtime、plan、HTTP 测试通过 |
| Plan 遇到临时 KernelUnavailable 直接失败 | 增加 `retry_wait`；保存结构化 `kernel_unavailable`；短 backoff 后继续；保留 plan-scoped 幂等 key；真正的 KernelError 仍终止计划 | `4afd9e7`；恢复场景测试通过 |
| 远程 Agent 生成脚本/patch 没有输入入口 | 增加 `POST /kernel/inputs` 和 `KernelClient.upload_input()`；SHA-256 内容寻址；Task 用 `input_id` 引用；staging、workspace、输入证据和用户所有权沿用现有链路 | `6c552b6`；远程上传→Task→staging→run 测试通过 |

## 质量结果

- 全量测试：`451 passed, 1 skipped`。
- Guardrails：`40 passed`，包括 G4 真实包名回归和 G8 规模门禁。
- 正式源码 mypy：47 个 source files，无问题。
- 受影响实现和测试的 Ruff：通过。
- G8 未提高 ceiling：总量和 guardian/runtime/store 分文件均回到既有批准上限以内。
- 全仓 Ruff 仍有 99 个历史问题；Black 仍有历史格式化债务。这两项没有被本轮伪造为绿色，也没有修改故意违规 fixture。

## 当前输入上传边界

这是单机本地输入交接，不是对象存储协议：gateway 复用现有 1 MiB 请求体上限，只接受 `application/octet-stream`，上传记录绑定认证用户，任务提交时检查所有权，staging 时再次校验对象大小和哈希。服务器迁移后，仍需根据真实部署的认证、存储和保留策略重新评估。

## 留给新服务器的工作

以下内容没有闭门实现：商业工具路径和 license、PDK/library、module/source 环境、Toolkit preflight、EnvironmentProfile、Innovus/PrimeTime Adapter，以及 ORFS 在新机器上的真实 GCD 验收。新服务器应先侦察并手工跑最小工具案例，再把真实差异接入现有生命周期。
