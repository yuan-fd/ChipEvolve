# 常见问题

[English version](FAQ.md)

## Codex 本来就会写脚本，为什么还需要这个平台？

Codex 会判断要写什么脚本，但一次实验还需要输入快照、工作目录、进程清理、超时、日志和能追溯的输出。如果没有共同执行层，每个 Agent 都要重新做这些管理工作。这里仍由 Agent 决定实验，平台负责管理和记录执行。

## 这是不是 OpenROAD 自动化工作台？

不是。目录名和 Python 包名沿用了历史名称，但 kernel 不包含 OpenROAD 或 ORFS flow 逻辑。真实 ORFS 集成在外部 Toolkit 中。Innovus 和 PrimeTime 是可接入的方向，不是已经完成验证的工具。新服务器先跑[本地示例](../examples/quickstart/README.zh-CN.md)，它不需要任何 EDA 安装。

## 我到底应该实现 Toolkit 还是 Plugin？

实现一个带 Adapter 的 Toolkit，再使用现有 plugin manifest 打包。Toolkit 表示能力集合；Adapter 负责启动工具和收集结果；Plugin 表示可发现的包。它们是不同职责，不是三套竞争协议。保留 `plugin_id`，不用再发明一个 API。

## Agent 可以提交自己生成的脚本或 patch 吗？

可以。使用 `staged_inputs` 提交文件，选择一个支持执行脚本或应用 patch、编译代码的 Toolkit capability。Kernel 不解析 TCL、Python 或 patch 的语义，具体见[脚本与 patch 示例](../examples/research-toolkit/README.zh-CN.md)。

只能运行可信代码。独立 workspace 不是操作系统安全沙箱。

## 能移植到团队另一台服务器吗？

可以，但本机成功不代表另一台机器的依赖已经齐全。需要克隆平台和外部 Toolkit，安装平台包，选择全新的可写状态目录，配置绝对工具路径、解释器和库路径，先跑本地示例，再通过 Plan API 跑一个小型真实设计。记录那台机器的架构、工具版本、Toolkit revision、输入、日志和产物哈希。

不要直接复制运行中的 SQLite 目录，也不要照搬本机绝对路径。若要保存已有实验，先停止服务，再一致性备份整个状态目录。迁移旧状态及其中的绝对 workspace 路径，目前不是已经验证的操作流程。

## 队友怎么连接这台服务器？

当前使用服务器上的可信账号或 SSH 转发。启动脚本监听 `127.0.0.1:8700` 和 `127.0.0.1:8840`。回环地址表示“本机”，不代表“只有当前 Unix 用户能访问”。无认证实例属于同一个信任范围，不提供多用户所有权隔离。

大家协调使用一个实例；若各自运行实例，需要不同的端口和状态目录。从自己的电脑转发 gateway 的示例：

```bash
ssh -L 18700:127.0.0.1:8700 user@team-server
```

电脑上使用 `http://127.0.0.1:18700` 访问。注意输入 source 路径仍然指服务器文件，不是自己电脑上的文件。

## Catalogue 里找不到 Toolkit，先检查什么？

扫描根目录下面要有子目录，子目录里放 `*.plugin.json`。把 `OPENROAD_PLATFORM_PLUGINS_ROOT` 指向这些子目录的父目录，不是 manifest 文件。先校验：

```bash
.venv/bin/openroad-platform-plugin-validate /absolute/path/to/my-toolkit
```

这个命令不执行工具，也不授予准入资格。

## 看得到 Toolkit，但为什么拒绝执行？

检查主机架构、请求版本和平台 admission record。准入需要 license review 和被审核的 commit。Provenance 如果声明了 commit，就必须和 admission 一致。不能把示例里的全零 fixture revision 复制成真实 Toolkit 审批。

## 在我的 shell 能运行，进入 attempt 就找不到工具或库？

Adapter 只继承允许列表中的主机环境，以及 manifest 指定的环境。交互 shell 的 `PATH`、`LD_LIBRARY_PATH`、虚拟环境和 license 配置可能没有传进去。使用绝对 adapter 路径，明确声明非敏感环境，并检查动态库。

真实凭据不要放入 Git 或任务输入证据；敏感环境配置按团队规则保存在本机。

## 工具退出码为零，平台为什么还判失败？

Adapter 还必须写合法 result，里面的退出码要和真实进程一致。必需产物必须存在、非空且位于 workspace 内，指标来源必须能解析到已登记产物。缺 result、缺输出或指标来源错误，都可能使运行失败。请一起看 failure、日志和产物，不要只看退出码。

## `execution_valid: true` 是不是表示设计达标？

不是。它只表示计划所有步骤执行成功，不证明 timing、DRC、signoff 或科学实验目标达标。领域规则由 Toolkit/evaluator 定义，Agent 负责判断不同结果是否可以比较。

## 重复提交同一个 plan，为什么不是一个新实验？

Plan ID 是持久化身份。新实验请使用新的 `plan_id`，不要期待重复提交能重跑已有计划。子任务使用稳定、按 plan 分隔的幂等 key；同一个 kernel 幂等 key 携带不同任务内容属于冲突，不是新运行。

## 任务一直 queued，怎么排查？

确认 worker 在运行，并与 gateway 使用相同的 state、plugin 和 admission 目录。再检查资源申请是否超过可用容量或被已有 reservation 占用。HTTP health 正常不代表 worker 一定在消费队列。

## 能硬限制 CPU 和内存吗？

目前不是硬隔离。平台做容量预留和进程树采样，发现超限后终止 attempt；这不是 cgroup 硬限制，也不是容器沙箱。共享服务器应明确配置保守的可用容量。

## 现在是不是全仓质量清零了？

不是。最近记录的全量测试是 438 passed、1 个条件跳过，39 个架构测试通过。改动范围内 Ruff 和正式源码 mypy 通过。全仓历史格式化和静态检查债务记录在[审计报告](internal/EXECUTION_AUDIT_REPORT.md)中，不能把测试通过说成所有质量检查清零。

## 报问题要附什么？

附上平台与 Toolkit commit、操作系统和架构、失败命令、脱敏后的 task/plan、plan ID、run ID、failure category 和相关日志。工具链问题再附可执行文件路径、版本及缺失库报错。不要向公开 issue 上传保密设计、license 凭据或大型工具数据库。
