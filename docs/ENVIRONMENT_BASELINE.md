# 环境基线

审计日期：2026-08-04

## 主机

| 项目 | 事实 |
| --- | --- |
| 主机架构 | `aarch64` |
| 操作系统 | openEuler 22.03 LTS-SP3 |
| CPU 可见核数 | 64 |
| 内存 | 1.0 TiB，总可用约 953 GiB（审计时） |
| 共享文件系统 | 99 TiB，总可用约 97 TiB（审计时） |
| sudo | 不需要且禁止使用 |

资源数字只代表审计时快照，不是任务可用配额。真实并发和长任务预算必须在对应阶段任务中另行声明。

## 平台语言与构建工具

| 工具 | 版本/状态 |
| --- | --- |
| Python | 3.9.9 |
| Git | 2.33.0 |
| GNU Make | 4.3 |
| Conda | 用户目录环境已存在；服务进程不依赖交互 shell 的 PATH |

核心平台保持 Python 3.9 兼容。要求 Python 3.10 及以上的插件必须使用独立环境，不能升级系统 Python 或污染共享环境。

## 内部 2D EDA 工具链

| 组件 | 固定事实 |
| --- | --- |
| ORFS | commit `51ad1231a231ee85234c06db807688d029b85c35` |
| OpenROAD | `26Q1-1961-g63ed2e0fe5`；submodule commit `63ed2e0fe5992099b7d528177bbb7a4df9523907` |
| Yosys | `0.63`；submodule commit `d3e297fcd479247322f83d14f42b3556db7acdfb` |
| ORFS remote | The-OpenROAD-Project/OpenROAD-flow-scripts |
| 可见 platform | asap7、gf180、ihp-sg13g2、nangate45、sky130hd、sky130hs、sky130io、sky130ram |

执行路径以运行时 ToolchainSnapshot 为准，不允许插件依赖隐式 cwd 或全局 PATH。共享 ORFS、OpenROAD、Yosys 和 PDK 默认为只读。

## 外部插件源码

精确仓库、commit、license 和入口见 `integrations/plugins.lock.json`。源码缓存在 `.external-src/`，三个仓库均已 detached 到批准的 commit，缓存不进入 Git。

## 已知环境差异

1. RTLScout 要求 Python >=3.10；其隔离环境和 Verilator 必须使用固定绝对路径，不能假设登录 shell 已导出 PATH。
2. AgenticPD 报告环境为 WSL2/x86_64/Python 3.10，与本机不同；需独立环境。
3. TaiWei 声明的 ORFS-Research/OpenROAD commit 与内部 2D 基线不同；必须配置独立 ToolchainSnapshot，禁止覆盖共享工具链。
4. PDK 缺少统一独立 revision 字段；P1/P2 必须至少记录 platform 配置哈希及可取得的 PDK 来源信息。

## 安全基线

- 项目不保存 token、密码、代理凭据或 `.env`。
- 网络仅用于检查并下载经批准的官方源码，禁止 `curl|sh`、`wget|bash`。
- API/Web 当前仅适合可信网络或 SSH tunnel；认证与 TLS 不属于 P0。
- 原始日志和运行产物只读；摘要必须引用原始路径与哈希。

## RTL 验证工具（2026-08-24 复核）

| 组件 | 已核实安装 | 平台使用方式 |
| --- | --- | --- |
| Verilator | `/share/home/yuanwenjie/.local/opt/openroad-rtl-tools/bin/verilator`，`5.050` | API 启动时优先采用此绝对路径；不需要 sudo、Docker 或浏览器配置 |
| RTLScout 专用 Verilator | `.tools/verilator-5.040/bin/verilator`，`5.040` | 仅供已固定版本的 RTLScout 插件使用，避免影响其它任务 |

普通终端中直接输入 `verilator` 可能找不到它，因为该用户目录不在默认 `PATH`；这不是未安装。需要手动检查时请运行上述绝对路径，不要修改系统目录或使用 sudo。
