# P8-Real 里程碑：真实 3D 与平台系统验收

captured_at: 2026-08-05
status: completed

- 官方固定链：ORFS-Research `568eb04...`、OpenROAD `305d3ba...`、Yosys `77005b6...`、TaiWei `db20136...`；私有 `.tools/taiwei-official-3d`，共享 2D 未改。
- Runtime run `dacffccb314e439aba6f1c9cd6c1d1fc`：1 Attempt、40 events、15 artifacts、20 metrics、status succeeded。
- GDS 550,222 bytes，SHA `fce450ea...bb3a4`；自定义 via 669/709 在 DEF、layout、重读 GDS 中数量与面积一致。
- QoR：core 36.8874、stdcell 54.36882、power 0.000821、wirelength 983.34、WNS -21.0、TNS -77.85、DRC 524、FEP 8、HB via 72、cross-tier 98。
- ProcessGuardian 现在能回收官方 `setsid` detached child；真实 timeout/failure/cancel 均无 orphan，失败证据保留。
- API/Web 真实读取 Runtime DB，展示 230 upper/252 bottom、HB via/cross-tier、工具链、GDS/SVG；Campaign 引用和 artifact SHA 下载通过。
- 三链总验收：RTLScout→2D GDS；AgenticPD→baseline/candidate Campaign→真实 QoR；TaiWei→3D GDS。
- live SQLite 在 `/tmp`；snapshot 恢复 integrity=ok、foreign keys=0、15 artifacts 全部可重验。
- 全量 87 passed；tracked credential scan 0 finding；不 push、不部署。
- 证据：`docs/evidence/P8_REAL_ACCEPTANCE.{md,json}`；原 P8 blocker 文件不得改写。
