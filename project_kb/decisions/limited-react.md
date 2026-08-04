# 有限 ReAct 决策

status: accepted-by-roadmap
captured_at: 2026-08-04

- 自然语言只产生 TaskSpec 数据，不产生进程命令。
- 失败修复只读取结构化 failure 和 evidence refs。
- RepairAction 使用精确白名单模板；新增动作类型必须先扩展契约、策略和失败测试。
- stop 是正常且必须可达的决策，不以无限重试掩盖阻塞。
- 任何模型实现只能替换“候选数据生成”部分，不能绕过 compiler、RepairAction validation 或 Runtime。
