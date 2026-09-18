# 贡献者指南

[English guide](CONTRIBUTING.md)

这个项目是执行底座，所以贡献应该让执行契约更清楚、更可靠。改代码前先判断功能属于哪一层。新增 EDA 命令通常应该放在外部 Toolkit 仓库，而不是 `core/runtime`。

## 1. 准备干净环境

```bash
git clone <repository-url> openroad-platform-v2
cd openroad-platform-v2
tools/install-local.sh
```

安装脚本会创建本地虚拟环境，并在不自动拉取运行时依赖的情况下安装仓库内各个包。修改包元数据后，如果环境已经存在，请重新执行安装脚本。

先运行最小的真实边界示例：

```bash
python3 -m pytest -q \
  apps/plan_executor/test_against_kernel.py \
  -k test_agent_generated_code_is_executed_and_measured
```

这个测试会启动本地 gateway、worker 和 plan executor，通过正常执行链路测试 Agent 生成的 script，以及 patch/build/benchmark 任务。它不是绕过平台直接调用 adapter 的单元测试。

## 2. 判断代码应该放在哪里

改动前先看[架构总览](docs/ARCHITECTURE_OVERVIEW.zh-CN.md)：

- 公共 task/result/input 字段：`contracts/`；
- workspace、进程、资源、retry、artifact 生命周期：`core/runtime/`；
- Toolkit 发现、manifest、admission：`core/registry/`；
- HTTP 组合和路由：`gateway/` 或对应 app；
- 有序计划和 artifact 传递：`apps/plan_executor/`；
- 工具命令、报告解析器、capability 语义：外部 Toolkit；
- 架构规则：`guardrails/` 加故意失败的 fixture。

Kernel 不应该知道 OpenROAD、Innovus、PrimeTime、ORFS stage 顺序或 QoR 策略。Plan executor 也不应该生成 Agent 没有提交的 flow。

## 3. 新增或修改 Toolkit

Toolkit 应该作为独立目录或独立仓库维护，建议按以下顺序：

1. 复制 `examples/research-toolkit/` 作为起点；
2. 编写 manifest 和 `provenance.json`；
3. 让 adapter 实现 `--request`/`--result`；
4. 在 `artifact_rules` 中声明每一个输出；
5. 让 metric 通过 artifact source path 可追溯；
6. 为经过审核的 revision 添加平台拥有的 admission evidence；
7. 通过真实 Task 或 Plan 验证，而不是只直接启动 adapter；
8. 写清楚工具依赖和一条已知可运行命令。

[Toolkit 协议](docs/PLUGIN_PROTOCOL.zh-CN.md)是字段和拒绝行为的准则。Capability 名称及参数属于 Toolkit 自己的数据，不要要求 kernel 统一它们。

## 4. 行为修改先写测试

修 bug 时先添加一个在旧代码上失败的回归测试，再做最小实现修改。先跑针对性测试，再跑对应包测试。测试应关注可观察状态：run 状态、证据、失败分类和 API 响应，不要把私有方法调用顺序当成行为契约。

新增公共字段时，要更新契约测试，并至少添加一个跨边界测试。生命周期修改要覆盖成功路径，以及对应的失败或取消路径。

## 5. 提交前运行检查

```bash
python3 -m pytest -q
python3 -m pytest -q guardrails
ruff check <修改过的 Python 文件>
mypy --no-error-summary --show-error-codes --cache-dir /tmp/platform-mypy \
  contracts/src core/*/src gateway/src apps/*/src
git diff --check
```

`guardrails/negative/` 下是故意违规的样例，不要为了绿色结果修改它们。不要提高代码规模 ceiling，也不要全局关闭门禁。

真实 ORFS 修改需要运行外部 ORFS 验收，并记录工具版本、路径、revision 和产物证据。不能只根据进程退出码宣布设计实验有效。

## 6. 文档要解释决策

用户看见的项目定位或 quick start 变化时，更新 README；职责和数据流变化时，更新架构总览；Toolkit 接口规则变化时，更新协议；如果存在重要替代方案和取舍，在 `docs/decisions/` 写 ADR。

尽量使用直白语言和完整示例：开发者应该做什么、平台替他做什么、仍然由他负责什么，都要写清楚。

## 7. 提交和发起 PR

每个 commit 只做一件事情，并使用能说明原因的消息：

```bash
git status --short
git diff --check
git add <文件>
git commit -m "fix: preserve artifact evidence on retry"
```

不要提交 `.env`、凭据、生成的 workspace、EDA 数据库或私人工具安装目录。Toolkit 源码和平台 admission evidence 应该让别人能够在不共享 secret 的情况下复现测试。

PR 至少说明：

- 改了什么以及为什么；
- 哪一层和哪一个公共契约受影响；
- 执行了哪些测试和命令；
- 已知限制和后续工作；
- 是否依赖真实 Toolkit 或 EDA 工具。

## 审查标准

另一个开发者应该能从 diff 和测试回答：

1. 用户能观察到什么变化？
2. 哪个组件拥有这个行为？
3. timeout、cancel 和工具失败时会怎样？
4. 结果能否追溯到 workspace、artifact 和 metric？
5. 是否偷偷加入了不必要的 EDA 策略或防御性复杂度？

当前协议是 `v1alpha1`。公共契约变化必须明确说明兼容性影响，不能静默迁移。
