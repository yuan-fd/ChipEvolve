# 下一步

updated_at: 2026-08-04

实施 P10 Coding/Evolve Agent：候选补丁只能进入隔离 worktree，执行确定性测试/静态检查并生成证据包；未通过或未获策略/人工批准不得应用到基线。Evolve 只从 P9 匹配上下文的证据生成候选。

首条恢复命令：

```bash
cd ~/openroad-platform && git status --short && sed -n '1,240p' docs/evidence/P9_KNOWLEDGE_ACCEPTANCE.md
```
