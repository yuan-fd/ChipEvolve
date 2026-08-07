"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = {
  platform: null, designs: [], examples: [], runs: [], results: [],
  selectedDesign: null, selectedRun: null, designView: "schematic",
  resultFilter: "all", extensions: [], selectedExtension: null,
  pendingExtension: null, rtlscoutStatus: null, providerProfile: null,
  rtlscoutPoll: null, locale: "en",
};
const stages = ["synth", "floorplan", "place", "cts", "route", "finish"];
const ZH = {
  "nav.overview": "平台概览", "nav.frontend": "前端设计", "nav.backend": "后端实现",
  "nav.extensions": "功能扩展", "nav.projects": "项目与结果", "nav.evolution": "自演化",
  "switch.frontend": "前端设计", "switch.backend": "后端实现", "switch.results": "运行结果",
  "frontend.kicker": "交互式设计工作区", "frontend.title": "前端设计",
  "frontend.subtitle": "按清晰顺序创建或导入 RTL、完成综合并查看电路结果。",
  "frontend.source.title": "创建或上传 RTL", "frontend.source.subtitle": "选择一种输入方式，最终都会生成统一的设计记录。",
  "frontend.upload.title": "上传 Verilog / SystemVerilog", "frontend.upload.help": "加载本地 .v 或 .sv 文件，确认内容后进行综合。",
  "frontend.upload.file": "RTL 源文件", "frontend.upload.review": "查看或粘贴源码", "common.filename": "文件名",
  "frontend.rtl.source": "RTL 源码", "frontend.upload.action": "导入并综合",
  "frontend.spec.title": "根据自然语言生成 RTL", "frontend.spec.help": "描述功能和接口，在物理实现前先审查生成结果。",
  "frontend.spec.label": "自然语言规格", "frontend.spec.placeholder": "设计一个带使能和高电平复位的四位同步计数器。",
  "frontend.spec.action": "创建规格会话", "frontend.spec.note": "模型生成需要连接用户自己的 Provider。",
  "frontend.examples.title": "选择经过审计的 RTL 示例", "frontend.examples.subtitle": "包含基础逻辑、ALU、控制器、UART 和教学用 RISC-V。",
  "frontend.examples.selected": "当前示例", "frontend.examples.action": "综合该示例",
  "frontend.results.title": "综合结果", "frontend.results.subtitle": "查看门级统计、电路图、Verilog 源码和综合网表。",
  "common.design": "已登记设计", "frontend.view.schematic": "电路图", "frontend.view.rtl": "Verilog 源码",
  "frontend.view.netlist": "门级网表", "frontend.download.rtl": "下载 RTL", "frontend.download.netlist": "下载网表",
  "frontend.empty.title": "综合后的电路将在这里显示。", "frontend.empty.help": "从上方选择设计以读取真实登记结果。",
  "common.optional": "可选", "rtlscout.subtitle": "在正确性硬门槛下优化 benchmark RTL。",
  "backend.kicker": "交互式物理实现工作区", "backend.title": "后端实现",
  "backend.subtitle": "配置 RTL-to-GDS，跟踪每个物理设计阶段并检查版图和 QoR 证据。",
  "backend.design.title": "选择已登记的 RTL 设计", "backend.design.subtitle": "前端产生的设计可以直接进入物理设计流程。",
  "backend.config.title": "配置物理实现", "backend.config.subtitle": "设置时钟、布局约束、目标阶段和优化策略。",
  "backend.clock": "时钟端口", "backend.period": "时钟周期 · ns", "backend.platform": "PDK / 工艺平台",
  "backend.util": "核心利用率 · %", "backend.density": "布局密度", "backend.target": "目标阶段",
  "backend.objective": "优化目标", "backend.objective.balanced": "综合平衡", "backend.objective.timing": "时序优先",
  "backend.objective.area": "面积优先", "backend.objective.power": "功耗优先", "backend.mode": "流程模式",
  "backend.run.title": "运行 RTL-to-GDS 并监控阶段", "backend.run.subtitle": "Runtime 记录进度、恢复状态、指标和产物。",
  "backend.run.select": "Runtime 任务", "backend.run.action": "开始 RTL-to-GDS", "backend.run.compare": "比较历史任务",
  "backend.run.empty": "尚未选择任务", "backend.run.empty.help": "排队、运行和已完成的记录会显示在这里。",
  "backend.evidence.title": "版图、QoR 与实现证据", "backend.evidence.subtitle": "查看版图、时序、面积、功耗、DRC、报告和下载产物。",
  "backend.evidence.empty": "运行证据将在这里显示。", "backend.evidence.empty.help": "选择已完成的 Runtime 任务以读取版图和报告。",
  "backend.extensions.title": "可选流程扩展", "backend.extensions.subtitle": "只打开当前设计真正需要的专业能力。",
  "backend.ext.flow": "批量实验、参数搜索、状态监控和有界纠错。", "backend.ext.3d": "双层实现、HBT、跨层指标与 3D 视图。",
  "backend.ext.craft": "后端中立规划与 OpenROAD 兼容实现。", "backend.ext.evolve": "可选的 OpenROAD 源码候选优化长任务。"
};
const LOOSE_ZH = {
  "Open-source intelligent chip design infrastructure": "开源智能芯片设计基础设施",
  "OpenROAD Self-Evolving EDA Platform": "OpenROAD 自演化 EDA 平台",
  "From design intent to verified silicon evidence.": "从设计意图到可验证的芯片证据。",
  "Watch Demo": "观看演示", "Tutorial": "使用教程", "Quick Start": "快速开始",
  "Platform walkthrough": "平台展示", "See the complete design journey.": "查看完整芯片设计过程。",
  "A connected workflow": "统一工作流", "One path from intent to learning.": "从设计意图到持续学习的一条主线。",
  "Recommended tutorial": "推荐教程", "Your first reproducible design.": "完成第一个可复现设计。",
  "Choose a frontend input": "选择前端输入", "Review the circuit": "检查电路", "Configure the physical flow": "配置物理设计流程", "Inspect and reuse the evidence": "检查并复用证据",
  "Extensions": "功能扩展", "Specialist tools,": "专业工具，", "connected by evidence.": "由统一证据连接。",
  "Extension catalog": "扩展目录", "Choose a capability to inspect.": "选择需要查看的能力。", "Select an extension.": "请选择一个扩展。",
  "Projects & results": "项目与结果", "A clear record": "清晰记录", "for every design.": "每一个设计。",
  "All records": "全部记录", "Designs": "设计", "Runtime runs": "Runtime 任务", "Refresh": "刷新",
  "Self-evolution": "自演化", "Learning from": "从可验证的", "verified design experience.": "设计经验中学习。",
  "Learning workflow": "学习流程", "How experience becomes a better next run.": "如何把经验转化为更好的下一次运行。",
  "Knowledge": "知识", "Research & RAG": "论文与 RAG", "Experience": "经验", "Runtime observations": "Runtime 观测",
  "Models": "模型", "Decision": "决策", "Human review": "人工审查", "Action": "执行", "Campaign & Runtime": "Campaign 与 Runtime",
  "Current learning record": "当前学习记录", "Recommendations remain inspectable and controllable.": "所有建议都保持可检查、可控制。",
  "How RTLScout works": "RTLScout 如何工作", "Agent loop": "智能体循环",
  "The agent may edit RTL, but it cannot declare success. Verilator and Yosys produce the recorded result.": "智能体可以修改 RTL，但不能自行宣布成功；最终结果由 Verilator 和 Yosys 验证。",
  "Input": "输入", "Specification + objective": "规格与优化目标", "Benchmark context, cost metric, tool rules": "Benchmark 上下文、成本指标和工具规则",
  "Agent": "智能体", "LLM proposes an edit": "LLM 提出修改", "Create, inspect, replace, or apply a diff": "创建、检查、替换或应用差异",
  "Verified evaluation": "可验证评估", "Compile": "编译", "Python DSL → Verilog when needed": "必要时将 Python DSL 转为 Verilog",
  "Lint and simulation": "Lint 与仿真", "Cost and structure": "成本与结构", "Selection": "选择",
  "Keep the best legal RTL": "保留最优合法 RTL", "Correct first, then lower cost": "先保证正确，再降低成本",
  "Evaluation evidence returns to the agent": "评估证据返回智能体", "Repeat until done or the step budget is reached": "重复执行，直到完成或达到步数预算",
  "1. Configure the experiment": "1. 配置实验", "Choose what RTLScout should optimize and how much work it may perform.": "选择优化对象、成本指标和最大工作量。",
  "Run mode": "运行模式", "Benchmark": "Benchmark", "Cost objective": "成本目标", "Maximum agent steps": "智能体最大步数",
  "2. Connect a model provider": "2. 连接模型 Provider", "This only validates and stores a session profile. It does not start RTLScout.": "这里只验证并保存会话配置，不会启动 RTLScout。",
  "Connect Provider": "连接 Provider", "3. Start optimization": "3. 开始优化", "RTLScout run dashboard": "RTLScout 运行仪表盘",
  "Current step": "当前步骤", "Evaluated candidates": "已评估候选", "Legal candidates": "合法候选", "Best cost": "最优成本",
  "Improvement": "优化幅度", "Runtime": "运行时间", "Candidate history": "候选历史", "Best candidate and changes": "最优候选与改动",
  "EDACraft frontend alternatives": "EDACraft 前端替代工具", "Open RTLCraft →": "打开 RTLCraft →", "Open EDACode →": "打开 EDACode →",
  "Baseline flow": "基线流程", "Stage-aware Campaign": "阶段感知批量实验", "Agent-guided search": "Agent 引导搜索",
  "Finish · GDS": "完成 · GDS", "Routing": "布线", "Clock tree": "时钟树", "Placement": "布局", "Floorplan": "布局规划", "Synthesis": "逻辑综合",
  "Execution ready": "执行就绪", "Console ready": "控制台就绪", "API unavailable": "API 不可用", "Connecting": "连接中",
  "Platform API status": "平台 API 状态"
};
const originalText = new WeakMap();
const originalPlaceholders = new WeakMap();

