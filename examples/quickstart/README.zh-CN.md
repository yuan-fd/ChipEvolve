# 快速开始：执行一次 Agent 生成的任务

[English version](README.md)

这是新贡献者最短的上手路径。它使用仓库内的 Research Toolkit，以及真实的 gateway、worker 和 plan executor。不需要 OpenROAD、license server 或 vendor 工具安装。

## 运行

在仓库根目录执行：

~~~bash
tools/install-local.sh
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest -q \
  apps/plan_executor/test_against_kernel.py \
  -k test_agent_generated_code_is_executed_and_measured
~~~

参数化测试会运行三种情况：

- `script`：Agent 生成的 Python 脚本写出报告；
- `patch_benchmark`：Agent 生成的 patch 修改小型 C 程序，然后 adapter 构建并比较 baseline 和 candidate；
- `build_failure`：候选代码无法编译，平台记录 Toolkit build failure。

测试会创建临时 plugin、admission 和 state 目录，启动 HTTP 服务和 worker，提交 Plan，等待终态，并检查 artifact、metric、输入 digest、日志和 timeline。结束时会清理服务。

## 真实 Toolkit 应该复制什么

测试本身是自包含的，但真正适合参考的实现是 [Research Toolkit](../research-toolkit/)。其中 adapter 演示：

- 读取不可变 request；
- 接收 Agent 参数和 staged 文件；
- 执行 Agent 脚本；
- 应用 patch 并执行 benchmark；
- 写出 artifact 和 metric 证据；
- 区分 configuration、patch、build 和 benchmark 失败。

复制后替换 capability 名称和工具命令，同时保留边界：adapter 负责工具语义，平台负责 workspace、进程、timeout、cancel、artifact 登记和证据。

## 什么算成功

测试通过说明这种 Toolkit 形状的执行生命周期可用，不代表机器已安装真实 EDA 工具，也不代表设计满足物理 signoff。真实 EDA 验收请使用外部 ORFS Toolkit 和 [GCD 验收记录](../../docs/internal/GCD_ACCEPTANCE_2026-09-18.md)。
