# 长期质量门禁

本仓库是控制平面，不把算法或 EDA 策略塞进 kernel。所有新能力必须沿
`contracts → core → app/plugin` 的依赖方向接入；应用之间不得互相导入。

## 每次变更的自动检查

- `pytest -q`：单元、协议、负向测试和四个应用的真实进程 smoke。
- `pytest -q guardrails`：G1–G17 架构规则及其负向 fixture。
- `mypy`：平台源码的类型边界；smoke 脚本不作为包模块检查。
- Ruff、Black、`git diff --check`：仅检查本次变更的 Python 文件，避免历史 fixture 的格式噪声掩盖回归。
- 依赖一致性测试：每个可安装包的 import 必须出现在自己的 `pyproject.toml` 中，反之亦然。

GitHub Actions 在每个 push 和 pull request 上执行同一组门禁。本地可直接运行：

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/python -m pytest -q guardrails
./.venv/bin/python -m mypy --exclude '(^|/)smoke\\.py$' contracts/src core gateway/src apps
```

## 设计约束

1. 一个模块只拥有一个变化原因；数据库持久化、执行编排、HTTP 适配和工具适配不能合并成新的“大杂烩”模块。
2. 新增 capability 放在 `plugins/<name>`，由它负责 wrapper、环境和工具日志；Foundation 只处理通用任务协议、生命周期和证据。
3. 边界输入立即校验并抛出命名错误；不保留 legacy/fallback 分支，不吞异常，不通过 `sys.path` 越过包边界。
4. 每个修复同时包含正向 smoke 和可证明失败的 negative test；测试不能通过修改 guardrail 来“放宽”约束。
5. 大模块拆分采用兼容的公共接口迁移：先抽出单一职责模块，再由旧入口短期 re-export，最后在一次独立变更中删除旧实现。

## 已知技术债

`RuntimeStore` 和 `plan_executor.__main__` 仍然偏大。它们当前有完整测试覆盖，暂不做高风险一次性重写；后续按上述迁移顺序拆成 store、executor、HTTP 和 CLI 模块，并为每一步保留 API/协议回归测试。
