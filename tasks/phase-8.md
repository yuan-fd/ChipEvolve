# P8 任务：TaiWei-Pin-3D 黑箱插件

status: completed_with_external_blocker
phase: P8
started_at: 2026-08-04
completed_at: 2026-08-04
base_commit: 339f34f

## 白名单

- `packages/execution/`、`integrations/taiwei_pin_3d/`：固定源码/工具链 profile 与黑箱 Adapter。
- `tests/test_taiwei_plugin.py`、P8 任务、证据、进度和 memory snapshot。
- 第三方固定源码/工具链只允许位于 ignored `.external-src/`、`.tools/`；验收产物在 ignored `runs/p8-*`。

## 禁止范围

- 不用平台 2D ORFS/OpenROAD 冒充官方 3D 工具链。
- 不修改 TaiWei、ORFS-Research、OpenROAD、Yosys 或 PDK 源码；执行使用 workspace 内源码快照。
- 不使用 Cadence/commercial flow；只允许 `ord/asap7_3D/gcd`。
- 不把 fake adapter fixture 称为真实 3D QoR/GDS。

## 验收门

- TaiWei `db201367...`、ORFS-Research `568eb04...`、OpenROAD `305d3ba...` 全部匹配且源码 clean 才能构造生产 manifest。
- Adapter 只在 Attempt workspace 执行，登记 eval、summary、GDS、3D view、log 和 toolchain snapshot。
- 固定 gcd 真实流程退出 0 且产物非空/哈希有效才能标记 real_3d=true。
- 若固定工具链因外部下载/构建不可得，必须 fail closed 并保存探测命令、现有不匹配版本和可复核 blocker；协议仍做进程级 fixture 验收。

## 预算与停止条件

- 真实 gcd 最多一次，超时 6 小时，并发 1；工具链同根因最多 3 次探测。
- 不在当前阶段从不受信任镜像或其他用户私有工作区复制二进制。
