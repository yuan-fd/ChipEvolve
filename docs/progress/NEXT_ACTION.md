# 下一步

updated_at: 2026-08-04

实施 P9 证据知识库：只导入带来源、版本上下文和验证状态的事实/经验；检索必须匹配 design/platform/toolchain，建议回放仍经过 TaskSpec/RepairAction 策略，不把文本当命令。

首条恢复命令：

```bash
cd ~/openroad-platform && git status --short && sed -n '1,260p' docs/evidence/P8_TAIWEI_ACCEPTANCE.md
```
