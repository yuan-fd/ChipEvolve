# P16：开放知识、BYOK 与人控自演化 v1

status: planned
approval: awaiting_explicit_execution_start
planned_at: 2026-08-06
base_commit: 55c0bde67ecbd9332a7f44d0d5c11ac2a119e1c8

## 1. 阶段目标

P16 不再通过昂贵的 DPLEvolve full-flow 或大规模 ORFS 扫描证明平台能力，而是完成开放平台前必须具备的五项基础设施：

1. 从公开论文、官方文档和开源项目引入可追溯知识与 benchmark 定义；
2. 让平台在后续每次真实运行后安全、幂等地积累 observation；
3. 允许用户配置自己的 API key、模型和 OpenAI-compatible 服务；
4. 把 RL/BO/GP 结果变成带证据和置信度的用户建议，并为未来有限自动决策建立硬门；
5. 为 IC Craft 增加真正可执行的 OpenROAD/ORFS 后端，而不是伪装成商业工具。

完成后，平台应能诚实声明“支持可审计的公开知识、用户自带模型、持续数据积累、人控学习建议和 Craft→OpenROAD 执行”。不能声明已经拥有成熟在线 RL、自动跨设计泛化、商业 signoff 或 DPLEvolve QoR 改进。

## 2. 当前事实与决策

- P14 只有 10 条 observed observation，适合验证闭环和建议器，不足以训练或放权成熟在线策略。
- DPLEvolve 上游包含 50 个 prompt 模板、73 个 knowledge 文件、17 个 problem 定义和多份实验计划。轻量 smoke 已校验 33 条知识索引，生成 Teacher/Student/Review prompts，0 个 prompt warning；不再安排 P16 全流程。
- 当前 Spec Provider 只有本机 `codex-cli` Terra/Sol 和离线 rules，没有多用户 API key 存储、OpenAI-compatible provider 或费用隔离。
- 当前 shadow policy 永远 `execution_allowed=false`，尚无用户接受/修改/拒绝建议的持久反馈，也没有可信的自动资格计算。
- ImplCraft v1 只允许 Synopsys/Cadence dry-run script generation。OpenROAD 不能靠替换 Tcl 命令冒充商业后端，应增加平台自有的 backend-neutral plan 与 ORFS adapter。
- Runtime 继续是唯一执行与终态权威；知识、模型、Craft planner 均不得成为第二调度器。

## 3. P16-A：公开知识与 benchmark Registry

### 目标

建立“引用公开知识，但不把外部结论冒充本地实测”的导入层。

### 工作内容

1. 新增版本化对象：
   - `KnowledgeSource`：来源、作者/组织、URL、版本/commit、许可证、获取日期、SHA-256；
   - `DocumentClaim`：原文位置、摘要、适用上下文、证据等级；
   - `BenchmarkDefinition`：设计、来源、许可证、RTL/约束哈希、允许工艺、规模和预期入口；
   - `CorpusSnapshot`：一次可重放语料快照与 parser/chunker/embedding/reranker 版本。
2. 首批只审计不盲目下载候选：
   - OpenROAD/OpenROAD-flow-scripts 官方文档与公开设计；
   - OpenROAD AutoTuner 的公开搜索空间和实验定义；
   - OpenABC-D、CircuitNet、ChiPBench、PDAGENT-BENCH；
   - ISPD/ICCAD 可合法获取的 contest benchmark；
   - PTPT、Customized RAG for EDA、DRiLLS、GP surrogate 等论文的公开元数据、作者版本或项目 artifact。
3. 每个来源先做 license、访问方式、文件规模、版本和再分发审计。付费正文、许可不明 PDF 和大数据集不得自动下载或提交。
4. 外部内容只允许进入 `official_documentation`、`paper_derived_claim`、`upstream_benchmark_metadata` 等类型；只有本平台 Runtime 产出可以标记 `observed_fact`。
5. RAG 检索先执行 PDK、工具链、阶段、设计特征、来源版本和许可范围硬过滤，再做 BM25/向量/标签重排。
6. 建立固定 QA/检索集，包含正确引用、版本错配、设计错配、许可证受限和恶意 prompt injection 文档。

