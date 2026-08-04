# TaiWei 官方固定链许可证审计

captured_at: 2026-08-05

| 组件 | 固定来源 | 许可证 | 结论 |
| --- | --- | --- | --- |
| TaiWei-Pin-3D | `db201367...` root `LICENSE` | BSD-3-Clause | 本地执行允许；保留原声明 |
| ORFS-Research build/run scripts | `568eb04...` `LICENSE_BUILD_RUN_SCRIPTS` | BSD-3-Clause | 仅覆盖 build/run scripts |
| OpenROAD | `305d3ba...` root `LICENSE` | BSD-3-Clause | 本地构建执行允许 |
| Yosys | `77005b6...` root `COPYING` | ISC | 本地构建执行允许 |
| ASAP7 3D data | TaiWei fixed tree `platforms/asap7_3D` | 未发现独立 license 文件 | 仅限私有本地验收，不复制进 Git、不发布、不再分发 |

各 license 文件的 SHA-256 已写入 `environment.lock.json`。ORFS 明确说明工具、平台和 design 可能各有独立许可，因此本审计不把 ORFS 脚本许可证错误扩展到 ASAP7 数据。当前 local release candidate 只包含平台代码、lock 和补丁；`.external-src/`、`.tools/`、PDK、第三方源码和构建产物均被 Git 忽略。

如果 P11 或后续部署需要向其他机器分发 ASAP7 3D 数据，必须先取得明确的分发许可；现有验收不提供这项授权。
