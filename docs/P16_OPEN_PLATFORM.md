# P16 开放知识、BYOK、人控建议与 Craft 后端

P16 新增四条平台能力，但没有改变 Runtime 的执行权威。

## 公开知识与 benchmark

固定清单位于 `knowledge/public-corpus.lock.json`。每个来源登记 URL、版本、许可判断、获取日期、哈希及缓存策略；许可未完成文件级审计的项目只登记 metadata，不下载或提交数据集。外部论文与 benchmark 永远标记为 `external_public`，不能写入本平台 `observed` 数据。

离线导入：

```bash
python scripts/import_public_knowledge.py \
  --database /tmp/public-eda.db \
  --query "OpenROAD RTL GDS flow"
```

检索在文本打分前执行 platform、toolchain、stage、design class、许可和 prompt-injection review 硬过滤。当前快照登记 10 个来源、7 个 benchmark definition；大数据集和论文全文未进入 Git。

## 持续学习

`LearningCollector` 只读取终态 Runtime evidence，执行 `quarantined → verified → admitted/rejected`。幂等键包含 tenant、project、run、attempt、context 和 parser version。默认按 tenant/project 私有；进入共享训练数据必须逐条 opt-in。Collector 失败不会修改 Runtime 的运行或终态。

API：

- `POST /api/runtime/runs/:id/collect-learning`
- `GET /api/learning/observations?tenant_id=...&project_id=...`

## BYOK Provider

支持 `deterministic`、`codex-cli` 与 `openai-compatible-byok`。Provider profile 只持久化 URL、模型、超时、响应上限和调用预算；API key 由内存 broker 保存，默认 TTL 8 小时，绑定 owner/session，重启、撤销或过期后失效。

安全限制包括 HTTPS（loopback 开发服务除外）、管理员 host allowlist、DNS 地址检查、私网/保留地址拒绝、禁止重定向、超时、响应大小限制、调用预算和密钥回显拒绝。默认 host allowlist 是 `api.openai.com` 与 loopback；其它服务由管理员设置 `OPENROAD_PLATFORM_PROVIDER_ALLOW_HOSTS`。

主要 API：

- `POST /api/providers`：保存非秘密 profile，并返回内存 secret handle；
- `GET /api/providers?owner_id=...`：只列非秘密配置；
- `POST /api/providers/secrets/revoke`：主动撤销；
- `POST /api/spec/sessions`：传入 `provider=openai-compatible-byok` 及 owner/session/profile/handle。

非 localhost 部署若 `OPENROAD_PLATFORM_EXTERNAL_URL` 不是 HTTPS，API key 输入会被禁用。

## 人控优化建议

BO/GP/RL 输出先转为 T1 `PolicyRecommendation`。页面展示参数、证据、数据覆盖、held-out 校准、OOD、安全约束、最坏成本和自动化资格，用户可以接受、修改或拒绝。修改参数会重新执行 Study bounds 校验，决策单独留证，不能改写历史 observation。

T2 `AutomationEnvelope` 默认不执行，只计算资格。它要求精确上下文、至少 20 条同上下文样本、至少 5 条候选邻域样本、held-out 校准通过、非 OOD、安全约束通过、Study opt-in 和预算充足；最多一个候选。当前 P14 的 10 条真实 observation 正确结果是 `not_eligible`。

## IC Craft OpenROAD/ORFS 后端

`BackendNeutralFlowPlan` 保存设计、RTL 哈希、时钟、平台、阶段、参数、QoR 意图及所需能力。两个 adapter 为：

- `openroad-orfs`：生成现有 `orfs` TaskSpec，经 Plugin Registry 和 Workflow Runtime 执行；
- `implcraft-scriptgen`：保留商业脚本生成语义，`commercial_eda_executed=false`。

商业 MMMC、PrimeTime/Calibre signoff 和专有数据库能力不会被 OpenROAD 冒充；请求这些能力会 fail closed。

真实验收 run `6111c2de98a94b28b7717d2f13cd96a3` 使用官方 ORFS gcd，经 Runtime 完成 synth、floorplan、place、CTS、route、finish，登记 GDS/DEF/ODB/网表、报告和 KLayout 2D 图像。详见 `docs/evidence/P16_OPEN_PLATFORM_ACCEPTANCE.md`。