### 验收

- 至少登记 10 个公开来源和 6 个可用 benchmark definition；每项有 URL/commit、license 结论和内容哈希。
- 至少 50 个固定检索用例；错 PDK、错工具版本、错设计上下文、失效哈希和受限来源必须 100% 拒绝。
- 外部论文结果不能写入 observed observation；API/Web 明示“外部知识”和“本地实测”。
- Corpus snapshot 可在无网络模式下重放；未经许可的大文件不进入 Git。

## 4. P16-B：持续学习数据管道

### 目标

平台以后每次运行都能积累数据，但不会把失败、篡改数据、不同用户数据或预测值混入训练集。

### 工作内容

1. 增加只读 `LearningCollector`，消费 Runtime 终态和已登记 artifact，不修改 Runtime DB。
2. 数据经历 `quarantined -> verified -> admitted/rejected`，只有 SHA、上下文和 parser 校验通过后才能进入 observed-only 数据库。
3. 以 `run_id + attempt_id + context_fingerprint + parser_version` 幂等去重；重启不得重复导入。
4. 保存失败、取消、超时、自动修复父子关系和成本，不只保存成功样本。
5. 模型更新采用显式 snapshot 和离线 rebuild；新 observation 不得在后台静默改变线上建议模型。
6. 多用户数据默认 tenant/project 私有。用于共享知识或全局模型必须由用户明确 opt-in，并保留撤回、派生模型失效和审计链。
7. 用户可导出自己的运行、学习样本和建议反馈；删除请求采用 tombstone、派生关系追踪和后续模型重建，不伪造“物理删除了仍在备份中的数据”。

### 验收

- 成功、失败、修复、重复事件、篡改 artifact、parser 升级和重启恢复均有测试。
- 同一 evidence 只产生一条 observation；prediction/hypothesis 没有写入 observed 表的入口。
- 两个测试 tenant 之间检索、数据导出和模型 snapshot 无泄漏。
- Collector 停止或失败不影响 Runtime 执行和终态。

## 5. P16-C：用户自带 API key 与模型 Provider

### 推荐方案

P16 v1 默认采用“会话内存密钥”：用户在 Web 输入 key，服务端只在内存保存带 TTL 的 secret handle；重启后失效。非秘密配置可以持久化，包括 provider 类型、base URL、模型名、超时、上下文限制和预算。长期密钥持久化留给外部 secret manager，不写平台 SQLite。

### 工作内容

1. 增加统一 `ModelProvider` 接口与 provider registry：
   - `offline-rules`；
   - `codex-cli-local`；
   - `openai-compatible-byok`。
2. `ProviderProfile` 只保存非秘密字段；`SecretHandle` 只保存随机引用、用户/会话、TTL 和用途，不能序列化出 key。
3. Web key 输入框不可回显；API 响应、异常、日志、event、prompt、artifact、数据库和进程列表均不得出现 key。
4. BYOK 请求由受控 egress client 发出：限制协议、重定向、DNS 重绑定、响应大小、超时、并发和允许 host。需要访问本地模型时由管理员显式加入 endpoint allowlist。
5. 每个用户设置 model、最大调用次数、token/费用软硬预算；平台不提供默认付费凭据，也不共享其他用户 key。
6. 模型仍只返回结构化 proposal；Schema 校验、用户确认、预算和 Runtime 提交门保持不变。
7. 部署到非 localhost 时，BYOK 页面必须位于 HTTPS 后；否则禁用 key 输入。

### 验收

- 使用本地 fake OpenAI-compatible server 验证成功、401、429、超时、无效 JSON、超大响应和取消，不消耗真实付费 API。
- 使用唯一 canary secret 扫描内存外可见面：SQLite、日志、events、artifacts、错误响应、子进程环境、备份均为 0 命中。
- TTL、登出、重启和用户删除后 secret handle 失效；不同用户不能引用彼此 handle。
- 未配置 key 时仍可使用 rules 或本机 Codex，不产生隐式外部请求。

