# P9 任务：证据知识库与跨实验复用

status: completed
phase: P9
started_at: 2026-08-04
completed_at: 2026-08-04
base_commit: 00cf2f8

## 白名单

- `packages/analysis/`：证据记录、索引、严格上下文检索和建议回放。
- `tests/test_knowledge_base.py`、P9 任务、证据、知识规范和 memory snapshot。
- 测试 live index 仅放 pytest `/tmp`；平台持久记录仍以原始 Artifact/Run evidence 为来源。

## 禁止范围

- 不导入未验证事实、无 SHA 证据、模型推测或 mock QoR。
- 不跨 PDK/toolchain 版本静默复用；不把检索文本当执行命令。
- 不修改原始 run/log/artifact，只追加索引和摘要。

## 验收门

- verified、evidence ref/SHA、design/platform/PDK/toolchain 上下文为强制字段。
- exact-design 与 platform-general scope 规则明确；版本不匹配检索为零。
- 结果包含可核验引用、内容指纹与确定性排序。
- 建议回放重新验证上下文、内容指纹和 RepairAction schema，返回 data-only。
- 全量 pytest、diff、越界审计通过。

## 预算与停止条件

- 单次检索上限 20 条，文本长度 4000 字，SQLite index 不使用 WAL。
- 任一上下文字段缺失或不匹配即拒绝，不用相似度绕过硬过滤。
