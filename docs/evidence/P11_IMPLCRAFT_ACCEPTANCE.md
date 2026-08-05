# P11 EDACraft / ImplCraft 验收

status: accepted with commercial-live boundary
date: 2026-08-05

官方 `ephonic/EDACraft` 已固定到 commit `739eee0f3ced8fc3cbb6f01b6cc89414758fd898`，通过黑箱适配接入 `edacraft-implcraft@1.0.0`。真实 Runtime run `6b93bfaffe404735ad8d5b4922277579` 成功生成 3 份 Tcl、配置、状态、报告、工具链快照与日志，共 8 个哈希产物。

上游固定回归共 220 项：215 passed，5 个已知失败与准入基线一致。源码和 Python 环境位于 ignored `.external-src/`、`.tools/`。

边界必须保持明确：本机没有 DC/ICC2/PrimeTime/Calibre/Innovus/Tempus 等商业 binary/license。因此当前完成的是 `eda.implcraft.scriptgen` 与 `eda.backend.plan`，不是商业 live flow、商业 GDS 或 signoff。EDACraft 根许可证含 Non-Commercial 附加限制，只用于本机私有非商业验收。

原始证据：`.tools/p11-acceptance/runtime-20260805/`。
