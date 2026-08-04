# 下一步

updated_at: 2026-08-04

实施 P4 RTLScout 黑箱插件：版本化 manifest/schema、Adapter、安全环境注入、fake smoke、RTL 产物登记，以及 RTLScout→ORFS 组合工作流。真实 LLM 若因 Python 环境或凭据不可用，必须以可复核的 external blocker 明确记录。

首条恢复命令：

```bash
cd ~/openroad-platform && git status --short && sed -n '1,240p' tasks/current_task.md
```