function ui(english, chinese) { return state.locale === "zh" ? chinese : english; }

function applyLocale(locale) {
  state.locale = locale === "zh" ? "zh" : "en";
  document.documentElement.lang = state.locale === "zh" ? "zh-CN" : "en";
  try { localStorage.setItem("openroad-platform-locale", state.locale); } catch (_) { /* storage may be disabled */ }
  $$('[data-locale]').forEach(button => button.classList.toggle("active", button.dataset.locale === state.locale));
  $$('[data-i18n]').forEach(element => {
    if (!originalText.has(element)) originalText.set(element, element.textContent);
    element.textContent = state.locale === "zh" ? (ZH[element.dataset.i18n] || originalText.get(element)) : originalText.get(element);
  });
  $$('[data-i18n-placeholder]').forEach(element => {
    if (!originalPlaceholders.has(element)) originalPlaceholders.set(element, element.placeholder);
    element.placeholder = state.locale === "zh" ? (ZH[element.dataset.i18nPlaceholder] || originalPlaceholders.get(element)) : originalPlaceholders.get(element);
  });
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (!node.parentElement || node.parentElement.closest("[data-i18n], script, style")) continue;
    if (!originalText.has(node)) originalText.set(node, node.nodeValue);
    const original = originalText.get(node);
    const trimmed = original.trim();
    const translated = LOOSE_ZH[trimmed];
    node.nodeValue = state.locale === "zh" && translated ? original.replace(trimmed, translated) : original;
  }
  renderExampleChips();
  renderDesignChips();
  if ($("#rtlscoutMode")) updateRtlscoutControls();
  if (state.selectedRun) selectRun(state.selectedRun.run.run_id);
}

function esc(value) {
  return String(value ?? "").replace(/[&<>'"]/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  }[character]));
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {"Content-Type": "application/json", ...(options.headers || {})}, ...options,
  });
  const type = response.headers.get("content-type") || "";
  const body = type.includes("json") ? await response.json() : await response.text();
  if (!response.ok) throw new Error(body?.error || body || `Request failed (${response.status})`);
  return body;
}

const post = (path, body) => api(path, {method: "POST", body: JSON.stringify(body)});
function message(selector, value, error = false) {
  const element = $(selector);
  if (!element) return;
  element.textContent = value || "";
  element.classList.toggle("error", error);
}

function route(name, options = {}) {
  if (!$(`#page-${name}`)) name = "overview";
  $$(".page").forEach(page => page.classList.toggle("active", page.id === `page-${name}`));
  $$(".tab").forEach(tab => tab.classList.toggle("active", tab.dataset.route === name));
  history.replaceState(null, "", `#${name}`);
  if (!options.preserveScroll) window.scrollTo({top: 0, behavior: "instant"});
  if (name === "projects") loadResults();
  if (name === "evolution") loadEvolution();
  if (name === "extensions") renderExtensions();
}

function selectInputMode(name) {
  $$('[data-input-mode]').forEach(button => button.classList.toggle("active", button.dataset.inputMode === name));
  $$(".input-mode").forEach(panel => panel.classList.toggle("active", panel.id === `input-${name}`));
}

function extensionCard(item) {
  return `<button class="extension-card" data-extension-id="${esc(item.id)}">
    <div class="card-top"><span class="layer">${esc(item.layer)}</span><span class="availability">${esc(item.status_label)}</span></div>
    <h3>${esc(item.name)}</h3><p>${esc(item.summary)}</p><i>View details →</i>
  </button>`;
}

function buildExtensions(platform) {
  const special = [
    {
      id: "flow-agent", name: "Flow-Agent", layer: "optimization", status_label: "Available",
      summary: "Stage-aware campaigns, parameter search, monitoring, diagnosis, and policy-bounded repair.",
      execution_class: "Campaign and Workflow Runtime", input: "Registered RTL, constraints, parameter space, objective and budget",
      safety_note: "Recommendations and repairs remain visible; execution follows campaign policy and explicit limits.",
      workflow: ["Define objective and search space", "Launch stage-aware candidates", "Monitor and diagnose stages", "Compare verified QoR and retain evidence"],
    },
    {
      id: "taiwei-3d", name: "TaiWei 3D IC", layer: "3D physical design", status_label: "Pinned flow",
      summary: "Two-tier gcd implementation with HBT, cross-tier metrics, 3D views, and replay evidence.",
      execution_class: "Pinned isolated 3D toolchain", input: "RTL, 3D platform files, clock and implementation constraints",
      safety_note: "The 3D toolchain is isolated from the default 2D OpenROAD/ORFS environment.",
      workflow: ["2D bootstrap", "Tier partition and 3D floorplan", "Upper/bottom placement and 3D CTS", "Routing, metrics, views, and final evidence"],
    },
    {
      id: "dplevolve", name: "DPLEvolve / Tool-Evolve", layer: "source optimization", status_label: "On demand",
      summary: "OpenROAD source-code candidate generation, validation, QoR evaluation, and best-candidate tracking.",
      execution_class: "User-configured long-running task", input: "Source request, model provider/API key, validation target and compute budget",
      safety_note: "Optional candidate generator. It never runs automatically and remains outside the primary RTL-to-GDS path.",
      workflow: ["Audit request and source baseline", "Generate reviewable candidates", "Compile and validate each candidate", "Measure QoR and retain the best verified result"],
    },
  ];
  const craft = (platform.extensions?.components || []).map(component => {
    const slug = component.plugin_id.replace("edacraft-", "");
    return {
      ...component, slug, id: component.plugin_id,
      source_commit: platform.extensions.source_commit,
      status_label: slug === "implcraft" ? "Dry-run" : "Smoke ready",
      input: slug === "implcraft" ? "Registered RTL and implementation configuration" : `Bounded ${component.smoke_mode} fixture or component-specific user input`,
      workflow: ["Validate extension input", "Submit an isolated Runtime task", "Execute the bounded component adapter", "Register artifacts, metrics, versions, and status"],
    };
  });
  return [...special, ...craft];
}

