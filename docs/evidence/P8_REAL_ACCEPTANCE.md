# P8-Real 补验与平台系统验收

status: completed
captured_at: 2026-08-05

## 结论

TaiWei 官方固定版本已经由 Workflow Runtime 在全新 workspace 真实跑通 `ord/asap7_3D/gcd`。这条记录是原 P8 外部阻塞解除后的新增证据；`P8_TAIWEI_ACCEPTANCE.*` 的历史 blocker 保持不变。

固定链为 ORFS-Research `568eb04...`、OpenROAD `305d3ba...`、Yosys `77005b6...`、TaiWei `db20136...`。源码、binary SHA、编译器、动态库和 ASAP7 3D 输入均由 `environment.lock.json` 锁定，安装位于项目私有 `.tools/taiwei-official-3d/`，没有覆盖共享 2D 工具链。

TaiWei/ORFS scripts/OpenROAD/Yosys 分别核验为 BSD-3-Clause、BSD-3-Clause、BSD-3-Clause、ISC。ASAP7 3D 数据未发现独立 license 文件，因此只用于私有本地验收，不进入 Git 或 release、不再分发；详见 `integrations/taiwei_pin_3d/LICENSE_AUDIT.md`。

## 真实 Runtime run

- Run：`dacffccb314e439aba6f1c9cd6c1d1fc`，状态 `succeeded`，1 个 Attempt。
- 事件 40 条、产物 15 个、指标 20 个；每个登记产物的 SHA-256 已从 workspace 重算并与 Runtime DB 一致。
- GDSII：550,222 bytes，SHA-256 `fce450ea0ace14c40c91ccdf1648146b11a0fb6cf9acb7566ea6253d4dabb3a4`。
- live DB 位于 `/tmp`；重建 Runtime 后再次执行同一 terminal run 没有新增 Attempt。

官方流程不主动 stream-out GDS，平台使用确定性 KLayout 后处理。适配层使用实际 ORD tech LEF `asap7_tech_1x_2A6M7M.lef`，并 fail closed 验证：

| 自定义 via | DEF 引用 | layout shapes | 重读 GDS shapes |
| --- | ---: | ---: | ---: |
| `VIA_M1m_M2add` / `V1_add` | 669 | 669 | 669 |
| `VIA_M2add_M3add` / `V2_add` | 709 | 709 | 709 |

shape 数量和面积逐层完全一致。原来可能把平台 library GDS 误当结果的宽泛 glob 也已移除；只允许登记 run output 下唯一 `6_final.gds`。

## QoR 与 3D 指标

Core area 36.8874、StdCell area 54.36882、power 0.000821、wirelength 983.34、setup WNS -21.0、TNS -77.85、DRC 524、FEP 8。物理 HB via 72，cross-tier nets 98（UB 44、UIO 0、BIO 27、UBIO 27），upper/bottom placed instances 为 230/252。

这些数字是本次固定工具链真实结果，不代表设计已达到 signoff 或 QoR 优化目标；尤其 DRC/WNS 仍应如实显示。

## 恢复、API/Web 与平台链

- 真实 timeout、工具失败和 cancel 均由 Runtime 留下独立终态与事件；失败证据没有覆盖。
- cancel 在官方 launcher 已经创建新 session 的 detached task 后发出，ProcessGuardian 仍完整回收其进程树，残留 EDA 进程为 0。
- loopback HTTP 实测通过 run/detail、Campaign、GDS/SVG 下载、工具链、tier、HB via、cross-tier 和 terminal cancel 幂等。
- RTLScout→真实 2D GDS、AgenticPD proposal→两成员 Campaign→真实 ORFS QoR、TaiWei→真实 3D GDS 三条链已组合验收；统一重放入口为 `scripts/run_platform_demo.py`。
- Runtime snapshot 已恢复到新的 `/tmp` DB；integrity/foreign key/artifact SHA 全部通过。

## 发布前检查

- 全量：`87 passed`。
- Git tracked credential scan：198 个文本文件，0 finding，0 suspicious filename。
- Node JS syntax、shell syntax、Python compile、lock/hash、SQLite backup/restore 均通过。
- 原始证据位于 ignored `.tools/p8-real-acceptance/runtime-20260805/` 和 `.tools/p8-real-resilience/runtime-20260805-r2/`。
- 不 push、不部署。
