# P9 里程碑：证据知识库与回放

captured_at: 2026-08-04
status: completed

- 仅 verified + durable ref/SHA + design/platform/PDK/toolchain 完整上下文可入库。
- 检索先硬过滤版本；exact-design 不跨设计，platform-general 仍不跨 PDK/toolchain。
- 结果带 evidence citation 和 record fingerprint；回放重验 fingerprint/context/RepairAction。
- P5 真实比较证据完成一次 score=1.0 回放，返回 data-only、`executed=false`。
- SQLite index 用 DELETE journal；原始 evidence 不修改。证据 `docs/evidence/P9_KNOWLEDGE_ACCEPTANCE.{md,json}`，全量 81 passed。