async function loadPlatform() {
  try {
    const [platform, health] = await Promise.all([api("/api/platform"), api("/api/health")]);
    state.platform = platform;
    state.extensions = buildExtensions(platform);
    $("#healthDot").className = health.ok ? "ok" : "bad";
    $("#healthText").textContent = health.execution_ready ? ui("Execution ready", "执行就绪") : ui("Console ready", "控制台就绪");
    renderExtensions();
    if (state.pendingExtension) {
      const pending = state.pendingExtension;
      state.pendingExtension = null;
      selectExtension(pending);
    }
  } catch (error) {
    $("#healthDot").className = "bad";
    $("#healthText").textContent = ui("API unavailable", "API 不可用");
    $("#extensionCatalog").innerHTML = `<div class="empty-row">${esc(error.message)}</div>`;
  }
}

async function loadRtlscoutStatus() {
  try {
    const status = await api("/api/extensions/rtlscout");
    state.rtlscoutStatus = status;
    const benchmarks = status.offline_demo?.benchmarks || [status.offline_demo?.benchmark || "simple_adder"];
    $("#rtlscoutBenchmark").innerHTML = benchmarks.map(name => `<option value="${esc(name)}">${esc(name)}</option>`).join("");
    $("#runRtlscout").disabled = !status.ready;
    if (!status.byok?.input_enabled) {
      $("#providerState").textContent = ui("HTTPS worker required", "需要 HTTPS Worker");
      $("#providerHint").textContent = ui("The offline demo needs no API key. Custom-provider profiles are accepted only through HTTPS; keys remain memory-only and never enter the project database.", "离线演示不需要 API Key。自定义 Provider 仅通过 HTTPS 接收，密钥只保存在内存中，不写入项目数据库。");
    }
    if (!status.ready) message("#rtlscoutMessage", `${ui("RTLScout is unavailable", "RTLScout 当前不可用")}: ${status.reason}`, true);
    updateRtlscoutControls();
  } catch (error) {
    $("#runRtlscout").disabled = true;
    message("#rtlscoutMessage", `${ui("RTLScout status unavailable", "无法读取 RTLScout 状态")}: ${error.message}`, true);
  }
}

async function loadExamples() {
  try {
    state.examples = (await api("/api/designs/examples")).examples || [];
    $("#exampleSelect").innerHTML = state.examples.map(example => `<option value="${esc(example.id)}">${esc(example.level === "advanced" ? "Advanced" : "Starter")} · ${esc(example.name)}</option>`).join("");
    updateExampleDescription();
    renderExampleChips();
  } catch (error) {
    $("#exampleSelect").innerHTML = '<option value="">Examples unavailable</option>';
    message("#specMessage", error.message, true);
  }
}

function renderExampleChips() {
  const root = $("#exampleChips");
  if (!root) return;
  const selected = $("#exampleSelect")?.value;
  root.innerHTML = state.examples.map(example => `<button type="button" class="${example.id === selected ? "active" : ""}" data-example-id="${esc(example.id)}">▶ ${esc(example.name)} · ${esc(example.level === "advanced" ? ui("advanced", "进阶") : ui("starter", "基础"))}</button>`).join("");
  $$('[data-example-id]', root).forEach(button => button.addEventListener("click", () => {
    $("#exampleSelect").value = button.dataset.exampleId;
    updateExampleDescription();
    renderExampleChips();
  }));
}

function updateExampleDescription() {
  const example = state.examples.find(item => item.id === $("#exampleSelect").value);
  $("#exampleDescription").textContent = example?.description || "";
  renderExampleChips();
}

