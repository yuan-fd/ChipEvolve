# P16 开放平台基础能力验收

status: accepted
date: 2026-08-06

P16 四项能力均已实现并验收：公开知识/benchmark 的可追溯导入、用户自带模型和内存 API key、人控 RL/BO/GP 建议、IC Craft 的 OpenROAD/ORFS 后端。Runtime 仍是唯一进程、Attempt、Artifact、Event 和终态权威。

公开语料快照登记 10 个来源和 7 个 benchmark definition。所有来源都有 URL、版本、许可判断和哈希；许可仍需文件级核实的大数据集只保存 metadata。50 个固定检索/错配用例通过，外部结论没有进入 observed 数据。

BYOK 用本地 fake OpenAI-compatible server 覆盖成功、401、429、超时、无效 JSON、超大响应、取消、跨 owner 拒绝和撤销。唯一 canary 在平台 SQLite、Runtime event、artifact、日志、备份候选和 Web/API 代码面 0 命中；本轮未调用真实付费 API。

T1 建议支持接受、修改、拒绝，修改后重新校验参数边界。T2 需要数据覆盖、held-out 校准、非 OOD、安全、opt-in 和预算全部通过，最多一个候选且仍不能直接执行。P14 真实 10 条 observation 的结果为 `not_eligible`。

Craft 真实验收使用官方 ORFS `gcd`。Runtime run `6111c2de98a94b28b7717d2f13cd96a3` 六阶段全部成功，99.479 秒完成，DRC=0、setup WNS=7.4585 ns。最终 GDS 631,606 bytes，SHA-256 `2d84c09a04902177def110104753df597b5b43b91082d8d434d65490a4dc6690`；同时登记 DEF、六份 ODB、网表、报告、工具链快照和 KLayout 2D 图像。

第一次 smoke run `d3b5b90540104414946f3a797b91398b` 因夹具真实顶层 `mux_2to1` 被误填为 `mux2`，在 synth 阶段失败。该失败证据和 Runtime DB 保留，第二次改用官方带时钟 gcd 后成功，没有覆盖前一失败。

全量回归 `167 passed`，Node 语法、JSON、`git diff --check` 和凭据扫描通过。机器可读结论见 `P16_OPEN_PLATFORM_ACCEPTANCE.json`，本地原始运行证据位于 `artifacts/p16-craft-real-gcd/` 与 `artifacts/p16-craft-real-smoke/`。
