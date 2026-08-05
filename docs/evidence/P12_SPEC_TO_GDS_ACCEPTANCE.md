# P12 多轮 Spec→GDS 验收

status: accepted for the bounded local workflow
date: 2026-08-05

平台新增持久化 `SpecConversationStore`、多轮 `SpecConversationManager`、离线规则 Provider 和 `CodexCliSpecProvider`。Codex 仅在临时空目录、`--ephemeral`、`--sandbox read-only`、JSON Schema 输出下提出规格/RTL；允许模型只有 `gpt-5.6-terra` 与 `gpt-5.6-sol`。模型输出不能直接执行，必须经过字段白名单、RTL 安全检查、确定性 Task 编译、用户显式确认与 Runtime 准入。

真实验收使用 Terra 将二输入与门规格变成 RTL。初始 run `1f65b71244704ab2a5e8fb2e2f9185aa` 的 synth 成功，floorplan 因极小设计触发 `PDN-0185`。平台保留失败证据，并创建 data-only 修复 Task，将 `minimum_die_size_um` 设为 20。子 run `2f6d473152204a799c80501678076d51` 完成六阶段，登记 12 条工具阶段事件和 18 个哈希产物，包括 GDS、DEF、ODB、网表、工具链快照和 KLayout 2D 图片。

会话有最大 turn、Provider call、EDA run、修复次数和 wall-clock 预算。确认提交使用稳定 task id；API 重试会重绑已有 Runtime run，不重复提交。Codex 登录只用于本机 CLI Provider，不等同于平台拥有可部署 API key；正式多用户服务仍应使用独立 Responses API Provider。

真实性边界：该验收证明结构化规格、RTL 可综合和真实物理实现闭环；任意规格的功能等价不能仅由综合成功推断，仍需按设计接入 testbench/reference/formal 证据。

原始证据：`.tools/p12-acceptance/runtime-20260805-accepted/`。
