# 工具路径与 preflight

绝对安装根目录由服务器管理员维护，报告使用环境变量占位符，避免把本机路径或许可证信息写进仓库。先执行 `module show <name>`，再把解析结果作为任务输入或部署配置传给 Toolkit。

| 工具 | 解析方式 | 版本/检查结果 | 结论 |
| --- | --- | --- | --- |
| OpenROAD | `$ORFS_ROOT/versions/<revision>/tools/install/OpenROAD/bin/openroad` | 真实二进制 `-version` 输出 `26Q1-2966-g29d97c45b3`；`ldd` 无 `not found` | 已确认可执行 |
| OpenSTA | 与 OpenROAD 同一安装树下的 `sta` | `3.1.0`；动态库完整 | 已确认可执行 |
| Yosys | `$ORFS_ROOT/versions/<revision>/tools/install/yosys/bin/yosys` | `0.63 (git d3e297fcd)`；动态库完整 | 已确认可执行 |
| KLayout | `command -v klayout`（当前为系统命令） | `0.30.7`；批处理调用可用，GUI 无 DISPLAY 不可用 | 批处理可用 |
| ORFS | `$ORFS_ROOT/versions/20260409` | `flow/`、Nangate45/Sky130/ASAP7 等平台及 GCD 设计存在 | 已确认可读；输出必须使用独立 `WORK_HOME` |
| Innovus | `module load cadence` 后从 `INNOVUSEXPORT.../bin/innovus` 解析 | 21.39-s058_1；Innovus license checkout succeeded；`-batch -files exit.tcl` 成功 | 商业工具可用，需 Cadence 模块/OA 环境 |
| Genus | `module load cadence` 后从 `GENUS.../tools/bin/genus` 解析 | 21.17-s066_1；license checkout/periodic check 成功，但输出显示 build expiry 为 2020-12-23，且 OS unsupported warning | 只能记为“版本可启动但供应商支持/版本有效性需管理员确认” |
| PrimeTime | `module load synopsys` 后调用 `pt_shell` | `-version` 在 8 秒 preflight 内未结束，未确认 license/启动 | 尚未验证/阻塞 |
| ICC2 | `module load synopsys` 后调用 `icc2_shell` | 立即报 `libkrb5.so.3` 缺少 `krb5int_c_deprecated_enctype` 符号 | 路径存在但无法执行 |
| Design Compiler | `module load synopsys` 后调用 `dc_shell` | `T-2022.03-SP5` 版本信息可输出；未运行综合 | 仅版本已确认 |

复现商业工具的安全最小检查（在临时目录执行，不把输出提交仓库）：

```bash
module show cadence
module load cadence
command -v innovus
innovus -version
printf 'exit\n' > /tmp/innovus-preflight.tcl
innovus -batch -files /tmp/innovus-preflight.tcl
```

许可证只记录“checkout succeeded/失败或未验证”。不要把 `CDS_LIC_FILE`、`LM_LICENSE_FILE`、`SNPSLMD_LICENSE_FILE` 的值写入日志或提交。
