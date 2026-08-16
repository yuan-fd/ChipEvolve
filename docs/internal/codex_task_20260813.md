# Task: TaiWei 3D 一键引导（阶段 2.2）

## 1. 用户原始提示词

> 之前我们遇到的问题是插件的兼容性很差 比如Craft 3D RTLScout 使用起来无法使用或者极其麻烦
> 我预想的是：3D可以一键生成3D结果
> 但实际用起来有各种各样的问题 比如 3D支持的格式很窄 使用起来很麻烦或者没法使用

（本任务只做 TaiWei 的 Web 层"引导式一键"体验。TaiWei 工具链本身仍只支持官方 gcd/asap7_3D —— 这是已验证的适配边界，不做工具链扩展。）

## 2. 仓库路径 + 分支约束

- REPO_PATH=/share/home/yuanwenjie/openroad-platform
- BRANCH=main（保留 1.1-1.3 未提交改动）
- 允许写入：是（仅白名单文件）

## 3. 背景（事实，来自审计）

- `apps/api/app.py` `submit_taiwei_design_run()` 现状：
  - `if design.get("module") != "gcd": raise ValueError("...validated only for TaiWei's official gcd configuration...")` —— 对非 gcd 设计**直接报错**。
  - 要求 `baseline_run_id` 是一个 succeeded 的 2D ORFS run（同一设计），否则报错 "A succeeded 2D ORFS run for the same registered design is required"。
- 用户视角问题：选错设计 → 裸错误；没有 2D baseline → 裸错误；用户不知道要 gcd、不知道要先跑 2D。体验 = "没法使用"。
- 前端 Backend 页有 `#embeddedExtensionDetail` 区域加载扩展详情（`data-open-extension="taiwei-3d"`），具体渲染在 app.js。
- TaiWei 真实执行需要 asap7_3D 工具链（已冻结在 .tools，`taiwei_3d_ready=true`）。

## 4. 目标改动（全部在 Web 层，不改 adapter/工具链）

1. `apps/api/app.py` `submit_taiwei_design_run()`：
   - 非 gcd 设计：不再直接抛 ValueError，改为返回结构化引导响应（HTTP 200 + JSON，例如 `{"status": "guidance_required", "reason": "design_not_supported", "message": "...当前 3D 适配仅验证 gcd 设计...", "supported_designs": [...]}`）。**注意**：这是 Web 层体验优化 —— 若你认为保持 400 + 可读 message 更合理（前端可展示 error.message），也可选择 400 + 友好中文/英文 message；二选一，由你判断哪种与现有前端错误处理最一致。
   - 缺 baseline：类似地返回引导（提示先跑 2D baseline，附 2D 提交指引）。
2. `apps/web/assets/app.js`：TaiWei 扩展详情区（taiwei-3d）渲染"引导式流程"：
   - 显示当前所选设计与支持范围说明；
   - 若设计非 gcd → 禁用提交按钮 + 显示"当前 3D 适配仅验证 gcd 设计"；
   - 若无 2D baseline → 显示"先运行 2D RTL-to-GDS baseline"引导（链接到 Backend 主流程）；
   - 满足条件 → 显示"Generate 3D"按钮（调用现有 `/api/extensions/taiwei/run`）。
   - 状态提示从 API 响应（引导 JSON）驱动。
3. `apps/web/index.html`：TaiWei 扩展面板文案补充支持范围说明（一句话）。
4. **不改**：`packages/execution/.../taiwei_adapter.py`、taiwei 工具链、ORFS 2D 流程。

## 5. 验收标准 + 测试命令

1. 单测：`tests/test_web_app.py tests/test_web_regressions.py tests/test_p8_real_api.py` 相关通过（新增/调整的测试由你补充，覆盖：非 gcd 设计返回引导而非 500；无 baseline 返回引导；有 baseline+gcd 正常提交路径不变）。
2. 全量回归：`PYTHONPATH=... python3 -m pytest -q --no-header 2>&1 | tail -3` → 205 passed / 2 failed（既有环境性失败）。
3. 行为验证：写一个测试或脚本调用 `submit_taiwei_design_run`，用非 gcd design（如 mux4 注册设计）→ 断言返回引导结构（或 400 友好 message，取决于你的实现），**不抛 500**。
4. `git diff --stat` 仅涉及：apps/api/app.py、apps/web/index.html、apps/web/assets/app.js、tests/（如需新增测试）。

## 6. 权限约束

- 提交 no / 推送 no / 联网 no / 部署 no（不重启服务）
- 禁止改 adapter/taiwei 工具链/ORFS

## 7. 相关项目记忆

- 当前服务在跑：web 8000 + worker（audit 后恢复）。
- TaiWei 只验证过 gcd：`taiwei_adapter.py:39` 硬校验 `task["inputs"] == {"flow": "ord", "tech": "asap7_3D", "case": "gcd"}`。
- 用户已有一个 succeeded 的 2D mux4 finish run（`e56b71e0...`，属用户 yuanwenjie）。gcd 2D baseline 是否存在需运行时查询 —— 不要假设存在。

## 8. 事实 vs 假设

- [事实] 非 gcd 设计目前直接 ValueError（HTTP 400/500 路径）。
- [事实] baseline 校验逻辑已存在。
- [假设] 引导式 JSON 或 400 友好 message 都能显著改善体验；最终取舍以"与现有前端错误处理一致性"为准。
- [假设] 前端 TaiWei 详情区已有渲染逻辑，改动是增量。

## 9. 要求 Codex 自己检查仓库

> 请阅读 apps/api/app.py submit_taiwei_design_run、apps/web/assets/app.js 中 taiwei 扩展详情渲染、index.html 扩展面板，确认最小改动。不要盲从建议；若你判断"保持 400 + 友好 message"与前端现状最一致，就选它并说明理由。

## 10. 要求 Codex 自我验证

> 完成后运行验收命令并粘贴真实输出（单测、回归、行为验证脚本输出）。TaiWei 真实 3D 全流程执行**不在**本任务验收范围（工具链耗时且依赖 baseline），不要伪造执行结果。
