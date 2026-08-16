# OpenROAD 自演化开放芯片设计平台

> [English](README.md) · **中文**

一个面向芯片设计自动化的开放平台：2D / 3D 物理设计 + AI 自演化学习。

---

## 平台定位

把「芯片设计」从手工流程变成 **自动化 + 会学习** 的开放平台：

- **自动跑通**：输入 RTL 图纸，自动完成 2D / 3D 芯片物理设计（综合 → 布局 → 布线 → 版图），全程留档可回溯；
- **会学习**：每次成功经验自动沉淀进知识库，AI 参考历史给出下一步参数建议（自演化闭环）；
- **开放扩展**：插件式架构，新 EDA 工具只需按接口写一个适配器即可接入，互不干扰。

一句话：**图纸进去，版图出来，经验留下，越用越聪明。**

---

## 功能清单

| 功能 | 状态 | 说明 |
| --- | --- | --- |
| 2D 物理设计（ORFS 六阶段） | ✅ 已跑通 | Nangate45 RTL→GDS 全流程真实验证 |
| 3D 物理设计（TaiWei） | ✅ 已跑通 | 3 种工艺库 × 任意设计，3 个真实变体验证 |
| 网页工作台（六页） | ✅ 已可用 | Overview / Frontend / Backend / Projects / Extensions / Self-Evolution |
| 自然语言生成 RTL | ✅ 已可用 | Spec-to-RTL，需人工确认后登记 |
| 自演化学习（知识入库 + AI 建议） | ✅ 已可用 | 入库链路实测通过（admitted）；建议采用 GP/BO + 行为克隆 |
| 插件生态 | ✅ 架构就绪 | TaiWei / RTLScout / AgenticPD / EDACraft / ImplCraft / DPLEvolve |
| 免登录内部模式 | ✅ 已可用 | `OPENROAD_PLATFORM_NO_AUTH=1` 跳过注册，共享工作区 |
| 批量实验（Campaign / Agent 搜索） | 🚧 部分可用 | 创建候选计划并人工确认后执行 |
| LLM 在线优化（需模型服务） | 🚧 需配置 | BYOK 或共享模型，凭据仅存内存 |

> 完整能力地图见网页 Overview 页与 [教程 01](docs/tutorials/01_openroad_platform_overview.html)。

---

## 目录树

```text
openroad-platform/
├── apps/
│   ├── api/                 # 后台服务：HTTP 接口、设计/任务/学习服务
│   └── web/                 # 前端网页（六页工作台，中英双语）
├── packages/
│   ├── contracts/           # 数据契约：任务单(TaskSpec)/插件声明/产物规则
│   ├── scheduler/           # 调度：SQLite 队列、Runtime、worker、campaign
│   ├── execution/           # 执行：插件注册表、2D/3D 适配器、进程隔离
│   ├── analysis/            # 分析+学习：指标分析、知识入库、GP/BO、AI 建议
│   └── visualization/       # 可视化：原理图(Graphviz)、版图(KLayout)、3D 视图
├── integrations/            # 插件声明与固定源码审计（taiwei/rtlscout/...）
├── workflows/               # 标准流程说明（spec-to-gds / three_d / ...）
├── scripts/                 # 启动、worker、验收、工具链构建脚本
├── tests/                   # 自动测试（pytest）
├── docs/                    # 文档：架构、教程(HTML)、操作、插件指南
├── knowledge/               # 公开知识语料
├── project_kb/              # 技术决策与经验记录
├── var/                     # 运行证据（git 忽略，勿删）
└── .tools/  .external-src/  # 本地工具链 / 固定第三方源码（git 忽略）
```

---

## 开发模式

- **插件式并行开发**：每个插件是独立的 `xxx_plugin.py` + `xxx_adapter.py` 文件对，
  插件之间零依赖；新插件只需 ①新建自己的文件对 ②在 `execution/__init__.py` 导出
  ③在 `app.py` 挂载 manifest。不同插件的开发者互不冲突。
- **分支流程**：功能分支开发 → 本地提交 → 跑全量测试 → 合入 main。
- **测试**：`python3 -m pytest -q`（当前 215 passed / 2 failed，2 个为环境性）。

> 插件开发详细指南见 [docs/PLUGINS.md](docs/PLUGINS.md) 与 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## 接口与插件说明

平台对外提供 REST API（网页全部功能均可通过 API 调用）：

| 接口 | 作用 |
| --- | --- |
| `/api/auth/*` | 登录注册（免登录模式可跳过） |
| `/api/designs/*` | 设计登记 / 导入 / 生成 |
| `/api/runtime/runs/*` | 2D/3D 任务提交、进度、取消、产物 |
| `/api/extensions/taiwei/run` | 3D 任务提交（工艺库/参数） |
| `/api/extensions/edacraft/*` | 专业小工具（TCAD/SPICE 等） |
| `/api/platform/results` | 项目与结果列表 |
| `/api/runtime/runs/<id>/collect-learning` | 知识入库 |
| `/api/learning/observations` | 查看已入库知识 |
| `/api/recommendations/*` | AI 建议与人工决策 |

**插件三件套**：① `plugin.json`（身份证：能力/工具/产物规则）② `xxx_plugin.py`
（任务单生成器）③ `xxx_adapter.py`（机器操作员：翻译任务单→跑工具→收产物）。
符合契约即插即用，详见 [docs/PLUGINS.md](docs/PLUGINS.md)。

