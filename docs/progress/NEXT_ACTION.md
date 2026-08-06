# 下一步

updated_at: 2026-08-06

P16“开放知识、BYOK 与人控自演化 v1”已完成并验收。下一阶段建议定义为 P17“公开服务化与学习规模扩展”。

建议顺序：

1. 引入真正的用户认证、项目 ACL、HTTPS 反向代理和外部 secret manager，替代当前 local-user 演示身份；
2. 对用户后续提供的四篇论文和 artifact 做逐项许可/版本审计，扩展公开语料与 benchmark adapter；
3. 把 LearningCollector 作为独立守护服务消费 Runtime 终态，并完成删除/tombstone/派生模型重建演练；
4. 积累足量跨设计数据，建立 held-out 校准和 OOD 报告；T2 继续默认关闭；
5. 将 T1 接受结果显式转换为 ExperimentPlan/Campaign，并加入费用、并发和配额 UI；
6. 为 Craft 增加更多开源 PDK/设计模板和能力对照，不把 OpenROAD 结果称为商业 signoff。

P17 尚未建立目标；开始前应先让用户提供四篇论文及代码级优化 Agent 源码，并确认公开部署的认证/HTTPS方案。

恢复入口：

```bash
cd ~/openroad-platform
git status --short
sed -n '1,260p' docs/evidence/P16_OPEN_PLATFORM_ACCEPTANCE.md
sed -n '1,320p' docs/P16_OPEN_PLATFORM.md
```
