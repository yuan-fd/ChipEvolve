# 下一步

updated_at: 2026-08-04

## 当前首项动作

```bash
cd ~/openroad-platform && python3 -m pytest -q tests/test_platform_contracts.py
```

先添加 v1 公共 contracts 的失败测试，再实现严格 schema/version/identifier/timeout/retry 校验。

## 重大问题暂停条件

P1 只有需要改变 Accepted ADR、核心 ID/Attempt 不可变语义、安全边界或项目外共享工具链时暂停请求用户决策。