---

## 快速启动

### 方式一：同一服务器（无需 clone）

仓库已在 `~/openroad-platform`，直接启动：

```bash
cd ~/openroad-platform
HOST=127.0.0.1 PORT=8000 ./scripts/run_demo.sh
# 免登录内部模式（可选）：export OPENROAD_PLATFORM_NO_AUTH=1 后再启动
```

浏览器打开 `http://127.0.0.1:8000`（远程机器用 SSH 隧道）：

```bash
ssh -N -L 8000:127.0.0.1:8000 <用户名>@<服务器>
```

### 方式二：新机器 clone

```bash
git clone https://github.com/CODA-Team/ChipEvolve.git
cd ChipEvolve
python3 -m pip install -e '.[test,visualization]'   # 安装平台本体（可选依赖）
./scripts/run_demo.sh                                # 启动
```

### 分别启动 worker 和 web（推荐）

```bash
export PLATFORM_STATE=/tmp/openroad-platform-$UID
mkdir -p "$PLATFORM_STATE"

# 终端 1：worker
python3 scripts/run_runtime_worker.py \
  --db var/platform.db --orfs-root ../OpenROAD-flow-scripts \
  --runtime-db "$PLATFORM_STATE/runtime.db" --campaign-db "$PLATFORM_STATE/campaign.db"

# 终端 2：web
python3 apps/api/app.py --host 127.0.0.1 --port 8000 \
  --db var/platform.db --orfs-root ../OpenROAD-flow-scripts \
  --runtime-db "$PLATFORM_STATE/runtime.db" --campaign-db "$PLATFORM_STATE/campaign.db"
```

### 5 分钟上手

1. 打开网页 → 导入一个 RTL 设计（或用内置示例）；
2. Backend 页 → 选设计 → **Start RTL-to-GDS**（2D）；
3. Backend 页 → TaiWei 3D 面板 → 选工艺库/参数 → **Generate 3D**；
4. Projects 页 → 查看版图、指标、产物；
5. Projects 页 → **Collect verified run** → 经验入库 → Self-Evolution 页看建议。

> 完整上手教程见下方「教程入口」。

---

## 环境与依赖

| 组件 | 说明 | 详情 |
| --- | --- | --- |
| 系统 | ARM64 / openEuler 22.03（已验证），Python ≥ 3.9 | — |
| 平台本体 | **零运行时依赖**；可视化可选：KLayout(pya)/Graphviz/Matplotlib/NumPy；测试：pytest | [docs/ENVIRONMENT_BASELINE.md](docs/ENVIRONMENT_BASELINE.md) |
| 2D 工具链 | ORFS + OpenROAD + Yosys（`../OpenROAD-flow-scripts`） | 同上 |
| 3D 工具链 | TaiWei 专用 ORFS-Research/OpenROAD/Yosys（`.tools/taiwei-official-3d`，含 LD_LIBRARY_PATH 配置） | [integrations/taiwei_pin_3d/environment.lock.json](integrations/taiwei_pin_3d/environment.lock.json) |
| 插件工具 | RTLScout：verilator+yosys；AgenticPD：python；DPLEvolve：bash/git/python3 | [docs/PLUGINS.md](docs/PLUGINS.md) |

**环境管理方式**：平台使用 `.tools/` 目录做工具链与 Python 虚拟环境隔离
（每个插件独立 venv + 固定 commit 工具链），通过 `PYTHONPATH` 注入包路径；
git 忽略 `.tools/`、`.external-src/`、`var/`，保证仓库干净、工具链不上传。

> 完整依赖与配置见 [docs/ENVIRONMENT_BASELINE.md](docs/ENVIRONMENT_BASELINE.md)。

---

## 教程入口

| 教程 | 内容 | 链接 |
| --- | --- | --- |
| 平台总览 | 定位、目录树、接口、协作、知识入库 | [01_openroad_platform_overview.html](docs/tutorials/01_openroad_platform_overview.html) |
| TaiWei 3D 原理 | 3D 芯片怎么工作、20 道工序、输入输出 | [02_taiwei_3d_how_it_works.html](docs/tutorials/02_taiwei_3d_how_it_works.html) |
| 自演化问题详解 | 知识入库流程、问题根因 | [03_self_evolution_issue.html](docs/tutorials/03_self_evolution_issue.html) |
| 多人协作开发 | Git 流程、模块分工、加新插件 | [04_collaboration_guide.html](docs/tutorials/04_collaboration_guide.html) |
| 为什么可以自演化 | GP/BO、离线 RL 的原理答疑 | [05_why_self_evolution.html](docs/tutorials/05_why_self_evolution.html) |
| AI for EDA 对照 | 参考 Si2 标准的数据对照 | [06_ai_for_eda_si2_mapping.html](docs/tutorials/06_ai_for_eda_si2_mapping.html) |

> HTML 教程为通俗讲解，双击即可本地打开；也可直接在 GitHub 页面点击查看源码。

---

## 更多文档

- [docs/PLUGINS.md](docs/PLUGINS.md) — 插件开发完整指南
- [docs/OPERATIONS.md](docs/OPERATIONS.md) — 运维：备份/恢复/取消/工具链升级
- [docs/self_evolution_report.md](docs/self_evolution_report.md) — 自演化入库审计报告（技术底稿）
- [CONTRIBUTING.md](CONTRIBUTING.md) — 贡献规范
