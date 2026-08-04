# P9 证据知识库与建议回放验收

status: completed
captured_at: 2026-08-04

## 结论

证据知识库已实现为确定性索引：只有 verified、带持久 evidence ref/SHA-256、完整 design/platform/PDK/toolchain 上下文的记录可以入库。检索先做版本硬过滤，再做词项排序；“相似”不能绕过版本隔离。

## 真实证据回放

从 P5 真实 38%/35% ORFS 对比证据导入一条 exact-design 经验：35% 候选功耗略低且 DRC=0，但时序/线长有权衡。

- 源：`docs/evidence/P5_AGENTICPD_ACCEPTANCE.json`。
- 源 SHA-256：`6aef7414c8076aac6614d9139fb0e74c66c954b4df8e7e04532b53ec4c500789`。
- 上下文：`p4_simple_adder / nangate45 / nangate45-public / orfs-51ad123`。
- 查询 `power utilization DRC` 命中 score 1.0，结果携带原始引用、SHA 和 record fingerprint。
- replay 重新校验 context/fingerprint/RepairAction 后返回 `approved_for_policy_evaluation`、`executed=false`。

这里的建议不是“35% 总会更好”，更不是执行命令；它只是同版本同设计上下文中可供策略评估的候选。

## 版本和安全门

- exact-design 记录只能匹配相同 design；platform-general 可跨 design，但 PDK/toolchain 必须完全一致。
- 未验证记录、无 SHA、无持久引用、缺上下文全部拒绝。
- stale/tampered fingerprint、toolchain/PDK/design mismatch replay 全部拒绝。
- proposed action 必须引用同一证据并再次通过 P7 `RepairAction` 契约。
- SQLite index 使用 DELETE journal，不在共享盘产生 WAL；原始 artifact/run 不被修改。

## 验证

```text
python -m pytest -q
81 passed
```

覆盖未验证事实、版本隔离、scope、引用、排序、指纹篡改、上下文错配和 data-only replay。
