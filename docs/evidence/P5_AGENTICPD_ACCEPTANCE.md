# P5 AgenticPD 黑箱优化接入验收

status: completed
captured_at: 2026-08-04

## 结论

`agenticpd@1.0.0` 已作为“提案生成器”接入，未成为第二调度器。固定上游在官方 mock 模式下生成完整 Trial，Adapter 将其转换为版本化 `ExperimentPlan`；Runtime 随后以同一 RTL、Nangate45、时钟、布局密度和固定 2D 工具链完成 38% 基线与 35% 候选两次真实 RTL→GDS。

AgenticPD mock 中的面积、功耗和时序是伪造数据，未用于比较。下列 QoR 全部来自各 child run 的 ORFS `analysis/report.json`。

| 项目 | 基线 | 候选 |
| --- | ---: | ---: |
| CORE_UTILIZATION | 38% | 35% |
| setup WNS | 5.6557 ns | 5.6546 ns |
| 面积 | 88.312 µm² | 88.312 µm² |
| 功耗 | 8.10975 µW | 8.09822 µW |
| 实际利用率 | 52.6984% | 50.5327% |
| 线长 | 282 µm | 289 µm |
| DRC | 0 | 0 |

候选只是一个可信执行并可公平比较的样本，不宣称整体优于基线：功耗略降，但 WNS 和线长略差。

## 契约与边界

- `ExperimentPlan`/`ExperimentCandidate` 有 schema version、child-run 上限、证据引用和来源 Trial。
- 上游提案被完整保存；第一版只消费 ORFS 插件可无歧义验证的 `CORE_UTILIZATION`。
- `CORE_ASPECT_RATIO`、PL/CTS/RT 参数明确写入 `unsupported_parameters`，未冒充已生效。
- 无 LICENSE 上游源码没有被复制、修改或 import；Adapter 仅黑箱启动固定 commit。
- 上游源码运行前后 clean；凭据只允许环境注入。

## 真实证据

- RTL SHA-256：`4b4fe1e2f61672c0fb5b440e4361881a07cbc0c51172d1a0fee1acc5c2010e7d`。
- 基线 GDS：134,164 bytes，SHA-256 `b9e9c6a7...`。
- 候选 GDS：135,654 bytes，SHA-256 `a06be0f6...`。
- 两份生成 config 分别包含 `CORE_UTILIZATION = 38` 和 `35`。
- Runtime SQLite 快照、proposal 原始 Trial、两套日志/报告/GDS 在 ignored `runs/p5-acceptance-20260804-02/`。
- `-01` 保留一次脚本选错 report kind 的失败证据；修复后新目录重跑，未覆盖原证据。
- 全量：`python -m pytest -q` → 61 passed。

真实 LLM 未执行：当前没有显式注入的 DeepSeek 凭据和付费预算。这是外部能力阻塞，不影响黑箱 proposal、参数消费和真实 ORFS 比较的 P5 验收。
