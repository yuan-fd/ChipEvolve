# P15 DPLEvolve 轻量可运行性 smoke

status: accepted
date: 2026-08-06

按用户要求，本次不运行 DPLEvolve 完整布局布线，只验证控制仓库、知识、prompt 和已构建命令表面。

- 上游 release-readiness 的 Python compile、Shell syntax、repo hygiene 通过。
- 33 条 knowledge index 全部通过。
- `jpeg_nangate45` Teacher dry-run 成功；没有调用 Codex、没有执行 EDA。
- 生成 `teacher_plan.md`、`student_01.md`、`teacher_review.md`，prompt audit warnings 为 0。
- Tcl source smoke 找到 `detailed_placement_evolve`、`improve_placement_evolve`、`optimize_mirroring_evolve` 和 `check_placement_evolve`。
- 已构建静态库包含 `Dpl_evolve_Init` 与 `dpl_evolve::Opendp::runStudentAlgorithm`。
- smoke 前后共享 ORFS status 和 diff 哈希均未变化。

Prompt audit 位于 `artifacts/p15-command-smoke-20260806/state/p15_command_smoke/teacher_rounds/prompt_audit.json`，SHA-256 为 `b9d581047a3625272422bcb74ae217ce6e6e994ead66a6f622962eb3aca4c472`。

该 smoke 证明固定控制层能生成有效 prompt、检索知识，并且 DPLEvolve Tcl/C++ 入口已经构建。它不证明候选算法 QoR、placement legality 或 full-flow 正确性；用户已明确 P16 不为此消耗完整流程预算。
