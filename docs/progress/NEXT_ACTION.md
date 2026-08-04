# 下一步

updated_at: 2026-08-04

## 当前首项动作

```bash
cd ~/openroad-platform && sed -n '1,260p' tasks/phase-1.md
```

P0 验收已通过。提交 P0 后，将 `tasks/current_task.md` 切换为 `tasks/phase-1.md` 的内容，先实现 v1 公共 contracts 及其失败测试。

## 重大问题暂停条件

P1 只有需要改变 Accepted ADR、核心 ID/Attempt 不可变语义、安全边界或项目外共享工具链时暂停请求用户决策。
