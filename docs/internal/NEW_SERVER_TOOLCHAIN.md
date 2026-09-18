# 新服务器工具链接入复现

所有命令都应在临时目录或 Foundation attempt workspace 中执行。共享 ORFS 树只读，输出使用独立 `WORK_HOME`。

## 1. 解析开源工具

```bash
module show openroad
ORFS_ROOT=<shared-orfs-root>
OR=$ORFS_ROOT/versions/<revision>/tools/install/OpenROAD/bin/openroad
YOSYS=$ORFS_ROOT/versions/<revision>/tools/install/yosys/bin/yosys
STA=$ORFS_ROOT/versions/<revision>/tools/install/OpenROAD/bin/sta
KLAYOUT=$(command -v klayout)
"$OR" -version
"$YOSYS" --version
"$STA" -version
"$KLAYOUT" -v
ldd "$OR" | grep 'not found' && exit 1 || true
```

## 2. ORFS 独立 GCD 验证

```bash
WORK_HOME=$(mktemp -d)
make -C "$ORFS_ROOT/versions/<revision>/flow" -j1 \
  DESIGN_CONFIG=./designs/nangate45/gcd/config.mk \
  WORK_HOME="$WORK_HOME" FLOW_VARIANT=server_audit NUM_CORES=2 \
  OPENROAD_EXE="$OR" YOSYS_EXE="$YOSYS" KLAYOUT_CMD="$KLAYOUT" \
  EQUIVALENCE_CHECK=0 LEC_CHECK=0 finish
test -s "$WORK_HOME/results/nangate45/gcd/server_audit/6_final.gds"
test -s "$WORK_HOME/results/nangate45/gcd/server_audit/6_final.odb"
test -s "$WORK_HOME/results/nangate45/gcd/server_audit/6_final.def"
test -s "$WORK_HOME/results/nangate45/gcd/server_audit/6_final.v"
sha256sum "$WORK_HOME/results/nangate45/gcd/server_audit/6_final."{gds,odb,def,v}
```

## 3. Innovus Toolkit 验证

先把 `plugins/cadence-innovus` 和 `admissions/cadence-innovus.json` 纳入部署，再通过 API/worker 提交 Task。任务输入只提供部署解析出的 `tool_path`、模块名和 Agent-staged `script_path`：

```json
{
  "plugin_id": "cadence-innovus",
  "inputs": {
    "capability": "script",
    "tool_path": "<resolved-innovus-path>",
    "module": "cadence",
    "script_path": "hello.tcl"
  },
  "staged_inputs": [{"source": "<agent-file>", "destination": "hello.tcl"}],
  "expected_artifacts": ["toolchain", "tool_log", "script_receipt"]
}
```

`hello.tcl` 的最小内容是 `exit`。成功标准是 run/attempt 均为 `succeeded`，并可查询 toolchain、tool_log、script_receipt、输入 hash 和 `tool_version` metric；这不等于任何设计 QoR 达标。

## 4. 当前服务器用户目录 Toolkit

真实服务器当前使用：

```bash
export OPENROAD_PLATFORM_PLUGINS_ROOT="$HOME/toolkits"
export OPENROAD_PLATFORM_ADMISSIONS_ROOT="$PWD/admissions"
export OPENROAD_PLATFORM_STATE_ROOT="$HOME/.cache/chipevolve-platform/state"
```

Toolkit 目录为：

```text
$HOME/toolkits/chipevolve-orfs
$HOME/toolkits/synopsys-icc2
$HOME/toolkits/synopsys-primetime
$HOME/toolkits/cadence-genus
```

商业 Toolkit 的 `preflight`/`script` 均通过 Foundation plan/worker 执行。ICC2 必须
使用 `module load synopsys/default` 的 `icc2_shell` wrapper；PrimeTime wrapper
需要在 module 加载后设置 `SYNOPSYS_LC_ROOT="$LC_HOME"`。license 不属于 Toolkit
manifest 或 preflight 门禁，原始输出只在 attempt 的 `tool.log` 中保存。
