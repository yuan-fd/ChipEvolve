# P3 里程碑：三平台兼容性准入

captured_at: 2026-08-04
status: completed

- RTLScout `87a00edf...` / BSD-3-Clause-Clear：条件准入。系统 Python 3.9 不满足上游 `>=3.10`；入口实测退出 1（缺 `dotenv`）；Python 3.11 ARM 主要 wheels 可下载。P4 可完成契约、Adapter 和受控 fake；真实 LLM 必须另有合适环境与凭据。
- AgenticPD `4322a25c...` / 无 LICENSE：条件准入。临时副本 `make check` 87+17+167 全过。平台只黑箱调用/转换 proposal，禁止复制、修改和再分发源码，Runtime 保持唯一状态权威。
- TaiWei `db201367...` / BSD-3-Clause：条件准入。入口 help 通过；真实 3D 必须隔离 ORFS-Research `568eb04...` 与 OpenROAD `305d3ba...`，不能覆盖 2D 工具链。
- 原始固定源码前后 clean；没有运行付费 LLM，没有修改共享工具链。
- 机器可读和人读证据：`docs/evidence/P3_PLUGIN_ADMISSION.json`、`.md`。
