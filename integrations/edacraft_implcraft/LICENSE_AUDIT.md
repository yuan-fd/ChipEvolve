# EDACraft / ImplCraft 许可证与能力审计

固定来源为 `ephonic/EDACraft@739eee0f3ced8fc3cbb6f01b6cc89414758fd898`。

根 `LICENSE` 虽以 “MIT License” 开头，但附加了明确的 Non-Commercial
限制，因此不是标准 MIT，GitHub API 也返回 `NOASSERTION`。平台将其标记为
`LicenseRef-EDACraft-NonCommercial`：当前只允许私有、非商业、本机研究验收；
源码继续保存在 ignored `.external-src/edacraft`，不复制进 Git 或 release。

ImplCraft 是 DC/ICC2/PT/Calibre/Innovus/Tempus/Pegasus 的编排和脚本生成层。
本机未发现这些商业 EDA binary/license，所以 P11 v1 只声明并验收
`eda.implcraft.scriptgen` 与 `eda.backend.plan`。它真实执行第三方配置解析、
Tcl 生成、DesignState 和 QoR 报告流程，但明确记录
`commercial_eda_executed=false`，不宣称得到商业工具 GDS 或 signoff。

上游固定 commit 在 aarch64/Python 3.12.4 上测试为 220 项中的 215 通过、5 项
失败。失败集中在 ICC2/Innovus 脚本文本与测试期望不一致、默认 dry-run 的
`finish` 子阶段未实现，以及 physical-only SPICE 过滤；平台不修改上游源码，
只把 v1 allowlist 限制到已验证的 `synthesis/create_lib/floorplan/placement`
dry-run 范围。
