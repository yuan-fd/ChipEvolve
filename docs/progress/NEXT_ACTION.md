# 下一步

updated_at: 2026-08-06

P16“开放知识、BYOK 与人控自演化 v1”规划已完成，等待用户审核批准，不自动开始实现。

执行顺序：

1. 建立公开知识与 benchmark Registry，先审计许可和版本，再下载允许缓存的内容；
2. 建立 Runtime 终态到 observed-only 数据库的持续、幂等、tenant 隔离管道；
3. 接入用户自带 OpenAI-compatible API key/模型，会话内存保存密钥；
4. 将 RL/BO/GP 变成可接受、修改、拒绝的 T1 建议，并实现默认关闭的 T2 硬门；
5. 新增 backend-neutral Craft FlowPlan 和 OpenROAD/ORFS backend；
6. 用 fake provider、固定语料和最多一条小型 Craft→OpenROAD GDS 做集成验收。

推荐整体批准 `tasks/phase-16.md` 第 12 节：密钥 TTL 8 小时、用户数据默认私有、T2 默认关闭、Craft 新增 OpenROAD adapter、最多 8 个 ORFS run/并发 2、5 GiB 下载、最多 5 次用户自带真实 API 调用。DPLEvolve P16 full-flow 预算为 0。

恢复入口：

```bash
cd ~/openroad-platform
git status --short
sed -n '1,360p' tasks/phase-16.md
sed -n '1,220p' docs/evidence/P15_DPLEVOLVE_SMOKE.md
```
