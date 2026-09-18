# 环境与复现入口

当前登录 shell 没有持久的 EDA 环境变量；Cadence、Synopsys、OpenROAD 通过 Environment Modules 提供。建议每个 Toolkit Adapter 在自己的 preflight 中解析模块和工具版本，不依赖开发者 shell 偶然继承的 `PATH`。

建议部署变量（值由管理员或任务提交者提供，不进仓库）：

```bash
export ORFS_ROOT=<shared-orfs-root>
export CADENCE_MODULE=cadence
export SYNOPSYS_MODULE=synopsys
export INNOVUS_BIN=<resolved-innovus-executable>
export OPENROAD_BIN=<resolved-openroad-executable>
export YOSYS_BIN=<resolved-yosys-executable>
export KLAYOUT_BIN=<resolved-klayout-executable>
```

Toolkit 应把 `module`、`tool_path` 和 capability 参数放在 Agent 的 task `inputs` 中，或由部署模板注入；Execution Foundation 只负责 staging、workspace、进程、日志和 evidence。
