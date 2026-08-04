# 候选补丁晋级门

status: accepted-by-roadmap
captured_at: 2026-08-04

- Evolve proposal 是 change request，不是 patch/命令。
- PatchProposal 必须绑定 base commit、evidence refs 和内容 SHA。
- 候选只在 `/tmp/openroad-platform-*` detached worktree 验证。
- verification argv 属于策略，不属于模型输出。
- PromotionGate 不提供 apply/merge/push；源码候选默认等待人工批准。
- 任何失败都保留结果并终止本候选，不在同一 worktree 自动修补。