## 6. P16-D：RL/BO/GP 建议与有限自动资格

### 三档权限

| 等级 | 行为 | 默认状态 |
| --- | --- | --- |
| T0 Shadow | 离线回放，只记录如果当时采用会怎样 | 已有并继续保留 |
| T1 Advice | 展示建议、证据、置信度、风险；用户接受/修改/拒绝 | P16 默认启用 |
| T2 Bounded Auto | 用户对某个 Study 预先授权后，单次提交高置信候选到 Runtime | 实现门控，默认关闭 |

### 置信度原则

置信度不能使用 LLM 自报的百分比。它必须由以下可复核部分组成：

- 上下文匹配：PDK、工具链、stage、RTL/设计特征是否在域内；
- 数据覆盖：同上下文样本数和候选附近有效邻居数；
- 模型校准：held-out 误差、预测区间覆盖率和 GP 不确定性；
- 安全约束：参数在白名单内、DRC/失败概率和预算风险；
- 证据质量：observed/validated 证据比例、哈希和新鲜度。

### 工作内容

1. 新增 `PolicyRecommendation`、`ConfidenceBreakdown`、`UserDecision` 和 `AutomationEnvelope`。
2. Web 展示“建议什么、为什么、证据在哪、模型有多不确定、最坏成本”，提供接受、修改、拒绝。
3. 用户反馈作为独立标签进入学习库，不能回写历史 observation。
4. T2 必须同时满足：精确版本上下文、足够样本、held-out 校准通过、非 OOD、全部约束通过、用户对当前 Study 显式 opt-in、剩余预算充足。
5. 每个 envelope 默认最多 1 个自动候选；仍经 ExperimentPlan、Campaign 和 Runtime，支持取消、超时和恢复。
6. 当前 P14 数据量不足时，系统正确结果应为 `not_eligible`，而不是降低门槛制造自动演示。

### 验收

- T1 建议可接受/修改/拒绝并完整留证；修改后重新校验参数。
- LLM 自报高置信、错版本、OOD、数据不足、校准失败、预算不足均不能获得 T2。
- 使用固定、充分的测试数据验证 T2 只提交一个幂等 Runtime fixture；真实 P14 数据预期仍为 `not_eligible`。
- 任何 policy 都不能直接启动 EDA 或写 Runtime 终态。

## 7. P16-E：IC Craft 的 OpenROAD/ORFS 后端

### 设计原则

不修改或伪造 ImplCraft 的商业 Tcl。新增平台自有 `BackendNeutralFlowPlan`，由 Craft/Agent 负责表达意图，再由不同 backend adapter 翻译：

```text
自然语言 / Craft Planner
        -> BackendNeutralFlowPlan
             -> openroad-orfs backend -> TaskSpec -> Runtime
             -> implcraft-scriptgen backend -> Cadence/Synopsys scripts
```

### 工作内容

1. FlowPlan 固定 stage、输入、时钟、工艺、QoR 目标、参数、所需能力和 unsupported capabilities。
2. `openroad-orfs` backend 必须复用现有 `build_orfs_task`、Plugin Registry 和 Runtime，不能自行调用 make/openroad。
3. 支持映射 synthesis、floorplan、placement、CTS、route、finish；明确记录 OpenROAD 不支持的商业 MMMC、专有数据库和 signoff 能力。
4. 保留固定 ImplCraft v1 作为 commercial script-generation backend；上游源码不因 OpenROAD 适配而修改。
5. 同一 FlowPlan 可生成 capability comparison，Web 显示“可执行”“仅脚本”“不支持”，禁止把 OpenROAD DRC/STA 称为 Calibre/PrimeTime signoff。

### 验收

