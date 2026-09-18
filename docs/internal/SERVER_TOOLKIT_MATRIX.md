# 当前服务器 Toolkit 验收矩阵

本文件记录当前 `eda-node01` 上通过 Foundation 验证的 Toolkit 入口。工具
安装和 modulefile 属于服务器部署；本仓库只维护 adapter 协议和用户目录中的
Toolkit 包，不复制商业 EDA 二进制。

## Toolkit 位置与能力

| Toolkit | 用户目录 | 能力 | 环境入口 | 当前结果 |
| --- | --- | --- | --- | --- |
| ORFS | `~/toolkits/chipevolve-orfs` | `preflight`、`finish`、`script` | 当前 ORFS checkout + 显式 OpenROAD/Yosys/KLayout 路径 | Nangate45 GCD 通过 Foundation `finish` |
| Innovus | `plugins/cadence-innovus`（用户目录 symlink） | `preflight`、`script` | `module load cadence` | Foundation staged Tcl 通过 |
| ICC2 | `~/toolkits/synopsys-icc2` | `preflight`、`script` | `module load synopsys/default`，调用 `icc2_shell` wrapper | 版本和最小 Tcl 通过 |
| PrimeTime | `~/toolkits/synopsys-primetime` | `preflight`、`script` | `module load synopsys/default`，设置 `SYNOPSYS_LC_ROOT=$LC_HOME` | 版本和最小 Tcl 通过 |
| Genus | `~/toolkits/cadence-genus` | `preflight`、`script` | `module load cadence` | 版本和最小 Tcl 通过；保留 OS warning 原始日志 |

所有 Toolkit 都返回共同的基础证据：

- `toolchain.json`：解析后的工具版本和环境入口，不保存部署 license 值；
- `tool.log`：原始工具输出，供服务器内部排错；
- `script_receipt.json`：脚本能力的输入和退出码；
- `tool_version` metric：引用 `toolchain.json` artifact；
- Foundation 计算 artifact SHA-256，并记录 run/attempt/workspace/resource timeline。

## Foundation 验收记录

| 场景 | 记录 |
| --- | --- |
| ORFS Nangate45 GCD | `run-ca5593bca3e2452ea561806a852883a8`，`execution_valid=true`，生成 GDS/ODB/DEF/netlist/report |
| Innovus staged Tcl | `run-8c42c487bec84a7bb1b476288db09887`，`execution_valid=true` |
| ICC2 preflight | `run-8f040c2da0d74f78b1180e5bc6c8af68`，`execution_valid=true` |
| ICC2 staged Tcl | `run-fd608c99920f42019fa172c3942cdaf8`，`execution_valid=true` |
| PrimeTime preflight | `run-4056d42c661d45648facbd7190708061`，`execution_valid=true` |
| PrimeTime staged Tcl | `run-5b6f5f35df1c4adab00be239fde40659`，`execution_valid=true`；通过 Toolkit 注入 `SYNOPSYS_LC_ROOT` 消除 PT-063 |
| Genus preflight | `run-394a60064e4845418b10e3dec08572eb`，`execution_valid=true` |
| Genus staged Tcl | `run-b349dd12813045dd962f9269af6988c0`，`execution_valid=true`；原始日志保留 Rocky 8 unsupported-OS warning |

## 运行方式

用户目录 Toolkit 根目录：

```bash
export OPENROAD_PLATFORM_PLUGINS_ROOT="$HOME/toolkits"
export OPENROAD_PLATFORM_ADMISSIONS_ROOT="$PWD/admissions"
export OPENROAD_PLATFORM_STATE_ROOT="$HOME/.cache/chipevolve-platform/state"
```

真实工具调用必须通过 Foundation plan/worker。直接运行 adapter 只用于协议
smoke，不作为平台验收证据。

## 未覆盖范围

- Innovus/Genus/ICC2/PrimeTime 的完整真实设计 flow、PDK/NDM/Milkyway 和 QoR/signoff；
- Genus 的供应商支持版本替换；
- 多节点调度、cgroup 硬隔离和远程对象存储；
- 服务器 `/opt`、modulefile、EDA 安装和 systemd 的直接修改。