async function useExample() {
  const example = state.examples.find(item => item.id === $("#exampleSelect").value);
  if (!example) return message("#specMessage", "Choose an example first.", true);
  const button = $("#useExample");
  button.disabled = true;
  message("#specMessage", `Synthesizing ${example.name}…`);
  try {
    const design = await post("/api/designs/import", {filename: example.filename, rtl_source: example.rtl_source, description: example.description});
    message("#specMessage", `${example.name} is registered and ready for review.`);
    await loadDesigns(design.id);
    $("#designMeta").scrollIntoView({behavior: "smooth", block: "center"});
  } catch (error) {
    message("#specMessage", error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function loadDesigns(preferred = null) {
  try {
    state.designs = (await api("/api/designs")).designs || [];
    const options = `<option value="">${ui("Select a registered design", "选择已登记设计")}</option>` + state.designs.map(design => `<option value="${esc(design.id)}">${esc(design.module)} · ${esc(design.id.slice(-8))}</option>`).join("");
    $("#frontendDesign").innerHTML = options;
    $("#backendDesign").innerHTML = options;
    renderDesignChips();
    const id = preferred || state.selectedDesign?.id || state.designs[0]?.id;
    if (id) {
      $("#frontendDesign").value = id;
      $("#backendDesign").value = id;
      await selectDesign(id);
    }
  } catch (error) {
    message("#specMessage", error.message, true);
  }
}

function renderDesignChips() {
  const root = $("#backendDesignChips");
  if (!root) return;
  const selected = $("#backendDesign")?.value || state.selectedDesign?.id;
  root.innerHTML = state.designs.length ? state.designs.map(design => `<button type="button" class="${design.id === selected ? "active" : ""}" data-backend-design="${esc(design.id)}">▶ ${esc(design.module)} · ${esc(design.id.slice(-6))}</button>`).join("") : `<div class="empty-row">${ui("No frontend designs are registered yet.", "尚未登记前端设计。")}</div>`;
  $$('[data-backend-design]', root).forEach(button => button.addEventListener("click", () => selectDesign(button.dataset.backendDesign)));
}

async function selectDesign(id) {
  if (!id) return;
  const design = await api(`/api/designs/${encodeURIComponent(id)}`);
  state.selectedDesign = design;
  $("#frontendDesign").value = id;
  $("#backendDesign").value = id;
  renderDesignChips();
  const analysis = design.analysis || {};
  const ports = (analysis.inputs || []).length + (analysis.outputs || []).length;
  $("#designMeta").innerHTML = `<div><b>${esc(design.module)}</b><span>${esc(design.description)} · ${esc(design.origin)}</span></div><div class="metric"><strong>${esc(analysis.instance_count ?? "—")}</strong><small>${ui("Gate instances", "门级实例")}</small></div><div class="metric"><strong>${esc(ports)}</strong><small>${ui("Ports", "端口")}</small></div>`;
  $("#downloadRtl").href = `/api/designs/${encodeURIComponent(id)}/source?kind=rtl`;
  $("#downloadNetlist").href = `/api/designs/${encodeURIComponent(id)}/source?kind=netlist`;
  await renderDesignView();
}

async function renderDesignView() {
  const design = state.selectedDesign;
  if (!design) return;
  const canvas = $("#frontendCanvas");
  $$('[data-design-view]').forEach(button => button.classList.toggle("active", button.dataset.designView === state.designView));
  if (state.designView === "schematic") {
    canvas.innerHTML = `<img src="/api/designs/${encodeURIComponent(design.id)}/schematic.svg" alt="Synthesized circuit schematic">`;
  } else {
    const text = await api(`/api/designs/${encodeURIComponent(design.id)}/source?kind=${state.designView}`);
    const lines = formatCodeForDisplay(text);
    canvas.innerHTML = `<div class="code-viewer" aria-label="${state.designView === "rtl" ? "Verilog source" : "Gate netlist"}"><ol class="code-lines">${lines.map(line => `<li><code>${esc(line) || "&nbsp;"}</code></li>`).join("")}</ol></div>`;
  }
}

function formatCodeForDisplay(source) {
  const normalized = String(source || "").replace(/\r\n?/g, "\n").trimEnd();
  let lines = normalized.split("\n");
  if (lines.length <= 3 || lines.some(line => line.length > 180)) {
    lines = normalized
      .replace(/;\s*(?=(?:assign|always|wire|reg|logic|input|output|inout|module|endmodule|[A-Za-z_$]))/g, ";\n")
      .replace(/\s+(endmodule|endcase|endgenerate|endfunction|endtask)\b/g, "\n$1")
      .replace(/\s+(begin|case\s*\([^)]*\)|generate)\s*/g, " $1\n")
      .replace(/\s+end\s+(?=(?:else\b|endmodule\b|endcase\b|$))/g, "\nend ")
      .split("\n");
  }
  let indent = 0;
  return lines.flatMap(raw => raw.split(/(?<=;)\s+(?=\S)/)).map(raw => {
    const line = raw.trim();
    if (/^(end\b|endcase\b|endmodule\b|endgenerate\b|endfunction\b|endtask\b)/.test(line)) indent = Math.max(0, indent - 1);
    const rendered = `${"  ".repeat(indent)}${line}`;
    if (/\bbegin\s*$/.test(line) || /^case\b/.test(line) || /^generate\b/.test(line)) indent += 1;
    return rendered;
  });
}

async function importRtl() {
  const button = $("#importRtl");
  button.disabled = true;
  message("#specMessage", "Synthesizing the imported RTL…");
  try {
    const design = await post("/api/designs/import", {filename: $("#rtlFilename").value.trim(), rtl_source: $("#rtlSource").value, description: "Imported from the web workspace"});
    message("#specMessage", `Registered ${design.module}.`);
    await loadDesigns(design.id);
  } catch (error) {
    message("#specMessage", error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function createSpec() {
  const prompt = $("#specPrompt").value.trim();
  if (!prompt) return message("#specMessage", "Enter a circuit specification first.", true);
  const button = $("#createSpec");
  button.disabled = true;
  message("#specMessage", "Creating a reviewable specification session…");
  try {
    const payload = {message: prompt, provider: "deterministic"};
    if (state.selectedDesign) payload.design_id = state.selectedDesign.id;
    const result = await post("/api/spec/sessions", payload);
    message("#specMessage", `Session ${result.session_id.slice(0, 12)} is ${result.status}. Review and confirmation are recorded separately.`);
  } catch (error) {
    message("#specMessage", error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function saveProvider() {
  const key = $("#providerKey").value;
  if (!key) return message("#rtlscoutMessage", "Enter an API key before connecting the provider.", true);
  try {
    const result = await post("/api/providers", {owner_id: "local-user", session_id: `web-${Date.now()}`, profile_id: `web-provider-${Date.now()}`, base_url: $("#providerUrl").value, model: $("#providerModel").value, api_key: key});
    $("#providerKey").value = "";
    state.providerProfile = result;
    $("#providerState").textContent = `${ui("Connected", "已连接")} · ${result.model || $("#providerModel").value || ui("custom model", "自定义模型")}`;
    message("#rtlscoutMessage", ui("Provider connected for this server session. Connecting does not start an optimization run.", "Provider 已连接到当前服务会话；连接操作不会启动优化。"));
  } catch (error) {
    $("#providerState").textContent = "Connection failed";
    message("#rtlscoutMessage", error.message, true);
  }
}

function updateRtlscoutControls() {
  const mode = $("#rtlscoutMode").value;
  const benchmark = $("#rtlscoutBenchmark").value || "simple_adder";
  const cost = $("#rtlscoutCost").value;
  const steps = Math.max(1, Math.min(Number($("#rtlscoutSteps").value) || 3, 8));
  const byok = mode === "byok";
  if (!state.providerProfile) {
    $("#providerState").textContent = byok
      ? (state.rtlscoutStatus?.byok?.input_enabled ? ui("Not connected", "尚未连接") : ui("HTTPS worker required", "需要 HTTPS Worker"))
      : ui("Not required for offline demo", "离线演示无需 Provider");
  }
  $("#rtlscoutModeNote").textContent = byok
    ? ui("Custom-provider execution is disabled on this HTTP review site. It requires HTTPS and the Runtime worker secret bridge.", "当前 HTTP 验收站禁用自定义 Provider 执行，需要 HTTPS 与 Runtime Worker 密钥桥接。")
    : ui("The offline demo uses the official deterministic model while real Verilator and Yosys verify and score every generated candidate.", "离线演示使用官方确定性模型，真实 Verilator 与 Yosys 负责验证和评分。") ;
  $("#rtlscoutLaunchSummary").textContent = state.locale === "zh" ? `${byok ? "自定义 Provider" : "离线验证演示"} · ${benchmark} · 最小化 ${cost.replaceAll("_", " ")} · ${steps} 步` : `${byok ? "Custom provider" : "Offline verified demo"} · ${benchmark} · minimize ${cost.replaceAll("_", " ")} · ${steps} steps`;
  $("#runRtlscout").textContent = byok ? ui("Secure Worker Required", "需要安全 Worker") : ui("Run Offline Demo →", "运行离线演示 →");
  $("#runRtlscout").disabled = byok || state.rtlscoutStatus?.ready === false;
}

async function submitRtlscout() {
  const mode = $("#rtlscoutMode").value;
  if (mode === "byok") return message("#rtlscoutMessage", "BYOK execution is intentionally blocked on HTTP. Connect through HTTPS with a configured Runtime worker.", true);
  const button = $("#runRtlscout");
  button.disabled = true;
  message("#rtlscoutMessage", "Submitting the verified RTLScout experiment to Workflow Runtime…");
  try {
    const result = await post("/api/extensions/rtlscout/runs", {
      mode,
      benchmark: $("#rtlscoutBenchmark").value,
      cost_metric: $("#rtlscoutCost").value,
      max_steps: Number($("#rtlscoutSteps").value),
    });
    const runId = result.run?.run?.run_id;
    message("#rtlscoutMessage", `Run ${runId?.slice(0, 12) || "record"} is queued. The dashboard will follow Runtime evidence.`);
    await loadRuns(runId);
    $("#rtlscoutDashboard").scrollIntoView({behavior: "smooth", block: "start"});
  } catch (error) {
    message("#rtlscoutMessage", error.message, true);
  } finally {
    updateRtlscoutControls();
  }
}

async function loadRuns(preferred = null) {
  try {
    state.runs = (await api("/api/runtime/runs")).runs || [];
    const physicalRuns = state.runs.filter(run => ["orfs", "taiwei-pin-3d", "implcraft"].includes(run.plugin_id));
    $("#runSelect").innerHTML = `<option value="">${ui("Choose a Runtime run", "选择 Runtime 任务")}</option>` + physicalRuns.map(run => `<option value="${esc(run.run_id)}">${esc(run.design_id)} · ${esc(run.plugin_id)} · ${esc(run.status)}</option>`).join("");
    const preferredPhysical = physicalRuns.find(run => run.run_id === preferred)?.run_id;
    const selectedPhysical = physicalRuns.find(run => run.run_id === state.selectedRun?.run?.run_id)?.run_id;
    const id = preferredPhysical || selectedPhysical || physicalRuns[0]?.run_id;
    if (id) {
      $("#runSelect").value = id;
      await selectRun(id);
    } else renderStageRail(new Map());
    renderDplevolveDashboard();
    await renderRtlscoutDashboard();
  } catch (error) {
    message("#flowMessage", error.message, true);
  }
}

function setRtlscoutProgress(status) {
  const nodes = $$("#rtlscoutProgress > div");
  const complete = status === "succeeded";
  nodes.forEach(node => {
    node.className = complete ? "done" : "";
    $("small", node).textContent = complete ? "complete" : status === "running" ? "awaiting evidence" : status === "failed" ? "not completed" : "waiting";
  });
}

function displayBoolean(value) {
  if (value === true) return '<span class="pass">Pass</span>';
  if (value === false) return '<span class="fail">Fail</span>';
  return "—";
}

function attemptArtifacts(detail) {
  const attempts = (detail.stages || []).flatMap(stage => stage.attempts || []);
  return attempts.at(-1)?.artifacts || [];
}

async function renderRtlscoutDashboard() {
  const latest = state.runs.find(run => run.plugin_id === "rtlscout");
  if (!latest) {
    setRtlscoutProgress("queued");
    return;
  }
  let detail;
  try {
    detail = await api(`/api/runtime/runs/${encodeURIComponent(latest.run_id)}`);
  } catch (error) {
    message("#rtlscoutMessage", `Cannot load RTLScout run: ${error.message}`, true);
    return;
  }
  const run = detail.run;
  const status = run.status;
  $("#rtlscoutRunLabel").textContent = `${run.task_spec?.inputs?.benchmark || run.task_spec?.design_id || "experiment"} · ${run.run_id}`;
  $("#rtlscoutStatus").textContent = status;
  $("#rtlscoutStatus").className = `status ${status}`;
  const started = run.started_at ? new Date(run.started_at).getTime() : null;
  const ended = run.ended_at ? new Date(run.ended_at).getTime() : Date.now();
  $("#rtlscoutRuntime").textContent = started ? `${Math.max(0, (ended - started) / 1000).toFixed(1)} s` : "Waiting";
  $("#rtlscoutCurrentStep").textContent = status === "queued" ? "Waiting for worker" : status === "running" ? "Agent evaluation" : status === "succeeded" ? "Complete" : "Stopped";
  setRtlscoutProgress(status);

  const artifacts = attemptArtifacts(detail);
  $("#rtlscoutArtifacts").innerHTML = artifacts.map(artifact => `<a href="${esc(artifact.url)}" target="_blank" rel="noopener">${esc(artifact.kind)}</a>`).join("");
  const resultArtifact = artifacts.find(artifact => artifact.kind === "rtlscout_result");
  if (resultArtifact && status === "succeeded") {
    try {
      const result = await api(resultArtifact.url);
      const evaluations = Array.isArray(result.all_evals) ? result.all_evals : [];
      const legal = evaluations.filter(item => item.passed === true);
      const firstCost = evaluations.find(item => item.passed === true && Number.isFinite(Number(item.cost_value)))?.cost_value;
      const bestCost = result.best_cost;
      const improvement = Number.isFinite(Number(firstCost)) && Number.isFinite(Number(bestCost)) && Number(firstCost) !== 0
        ? `${(((Number(firstCost) - Number(bestCost)) / Math.abs(Number(firstCost))) * 100).toFixed(1)}%` : "—";
      $("#rtlscoutCurrentStep").textContent = `${result.num_steps ?? evaluations.length} / ${run.task_spec?.parameters?.max_steps ?? "—"}`;
      $("#rtlscoutCandidates").textContent = String(evaluations.length);
      $("#rtlscoutLegal").textContent = String(legal.length);
      $("#rtlscoutBestCost").textContent = bestCost === null || bestCost === undefined ? "—" : `${bestCost} ${result.cost_metric || ""}`;
      $("#rtlscoutImprovement").textContent = improvement;
      if (Number.isFinite(Number(result.duration_s))) $("#rtlscoutRuntime").textContent = `${Number(result.duration_s).toFixed(1)} s`;
      $("#rtlscoutCandidateRows").className = evaluations.length ? "" : "candidate-empty";
      $("#rtlscoutCandidateRows").innerHTML = evaluations.length ? evaluations.map((item, index) => {
        const correctness = item.correctness || {};
        const cost = item.cost_value;
        const delta = Number.isFinite(Number(firstCost)) && Number.isFinite(Number(cost)) ? `${Number(cost) - Number(firstCost) > 0 ? "+" : ""}${(Number(cost) - Number(firstCost)).toFixed(1)}` : "—";
        return `<div class="rtlscout-row"><span>${esc(item.eval_index ?? index + 1)}</span><span>${esc(item.name || item.candidate || `eval-${item.eval_index ?? index + 1}`)}</span><span>${displayBoolean(item.lint_ok ?? correctness.lint_ok)}</span><span>${displayBoolean(item.sim_ok ?? correctness.sim_ok)}</span><span>${displayBoolean(item.passed)}</span><span>${esc(cost ?? "—")}</span><span>${esc(delta)}</span></div>`;
      }).join("") : "Run completed without candidate-level records in the upstream result.";
      $("#rtlscoutBestSummary").textContent = result.passed === true
        ? `Best verified candidate: ${bestCost ?? "recorded without a scalar cost"} ${result.cost_metric || ""}. Download the registered RTL and result evidence at right.`
        : "RTLScout did not register a fully verified candidate.";
    } catch (error) {
      $("#rtlscoutBestSummary").textContent = `The run succeeded, but its result artifact could not be read: ${error.message}`;
    }
  } else {
    $("#rtlscoutCandidates").textContent = "—";
    $("#rtlscoutLegal").textContent = "—";
    $("#rtlscoutBestCost").textContent = "—";
    $("#rtlscoutImprovement").textContent = "—";
    $("#rtlscoutCandidateRows").className = "candidate-empty";
    $("#rtlscoutCandidateRows").textContent = status === "failed" ? "The run stopped before verified candidate evidence was registered." : "Waiting for candidate evidence from the Runtime worker.";
    const attempts = (detail.stages || []).flatMap(stage => stage.attempts || []);
    const failure = attempts.at(-1)?.failure;
    $("#rtlscoutBestSummary").textContent = failure?.message || (status === "queued" ? "The durable task is queued; start a separate Runtime worker to execute it." : "Verified artifacts will appear after the run completes.");
  }
  if (state.rtlscoutPoll) clearTimeout(state.rtlscoutPoll);
  if (["queued", "running", "cancel_requested"].includes(status)) {
    state.rtlscoutPoll = setTimeout(() => loadRuns(latest.run_id), 4000);
  }
}

function renderStageRail(values) {
  const descriptions = {
    synth: ui("Synthesize RTL and produce the gate netlist", "综合 RTL 并生成门级网表"),
    floorplan: ui("Create die/core geometry, rows, tracks, and power plan", "生成芯片与核心区、标准单元行、布线轨道和电源规划"),
    place: ui("Place cells and optimize congestion and timing", "完成单元布局并优化拥塞与时序"),
    cts: ui("Build and optimize the clock distribution tree", "构建并优化时钟分布树"),
    route: ui("Perform global and detailed routing", "完成全局布线和详细布线"),
    finish: ui("Write final reports, DEF, netlist, and GDS", "输出最终报告、DEF、网表和 GDS"),
  };
  const succeeded = stages.filter(name => values.get(name)?.status === "succeeded").length;
  $("#flowProgressBar").style.width = `${(succeeded / stages.length) * 100}%`;
  $("#flowProgressText").textContent = `${succeeded} / ${stages.length}`;
  $("#stageRail").innerHTML = stages.map((name, index) => {
    const value = values.get(name);
    const css = value?.status === "succeeded" ? "done" : value?.status === "failed" ? "failed" : "";
    const status = value?.status || "waiting";
    const statusLabel = {waiting: ui("waiting", "等待"), running: ui("running", "运行中"), succeeded: ui("complete", "完成"), failed: ui("failed", "失败")}[status] || status;
    return `<div class="stage ${css}"><i></i><b>0${index + 1} · ${name}</b><span class="stage-description">${esc(descriptions[name])}</span><small>${esc(statusLabel)}${value?.seconds ? ` · ${Number(value.seconds).toFixed(1)}s` : ""}</small></div>`;
  }).join("");
}

async function selectRun(id) {
  if (!id) return;
  const detail = await api(`/api/runtime/runs/${encodeURIComponent(id)}`);
  state.selectedRun = detail;
  const run = detail.run;
  const task = run.task_spec || {};
  $("#runHeading").innerHTML = `<div><b>${esc(task.design_id)} · ${esc(task.plugin_id)}</b><span>${esc(run.run_id)} · ${esc(task.parameters?.target_stage || ui("extension task", "扩展任务"))}</span></div><span class="status ${esc(run.status)}">${esc(run.status)}</span>`;
  const values = new Map();
  (detail.events || []).forEach(event => {
    const name = event.payload?.tool_stage;
    if (event.event_type === "tool.stage.started" && name) values.set(name, {status: "running"});
    if (event.event_type === "tool.stage.finished" && name) values.set(name, {status: event.payload.status, seconds: event.payload.seconds});
  });
  renderStageRail(values);
  const attempts = (detail.stages || []).flatMap(stage => stage.attempts || []);
  const attempt = attempts.at(-1);
  if (!attempt) {
    $("#backendEvidence").innerHTML = `<div class="empty"><span>⋯</span><h3>${ui("Waiting for a Runtime worker.", "正在等待 Runtime Worker。")}</h3><p>${ui("The queued task and its recovery state are already recorded.", "任务与恢复状态已登记，执行进程由独立 Worker 接管。")}</p></div>`;
    return;
  }
  const artifacts = attempt.artifacts || [];
  const metrics = attempt.metrics || [];
  const views = artifacts.filter(artifact => ["layout_view", "three_d_view"].includes(artifact.kind));
  const visual = views.length ? `<div class="layout-gallery">${views.map(view => `<figure class="layout-figure"><img src="${esc(view.url)}" alt="Registered layout view"><figcaption>${esc(view.store_key)} · SHA-256 ${esc((view.sha256 || "").slice(0, 12))}…</figcaption></figure>`).join("")}</div>` : `<div class="empty"><span>□</span><h3>${ui("No registered layout preview in this attempt.", "该次尝试尚未登记版图预览。")}</h3><p>${ui("Artifacts and reports remain listed below.", "产物与报告仍会在下方列出。")}</p></div>`;
  const metricCards = metrics.length ? `<div class="qor-grid">${metrics.slice(0, 15).map(metric => `<div class="qor-card"><b>${esc(metric.value)}${metric.unit ? ` ${esc(metric.unit)}` : ""}</b><small>${esc(metric.name)}</small></div>`).join("")}</div>` : "";
  $("#backendEvidence").innerHTML = `${visual}${metricCards}<div class="artifact-grid">${artifacts.map(artifact => `<a class="artifact-link" href="${esc(artifact.url)}" target="_blank" rel="noopener"><b>${esc(artifact.kind)}</b><span>${esc(artifact.store_key)} · ${esc((artifact.sha256 || "").slice(0, 10))}…</span></a>`).join("")}</div>${attempt.failure ? `<div class="message error">${esc(attempt.failure.message || attempt.failure.category)}</div>` : ""}`;
}

async function submitFlow() {
  const id = $("#backendDesign").value;
  if (!id) return message("#flowMessage", ui("Select a registered design first.", "请先选择已登记设计。"), true);
  const button = $("#submitFlow");
  button.disabled = true;
  const mode = $("#flowMode").value;
  const objective = $('input[name="flowObjective"]:checked')?.value || "balanced";
  message("#flowMessage", mode === "baseline" ? ui("Submitting a recoverable Runtime task…", "正在提交可恢复的 Runtime 任务……") : ui("Creating a bounded campaign plan for review…", "正在创建有界批量实验计划，等待审查……"));
  try {
    const base = {design_id: id, clock: $("#flowClock").value.trim() || null, clock_period_ns: Number($("#flowPeriod").value), core_utilization_pct: Number($("#flowUtil").value), place_density: Number($("#flowDensity").value), target_stage: $("#flowTarget").value, objective, flow_mode: mode};
    if (mode === "baseline") {
      const detail = await post("/api/runtime/runs/from-design", base);
      message("#flowMessage", ui(`Run ${detail.run.run_id.slice(0, 12)} is queued.`, `任务 ${detail.run.run_id.slice(0, 12)} 已进入队列。`));
      await loadRuns(detail.run.run_id);
    } else {
      const util = Number($("#flowUtil").value);
      const density = Number($("#flowDensity").value);
      const period = Number($("#flowPeriod").value);
      const objectiveMetric = {timing: "finish__timing__setup__ws", area: "finish__design__instance__area", power: "finish__power__total", balanced: "finish__design__instance__area"}[objective];
      const parameterGrid = objective === "timing"
        ? {clock_period_ns: [Math.max(.01, period * .9), period, period * 1.1]}
        : objective === "area" ? {core_utilization_pct: [Math.max(1, util - 5), util, Math.min(99, util + 5)]}
          : objective === "power" ? {place_density: [Math.max(.01, density - .05), density, Math.min(1, density + .05)]}
            : {core_utilization_pct: [Math.max(1, util - 5), util, Math.min(99, util + 5)]};
      const campaign = await post("/api/campaigns/stage-aware", {...base, name: `${mode}-${objective}-${id}`, parameter_grid: parameterGrid, max_parallel: 1, objective_metric: objectiveMetric, direction: objective === "timing" ? "max" : "min", top_k: 2, max_repairs: mode === "agent" ? 2 : 0, max_total_runs: 6});
      message("#flowMessage", ui(`Campaign ${campaign.campaign_id} created with ${campaign.members.length} candidates. It has not been submitted for execution.`, `批量实验 ${campaign.campaign_id} 已创建，共 ${campaign.members.length} 个候选；尚未提交执行。`));
    }
  } catch (error) {
    message("#flowMessage", error.message, true);
  } finally {
    button.disabled = false;
    updateFlowMode();
  }
}

function updateFlowMode() {
  const mode = $("#flowMode").value;
  const baseline = mode === "baseline";
  $("#submitFlow").textContent = baseline ? ui("Start RTL-to-GDS", "开始 RTL-to-GDS") : ui("Create Campaign Plan", "创建批量实验计划");
  $("#flowModeNote").textContent = baseline
    ? ui("Baseline submits one Runtime run using the values above.", "基线模式会按照上方参数提交一个 Runtime 任务。")
    : ui("Campaign modes create three bounded candidates for review; they do not execute automatically.", "批量模式会创建三个有界候选供审查，不会自动执行。")
}

function renderExtensions() {
  if (!$("#extensionCatalog") || !state.extensions.length) return;
  $("#extensionCatalog").innerHTML = state.extensions.map(extensionCard).join("");
  $$('[data-extension-id]').forEach(button => button.addEventListener("click", () => selectExtension(button.dataset.extensionId)));
  if (state.selectedExtension) $$('[data-extension-id]').forEach(button => button.classList.toggle("active", button.dataset.extensionId === state.selectedExtension));
}

function openExtension(id) {
  state.pendingExtension = id;
  route("extensions");
  if (state.extensions.length) {
    state.pendingExtension = null;
    selectExtension(id);
  } else {
    $("#extensionDetail").innerHTML = '<div class="empty"><span>⋯</span><h3>Opening extension…</h3><p>Loading its purpose, supported input, workflow, and available actions.</p></div>';
    $("#extensionDetailSection").scrollIntoView({behavior: "smooth", block: "start"});
  }
}

function selectExtension(id) {
  const extension = state.extensions.find(item => item.id === id);
  if (!extension) return;
  state.selectedExtension = id;
  $("#dplevolveDashboard").classList.toggle("visible", id === "dplevolve");
  renderExtensions();
  const isCraft = id.startsWith("edacraft-");
  const slug = id.replace("edacraft-", "");
  const smokeAllowed = isCraft && slug !== "implcraft";
  $("#extensionDetail").innerHTML = `<div class="extension-detail">
    <div><p class="eyebrow">${esc(extension.layer)} extension</p><h2>${esc(extension.name)}</h2><p class="lead">${esc(extension.summary)}</p>
      <div class="extension-meta"><div><span>Availability</span><b>${esc(extension.status_label)}</b></div><div><span>Execution</span><b>${esc(extension.execution_class)}</b></div><div><span>Required input</span><b>${esc(extension.input)}</b></div>${extension.source_commit ? `<div><span>Source commit</span><b>${esc(extension.source_commit)}</b></div>` : ""}</div>
      <p class="selection-note">${esc(extension.safety_note)}</p>
      <div class="extension-actions">${smokeAllowed ? `<button class="button primary" data-smoke-slug="${esc(slug)}">Run Bounded Smoke <span>→</span></button>` : ""}<button class="button" data-route="projects">View Recorded Results</button></div><p class="message" id="extensionMessage"></p>
    </div>
    <div><p class="eyebrow">Workflow</p><div class="extension-workflow">${(extension.workflow || []).map((step, index) => `<article><span>0${index + 1}</span><b>${esc(step)}</b></article>`).join("")}</div></div>
  </div>`;
  $$('[data-route]', $("#extensionDetail")).forEach(element => element.addEventListener("click", () => route(element.dataset.route)));
  const smoke = $('[data-smoke-slug]', $("#extensionDetail"));
  if (smoke) smoke.addEventListener("click", () => submitExtensionSmoke(smoke.dataset.smokeSlug));
  $("#extensionDetailSection").scrollIntoView({behavior: "smooth", block: "start"});
  if (id === "dplevolve") setTimeout(() => $("#dplevolveDashboard").scrollIntoView({behavior: "smooth", block: "start"}), 420);
}

async function submitExtensionSmoke(slug) {
  const button = $('[data-smoke-slug]', $("#extensionDetail"));
  button.disabled = true;
  message("#extensionMessage", "Submitting the bounded smoke to Runtime…");
  try {
    const result = await post(`/api/extensions/edacraft/${encodeURIComponent(slug)}/smoke`, {});
    message("#extensionMessage", `Run ${result.run.run.run_id.slice(0, 12)} is queued. A Runtime worker owns execution.`);
    await loadRuns();
  } catch (error) {
    message("#extensionMessage", error.message, true);
  } finally {
    button.disabled = false;
  }
}

function renderDplevolveDashboard() {
  const runs = state.runs.filter(run => run.plugin_id === "dplevolve");
  const latest = runs[0];
  $("#dplCandidates").textContent = runs.length || "—";
  if (!latest) return;
  $("#dplStatus").textContent = latest.status;
  $("#dplStatus").className = `status ${latest.status}`;
  $("#dplTitle").textContent = `${latest.design_id} · ${latest.status}`;
  $("#dplSummary").textContent = `Runtime record ${latest.run_id}. Open Projects & Results for artifacts, validation evidence, and the complete event chain.`;
  $("#dplRound").textContent = String(runs.length);
  $("#dplRows").innerHTML = runs.slice(0, 8).map((run, index) => `<div class="candidate-head"><span>Run ${index + 1} · ${esc(run.run_id.slice(0, 10))}</span><span>${esc(run.status)}</span><span>Recorded</span><span>See evidence</span><span>${formatDate(run.created_at)}</span></div>`).join("");
}

async function loadResults() {
  try {
    state.results = (await api("/api/platform/results")).records || [];
    renderResults();
  } catch (error) {
    $("#resultList").innerHTML = `<div class="empty-row">${esc(error.message)}</div>`;
  }
}

function renderResults() {
  const records = state.results.filter(record => state.resultFilter === "all" || record.record_type === state.resultFilter);
  $("#resultList").innerHTML = records.length ? records.map(record => `<button class="result-row" data-result="${esc(record.id)}"><span class="record-kind">${esc(record.project_type)}</span><div><b>${esc(record.name)}</b><span>${esc(record.summary)} · ${esc(record.status)}</span></div><time>${formatDate(record.created_at)}</time><i>→</i></button>`).join("") : '<div class="empty"><span>○</span><h3>No matching records.</h3><p>Results appear after a design or Runtime task is registered.</p></div>';
  $$('[data-result]').forEach(button => button.addEventListener("click", () => selectResult(button.dataset.result)));
}

async function selectResult(id) {
  const record = state.results.find(item => item.id === id);
  if (!record) return;
  try {
    const detail = await api(record.detail_url);
    const run = detail.run || {};
    const task = run.task_spec || {};
    const attempts = (detail.stages || []).flatMap(stage => stage.attempts || []);
    const artifacts = attempts.flatMap(attempt => attempt.artifacts || []);
    const metrics = attempts.flatMap(attempt => attempt.metrics || []);
    const layoutView = artifacts.find(artifact => ["layout_view", "three_d_view"].includes(artifact.kind));
    const visualizationUrl = record.visualization_url || layoutView?.url;
    const visual = visualizationUrl ? `<img src="${esc(visualizationUrl)}" alt="Project visualization">` : '<div class="empty"><span>□</span><h3>Visualization registered with run artifacts.</h3></div>';
    const analysis = detail.analysis || {};
    const designArtifacts = record.record_type === "design" ? `<div class="artifact-grid"><a class="artifact-link" href="/api/designs/${encodeURIComponent(record.id)}/source?kind=rtl" target="_blank"><b>RTL source</b><span>${esc(detail.rtl_file)}</span></a><a class="artifact-link" href="/api/designs/${encodeURIComponent(record.id)}/source?kind=netlist" target="_blank"><b>Gate netlist</b><span>${esc(detail.netlist_file)}</span></a><a class="artifact-link" href="/api/designs/${encodeURIComponent(record.id)}/schematic.svg" target="_blank"><b>Circuit schematic</b><span>${esc(detail.schematic_file)}</span></a></div>` : "";
    const metricTable = metrics.length ? `<div class="metric-record"><h3>Implementation metrics</h3><div class="kv-grid">${metrics.map(metric => `<div class="kv"><span>${esc(metric.name)}</span><b>${esc(metric.value)} ${esc(metric.unit || "")}</b></div>`).join("")}</div></div>` : "";
    $("#resultDetail").innerHTML = `<div class="project-detail-head"><span class="availability">${esc(record.record_type)} · ${esc(record.status)}</span><h2>${esc(record.name)}</h2><p>${esc(record.id)}</p></div><div class="project-overview"><div>${visual}</div><div class="kv-grid"><div class="kv"><span>Project type</span><b>${esc(record.project_type)}</b></div><div class="kv"><span>Module / design</span><b>${esc(detail.module || task.design_id || record.name)}</b></div><div class="kv"><span>Gate instances</span><b>${esc(analysis.instance_count ?? "Available in implementation reports")}</b></div><div class="kv"><span>Runtime plugin</span><b>${esc(task.plugin_id || "Frontend design service")}</b></div><div class="kv"><span>Runtime stages</span><b>${esc((detail.stages || []).length || "Frontend only")}</b></div><div class="kv"><span>Artifacts</span><b>${esc(artifacts.length || (record.record_type === "design" ? "RTL, netlist, schematic" : "Pending"))}</b></div><div class="kv"><span>Replay context</span><b>${record.replayable ? "Registered" : "Pending / not applicable"}</b></div></div></div>${designArtifacts}${artifacts.length ? `<div class="artifact-grid">${artifacts.map(artifact => `<a class="artifact-link" href="${esc(artifact.url)}" target="_blank" rel="noopener"><b>${esc(artifact.kind)}</b><span>${esc(artifact.store_key)} · ${esc((artifact.sha256 || "").slice(0, 10))}…</span></a>`).join("")}</div>` : ""}${metricTable}<details class="raw-record"><summary>Authoritative project record</summary><pre class="detail-code">${esc(JSON.stringify(detail, null, 2))}</pre></details>`;
    $("#projectDetailSection").classList.add("open");
    $("#projectDetailSection").scrollIntoView({behavior: "smooth", block: "start"});
  } catch (error) {
    $("#resultDetail").innerHTML = `<div class="empty-row">${esc(error.message)}</div>`;
  }
}

async function loadEvolution() {
  try {
    const data = await api("/api/platform/evolution");
    const counts = data.counts;
    $("#evoSources").textContent = counts.knowledge_sources;
    $("#evoBenchmarks").textContent = counts.benchmarks;
    $("#evoObservations").textContent = counts.observed_samples;
    $("#evoStudies").textContent = counts.optimization_studies;
    $("#evoRecommendations").textContent = counts.recommendations;
    $("#knowledgeList").innerHTML = [...data.knowledge_sources, ...data.benchmarks].map(item => `<div class="data-item"><div><b>${esc(item.title || item.source_id || item.benchmark_id)}</b><span>${esc(item.organization || item.version || item.entrypoint)}</span></div><small>${esc(item.content_kind || item.license_id)}</small></div>`).join("") || '<div class="empty-row">No audited public sources are registered.</div>';
    $("#studyList").innerHTML = data.studies.map(item => `<div class="data-item"><div><b>${esc(item.design_id)}</b><span>${esc(item.observation_count)} observations · ${esc(item.proposal_count)} proposals</span></div><small>${esc(item.status)}</small></div>`).join("") || '<div class="empty-row">No optimization study has been created yet.</div>';
    $("#researchMethodList").innerHTML = (data.research_methods || []).map(item => `<div class="data-item"><div><b>${esc(item.title)}</b><span>${esc(item.role)} · ${esc((item.implementation || []).join(" / "))}</span></div><small>DOI ${esc(item.doi)}</small></div>`).join("") || '<div class="empty-row">No research methods are registered.</div>';
    $("#recommendationList").innerHTML = data.recommendations.map(item => {
      const recommendation = item.recommendation || item;
      const confidence = recommendation.confidence || {};
      return `<div class="recommendation-item"><div><b>${esc(recommendation.policy_kind || "Optimizer recommendation")}</b><span>${esc(JSON.stringify(recommendation.parameters || {}))}</span><span>Confidence ${Number(confidence.overall || 0).toFixed(2)} · ${confidence.ood ? "Outside observed support" : "Within observed support"} · ${esc((confidence.reasons || []).join("; "))}</span></div><div class="decision-actions"><small>${esc(recommendation.permission_tier || "T1 advice")}</small><button class="button small" data-recommendation-action="accepted" data-recommendation-id="${esc(recommendation.recommendation_id)}">Approve Plan</button><button class="button small" data-recommendation-action="rejected" data-recommendation-id="${esc(recommendation.recommendation_id)}">Reject</button></div></div>`;
    }).join("") || '<div class="empty-row">Recommendations will appear here with confidence, context, and decision controls.</div>';
    $$('[data-recommendation-action]').forEach(button => button.addEventListener("click", () => decideRecommendation(button.dataset.recommendationId, button.dataset.recommendationAction)));
  } catch (error) {
    $("#knowledgeList").innerHTML = `<div class="empty-row">${esc(error.message)}</div>`;
  }
}

async function decideRecommendation(id, action) {
  const buttons = $$(`[data-recommendation-id="${id}"]`);
  buttons.forEach(button => { button.disabled = true; });
  message("#evolutionActionMessage", action === "accepted" ? "Creating a reviewed Campaign…" : "Recording rejection…");
  try {
    const result = await post(`/api/recommendations/${encodeURIComponent(id)}/decision`, {owner_id: "local-user", action, create_campaign: action === "accepted", submit: false});
    await loadEvolution();
    if (result.campaign_created) {
      $("#evolutionActionMessage").innerHTML = `Campaign ${esc(result.campaign_id)} is created and remains idle. <button class="button small" id="submitApprovedCampaign">Confirm Runtime Submission</button>`;
      $("#submitApprovedCampaign").addEventListener("click", () => submitApprovedRecommendation(id));
    } else message("#evolutionActionMessage", "Decision recorded; no execution was started.");
  } catch (error) {
    message("#evolutionActionMessage", error.message, true);
  } finally {
    buttons.forEach(button => { button.disabled = false; });
  }
}

async function submitApprovedRecommendation(id) {
  const button = $("#submitApprovedCampaign");
  if (button) button.disabled = true;
  message("#evolutionActionMessage", "Submitting the approved Campaign to Runtime…");
  try {
    const result = await post(`/api/recommendations/${encodeURIComponent(id)}/decision`, {owner_id: "local-user", action: "accepted", create_campaign: true, submit: true});
    message("#evolutionActionMessage", `Runtime queued ${result.run_ids.length} run. Campaign ${result.campaign_id}.`);
    await loadRuns();
  } catch (error) {
    message("#evolutionActionMessage", error.message, true);
  }
}

function formatDate(value) {
  if (value === null || value === undefined) return "";
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  return Number.isNaN(date.valueOf()) ? "" : date.toLocaleDateString(undefined, {year: "numeric", month: "short", day: "2-digit"});
}

$$('[data-route]').forEach(element => element.addEventListener("click", () => route(element.dataset.route)));
$$('[data-scroll]').forEach(element => element.addEventListener("click", () => $("#" + element.dataset.scroll)?.scrollIntoView({behavior: "smooth"})));
$$('[data-input-mode]').forEach(button => button.addEventListener("click", () => selectInputMode(button.dataset.inputMode)));
$$('[data-design-view]').forEach(button => button.addEventListener("click", () => { state.designView = button.dataset.designView; renderDesignView(); }));
$$('[data-open-extension]').forEach(button => button.addEventListener("click", () => openExtension(button.dataset.openExtension)));
$("#watchDemo").addEventListener("click", () => { $("#demoNotice").textContent = "The demo area is ready; the project video will be connected here when uploaded."; $(".showcase-section").scrollIntoView({behavior: "smooth"}); });
$("#exampleSelect").addEventListener("change", updateExampleDescription);
$("#useExample").addEventListener("click", useExample);
$("#rtlFile").addEventListener("change", event => { const file = event.target.files?.[0]; if (!file) return; $("#rtlFilename").value = file.name; const reader = new FileReader(); reader.onload = () => { $("#rtlSource").value = String(reader.result || ""); }; reader.readAsText(file); });
$("#frontendDesign").addEventListener("change", event => selectDesign(event.target.value));
$("#backendDesign").addEventListener("change", event => selectDesign(event.target.value));
$("#importRtl").addEventListener("click", importRtl);
$("#createSpec").addEventListener("click", createSpec);
$("#saveProvider").addEventListener("click", saveProvider);
$("#rtlscoutMode").addEventListener("change", updateRtlscoutControls);
$("#rtlscoutBenchmark").addEventListener("change", updateRtlscoutControls);
$("#rtlscoutCost").addEventListener("change", updateRtlscoutControls);
$("#rtlscoutSteps").addEventListener("input", updateRtlscoutControls);
$("#runRtlscout").addEventListener("click", submitRtlscout);
$("#runSelect").addEventListener("change", event => selectRun(event.target.value));
$("#submitFlow").addEventListener("click", submitFlow);
$("#flowMode").addEventListener("change", updateFlowMode);
$$('[data-locale]').forEach(button => button.addEventListener("click", () => { applyLocale(button.dataset.locale); updateFlowMode(); if (!state.selectedRun) renderStageRail(new Map()); }));
$("#refreshResults").addEventListener("click", loadResults);
$("#closeResultDetail").addEventListener("click", () => { $("#projectDetailSection").classList.remove("open"); $("#resultList").scrollIntoView({behavior: "smooth"}); });
$$('#resultFilters button').forEach(button => button.addEventListener("click", () => { $$('#resultFilters button').forEach(item => item.classList.remove("active")); button.classList.add("active"); state.resultFilter = button.dataset.filter; renderResults(); }));

let initialLocale = "en";
try { initialLocale = new URLSearchParams(location.search).get("lang") || localStorage.getItem("openroad-platform-locale") || "en"; } catch (_) { /* storage may be disabled */ }
applyLocale(initialLocale);
updateFlowMode();
route(location.hash.slice(1) || "overview");
Promise.all([loadPlatform(), loadRtlscoutStatus(), loadExamples(), loadDesigns(), loadRuns()]);