- 同一小设计生成 OpenROAD 和 ImplCraft 两个 backend plan，设计、时钟、stage 和 QoR 意图一致。
- OpenROAD backend 通过 Runtime 完成至少一条小型真实 RTL→GDS；ImplCraft 只生成脚本并明确 `commercial_eda_executed=false`。
- unsupported capability fail closed，不生成貌似成功的占位产物。
- Web/API 可选择 backend 并查看 capability matrix、真实状态和证据。

## 8. P16-F：集成展示与发布门

形成四条低成本演示：

1. 导入一个固定公开文档 snapshot，带引用回答 OpenROAD 问题；
2. 用户用 fake/BYOK Provider 生成结构化 Spec proposal，key 不留痕；
3. RL/BO/GP 给出 T1 建议，用户选择后由 Runtime 执行或拒绝；
4. Craft FlowPlan 选择 OpenROAD backend，完成一条小型真实 GDS。

系统验收还必须覆盖：Runtime 权威、重启幂等、tenant 隔离、凭据扫描、数据库备份恢复、离线模式、外部 endpoint 失败和共享工具链不变。

## 9. 资源预算

- P16 新增真实 ORFS run 上限 8，最大并发 2；计划验收只要求 1 条 Craft→OpenROAD 完整 GDS，其余优先 synth/fixture/复用历史证据。
- DPLEvolve 新增 full-flow run：0；只复用已完成的编译、命令和 prompt smoke。
- 公共资料首批最多登记 20 个来源；网络下载总量上限 5 GiB，单文件 512 MiB。超限或许可不清先只登记元数据。
- 平台不承担真实付费 LLM 调用。自动测试全部使用 fake provider；用户自带 key 的手工验收最多 5 次调用，并遵守用户设置的预算。
- 单次连续执行窗口不超过 4 小时；同一根因最多 3 次尝试，每次必须改变假设或措施。

## 10. 安全边界与暂停条件

必须暂停并提交方案的情况：

- 需要在 SQLite、日志或浏览器持久化明文 API key；
- 需要修改 Runtime 主键、终态或让 Provider/Collector/Policy/Craft 直接执行进程；
- 需要默认共享用户设计、RTL、日志或学习数据；
- 公开来源许可证不允许缓存、再分发或训练使用；
- 需要将 OpenROAD 结果描述为商业 signoff；
- 需要扩大 8 个 ORFS run、5 GiB 下载或 5 次真实 API 调用预算；
- 需要运行 DPLEvolve full-flow、应用候选 patch 或晋级源码。

禁止：抓取付费墙正文、提交大数据集/模型权重、把 benchmark 参考结果写为本地 observed、跨用户复用 key、将置信度等同于 LLM 自评、绕过用户 opt-in 自动执行、push 或部署。

## 11. 实施顺序与完成定义

顺序为 P16-A 契约/Registry → P16-B Collector/tenant → P16-C BYOK → P16-D 建议门控 → P16-E Craft backend → P16-F 集成验收。A/B/C 的契约可并行设计，但在 tenant 与 secret 边界冻结前不得开放 Web。

P16 只有在五条能力均有实现、测试和相应真实/fixture 证据，全量回归不退步，安全扫描通过，文档与长期记忆更新后才能完成。单纯下载论文、生成 UI、让模型输出“高置信”或把 ImplCraft Tcl 改名为 OpenROAD 均不算完成。

## 12. 执行前推荐审批项

1. BYOK v1 使用会话内存密钥，默认 TTL 8 小时；不在平台数据库持久化 key。
2. 用户数据默认私有；加入共享知识或全局模型必须逐项目 opt-in。
3. T1 建议默认启用；T2 默认关闭，只有满足硬门且用户对单个 Study opt-in 才开放一个候选。
4. IC Craft 采用新增 backend-neutral FlowPlan 和 OpenROAD adapter，不修改上游商业 backend 语义。
5. 批准上述 8 个 ORFS run、并发 2、5 GiB 下载、最多 5 次用户自带真实 API 调用的上限。

用户可按推荐项整体批准；若不批准真实 API 调用，P16 仍可用 fake provider 完成安全与功能验收。
