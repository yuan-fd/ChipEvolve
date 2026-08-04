# P2 ORFS Plugin 里程碑快照

snapshot_type: milestone
created_at: 2026-08-04
append_only: true

## 可恢复锚点

- P2 实现：`aa7cf0a8b3b2feaa1e16f2a1bad45e612b89beef`。
- 标准插件：`orfs@1.0.0`，能力 `eda.orfs` / `eda.rtl_to_gds`。
- 真实证据：`docs/evidence/P2_ORFS_ACCEPTANCE.md` 和 `.json`。
- 自动验证：53 tests passed。

## 真实验收事实

- Run `d154f31a0ac64e3cb068329dfcde3149`，1 StageRun，1 Attempt，均 succeeded。
- Nangate45 `mux_2to1` 六阶段全部 succeeded，总墙钟 77.05 秒。
- 17 Artifacts、7 Metrics、29 Events；结束序列为 Attempt succeeded、Run succeeded。
- `implementation_valid=true`、`gds_complete=true`。
- final GDS：19,572 bytes，SHA-256 `d20ee44ef216af20a896b4a48794d2ee3fdd8de70b7fe8280fb8ae13a59ad1e6`。
- 共享 ORFS/OpenROAD/Yosys/KLayout 和 Nangate45 配置前后指纹完全一致。

## P2 固定不变量

- TaskSpec 中 RTL 必须携带 size/SHA-256；adapter 校验后复制到 Attempt workspace。
- ORFSRunner 继续支持旧 API，但执行环境由 ToolchainConfig allowlist 构造。
- ToolchainSnapshot 记录版本、commit、工作树状态、二进制/配置/RTL 哈希和请求参数。
- Runtime 独占终态；ORFS adapter 只返回 PluginResult 和 workspace-relative artifacts。
- nested make 在 cancel/timeout 时必须随 adapter 收敛，不得留下子进程。
- 当前 GlusterFS 不承载 SQLite WAL；live DB 使用节点本地存储，checkpoint 后归档快照/摘要。

## 未改变的后续门槛

- P3 才审计并 smoke RTLScout、AgenticPD、TaiWei；P2 未接入三者。
- AgenticPD 未声明 LICENSE，澄清前不得复制/再分发。
- TaiWei 保持独立 ORFS-Research/OpenROAD 工具链，不能覆盖 2D baseline。
- 未经 P3 授权，不创建外部平台环境、不安装系统依赖、不运行 LLM。
