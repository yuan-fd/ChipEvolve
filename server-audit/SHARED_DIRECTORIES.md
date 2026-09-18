# 共享目录和安装说明

- 共享 EDA 安装根由管理员维护；本次核查只读，没有修改其中任何文件。
- 系统 modulefiles 目录提供 `cadence/default`、`synopsys/default`、`openroad`、GCC 和其他模块。
- ORFS 共享树包含多个版本；不能只用 `which` 判断版本，必须记录真实二进制的 `-version` 和 `ldd`。
- `module show cadence`/`module show synopsys` 是部署成员应使用的入口；报告不复制许可证地址。
- 共享 ORFS 树的 `flow/` 只能作为只读源。每个执行尝试必须把 `WORK_HOME` 指到自己的 workspace；禁止直接在共享树运行会写 `results/`、`logs/` 或 `objects/` 的 `make`。
- 服务器存在多个用户的 OpenROAD、KLayout、ORFS 和长期运行脚本。本次只记录，没有终止或修改任何他人进程。
