# P2 ORFS 插件真实验收证据

date: 2026-08-04
result: accepted

## 结论

`orfs@1.0.0` 已通过 P1 WorkflowRuntime 在独立 Attempt workspace 中完成真实 Nangate45 `mux_2to1` 六阶段 RTL→GDS。Run、StageRun 和 Attempt 均为 `succeeded`；`implementation_valid=true`、`gds_complete=true`，不是仅有 GDS 的失败恢复结果。

## 运行身份

- Run：`d154f31a0ac64e3cb068329dfcde3149`
- StageRun：`409f41586c5f4f63aeeba6c6e2a6f66c`
- Attempt：`44dc660834f64c13b7d6bff3fbb3d491`
- 总 Runtime 墙钟：77.05 秒
- 阶段：synth 6.214s、floorplan 11.118s、place 21.865s、CTS 5.011s、route 10.320s、finish 11.819s
- v1 DB：1 Run、1 StageRun、1 Attempt、17 Artifacts、7 Metrics、29 Events

事件以 `run.accepted → stage.ready → attempt.started` 开始，在 17 个 artifact 和 7 个 metric 同事务登记后，以 `attempt.finished(succeeded) → run.finished(succeeded)` 结束。

## 关键证据

| 证据 | 大小 | SHA-256 |
| --- | ---: | --- |
| 输入 RTL | 131 | `ac316a63050a0047533ec6cf5f0a4daa5e4dc135c407bf868eddd6f27439d263` |
| final GDS | 19,572 | `d20ee44ef216af20a896b4a48794d2ee3fdd8de70b7fe8280fb8ae13a59ad1e6` |
| final DEF | 8,279 | `f3c8db8c27a043c476731fa41da1d8d026db7ef1ae4fc67d7eb41b412f2842ce` |
| final netlist | 369 | `72818ad8eefc2923482385b0dd4e4fe7063994071720556efb76d1c7fc2d39e3` |
| final ODB | 436,709 | `b3d102bd0eb2f3c1695833eea14611d725b8d3ab6fe327758a53c1d4b5d8f5c9` |
| generated config | 352 | `70a1ce8f22d05d8fe0d4292bf08d507128ea21dd3b5e93aa06c1979d13c93ccb` |
| ToolchainSnapshot | 2,906 | `9c791e1a30745e5fa30a60adb5952a362bc932af89c158e1475a7e82079350c8` |
| v1 DB snapshot | 122,880 | `b41b9f7843b3ade7effa03c4ee7d19ad556066a24c5e78cffe8ba8ceee261d37` |

工具链快照记录 ORFS `51ad1231...`、OpenROAD `26Q1-1961-g63ed2e0fe5`、Yosys 0.63、KLayout 0.30.6、Nangate45 platform config 哈希、生成配置哈希、输入 RTL 哈希和运行参数。

完整大产物保留在被 Git 忽略的 `runs/p2-acceptance-20260804-02/`；机器可读摘要见 `docs/evidence/P2_ORFS_ACCEPTANCE.json`。

## 保护范围

验收脚本在运行前后比较 ORFS HEAD、完整 dirty/status 文本、tracked binary diff 哈希、OpenROAD 子模块状态以及 OpenROAD/Yosys/KLayout wrapper 和 Nangate45 config 哈希，结果为 `shared_toolchain_unchanged=true`。P2 没有清理或覆盖共享 ORFS 原有的 dirty 状态。

Legacy 主数据库 `var/platform.db` 与 WAL 的大小和 mtime 保持原值。一次使用 `sqlite3 -readonly` 的诊断读取刷新了 `var/platform.db-shm` 锁侧文件的 mtime；没有写入主数据库、WAL 或业务记录。后续审计不再直接打开该共享 WAL 数据库。

项目位于分布式文件系统，首次把 SQLite WAL 放在项目盘会触发主机 `SIGBUS`，且在创建 Attempt 前终止。成功验收将 live Runtime DB 放在节点本地 `/tmp`，完成 checkpoint 后复制不可变 DB snapshot 和查询摘要到 evidence workspace；Attempt 与 EDA 产物始终保存在项目 workspace。此约束应延续到后续单机 Runtime 部署。

旧 `ORFSRunner`、JobStore、Web 与 P1 Runtime 回归均保留；最终全量自动测试为 53 passed。
