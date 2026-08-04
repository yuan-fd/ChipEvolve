# 下一步

updated_at: 2026-08-04

实施 P7 自然语言入口与有限 ReAct：自然语言只转换为严格 TaskSpec；错误分类映射到证据化 RepairAction 白名单，执行次数和停止条件受预算控制，禁止任意 shell。

首条恢复命令：

```bash
cd ~/openroad-platform && git status --short && sed -n '1,240p' docs/evidence/P6_CAMPAIGN_ACCEPTANCE.md
```
