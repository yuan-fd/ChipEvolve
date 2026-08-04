# ADR-0002：首版插件采用独立环境与版本化子进程协议

- status: Accepted
- accepted_by: user
- accepted_at: 2026-08-04

## 背景

RTLScout、AgenticPD、TaiWei 与核心平台的 Python、工具链、PDK 和系统要求不同。核心直接 import 插件依赖会产生版本冲突、全局环境污染和取消边界不清。

## 决策

首版插件统一采用独立 Python/Conda 环境加子进程 JSON stdin/stdout 或任务/结果文件协议。公共 envelope 必须带 schema_version；工具 stdout/stderr 写独立日志。Runtime 使用受控 cwd、环境 allowlist、进程组、超时和 artifact path 校验。

插件 manifest 固定 adapter entry、环境、能力、架构、输入输出 Schema、工具要求、超时和 artifact rules。跨节点需求出现前不引入网络微服务或消息队列。

## 影响

- 插件可以使用不同 Python 和工具链。
- 需要协议 conformance tests 和清晰的日志通道。
- 大文件使用 Artifact 引用，不能塞入 JSON。
- 将来可把同一 adapter envelope 搬到队列 worker，而无需改变业务契约。

## 被否决方案

1. 核心直接 import 第三方包：依赖与安全边界不可控。
2. 一开始微服务化：增加部署和一致性成本，当前无跨节点硬需求。
3. 仅通过非结构化 shell 输出判断状态：无法可靠校验、迁移和恢复。
