"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = {
  platform: null, designs: [], examples: [], runs: [], results: [],
  selectedDesign: null, selectedRun: null, designView: "schematic",
  resultFilter: "all", extensions: [], selectedExtension: null,
  rtlscoutStatus: null, providerProfile: null, selectedRtlscoutRun: null,
  requestedExtension: null, rtlscoutPoll: null, runtimePoll: null, healthPoll: null,
  health: null, auth: null, workspaceLoaded: false, locale: "en",
  specSession: null, pendingCampaign: null, developerView: false,
};
const stages = ["synth", "floorplan", "place", "cts", "route", "finish"];
const ZH = {
  "nav.overview": "平台概览", "nav.frontend": "前端设计", "nav.backend": "后端实现",
  "nav.projects": "项目与结果", "nav.evolution": "自演化",
  "auth.personal": "个人工作区", "auth.title": "登录后开始设计",
  "auth.help": "账户会将你的设计、任务、报告、学习记录和模型配置与其他用户分开。",
  "auth.username": "用户名", "auth.password": "密码", "auth.login": "登录",
  "auth.register": "创建账户", "auth.note": "本研究预览站开放注册。密码经加盐哈希保存，浏览器会话七天后失效。",
  "overview.eyebrow": "开源智能芯片设计基础设施", "overview.tagline": "从设计意图到可验证的芯片证据。",
  "overview.cap1": "由自然语言创建设计，经人工审查 RTL 后完成可复现的物理实现。",
  "overview.cap2.name": "EDA 智能助手", "overview.cap2": "通过自然语言控制流程、解读报告、定位问题并给出修复建议。",
  "overview.cap3.name": "流程优化", "overview.cap3": "支持阶段感知实验、有界搜索、BO/GP、Pareto 分析与 RL 建议。",
  "overview.cap4": "按需生成 OpenROAD 源码优化候选，并通过真实证据进行评估。",
  "overview.cap5.name": "扩展设计栈", "overview.cap5": "在 2D/3D IC 实现之外，补充器件 TCAD、互连电磁与 SPICE 电路仿真。",
  "overview.cap6.name": "持续学习", "overview.cap6": "积累可追溯知识、真实运行数据、优化建议与可复用设计经验。",
  "overview.walkthrough": "平台演示", "overview.walkthrough.title": "查看完整的芯片设计过程。",
  "overview.walkthrough.help": "此处预留项目视频与演示文稿位置，后续可直接接入，不改变页面结构。",
  "overview.media.video": "平台演示视频", "overview.media.video.help": "视频预留位 · 待上传",
  "overview.media.slides": "项目幻灯片", "overview.media.slides.help": "功能 · 工作流 · 架构 · 结果",
  "overview.workflow": "统一工作流", "overview.workflow.title": "从设计意图到验证学习的一条主线。",
  "overview.workflow.help": "每个阶段产生可审查的输出；只有通过验证的证据才会用于改进后续决策。",
  "overview.path.intent": "设计意图", "overview.path.intent.help": "规格、RTL 与约束",
  "overview.path.frontend": "前端设计", "overview.path.frontend.help": "RTL、网表与电路图",
  "overview.path.physical": "物理设计", "overview.path.physical.help": "2D / 3D 实现",
  "overview.path.optimization": "流程优化", "overview.path.optimization.help": "批量实验与有界修复",
  "overview.path.learning": "验证学习", "overview.path.learning.help": "运行证据与决策建议",
  "overview.optional": "可选研究能力", "overview.optional.title": "专业支线在主流程中按需打开。",
  "overview.optional.help": "默认主线始终是 Spec-to-GDS；只有需要 3D 实现、器件物理、互连电磁、电路仿真或源码优化时，才打开对应支线。",
  "overview.optional.physical": "物理设计扩展", "overview.optional.physical.help": "双层 3D 物理实现与可选 OpenROAD 源码优化均从后端流程进入。",
  "overview.optional.backend.action": "打开后端选项 →", "overview.optional.device": "器件与电路研究",
  "overview.optional.device.help": "TCADCraft、MoMCraft 与 CktCraft 分别补充器件物理、S 参数提取和晶体管级仿真，不重复生成 RTL。",
  "overview.optional.device.action": "打开器件工具 →",
  "tutorial.eyebrow": "端到端使用教程", "tutorial.title": "依次走完平台的各条工作流。",
  "tutorial.help": "建议先使用小型设计完成一次验收；所有可选支线都会明确标注，且不会自动启动。",
  "tutorial.1.title": "从规格生成并审查 RTL", "tutorial.1.help": "描述功能与端口，审查服务器模型生成的 RTL，回答待确认问题后批准登记。",
  "tutorial.key.required": "已包含服务器共享模型 · 无需用户 API Key", "tutorial.frontend.action": "打开前端设计 →",
  "tutorial.2.title": "运行可验证的 RTL 自探索", "tutorial.2.help": "RTLScout 提出 RTL 改动，由 Verilator 与 Yosys 独立验证并评价每个候选。",
  "tutorial.key.optional": "可验证离线演示无需 Key · 当前预览站尚未启用完整自定义 Provider 探索",
  "tutorial.3.title": "检查器件或电路研究支线", "tutorial.3.help": "选择主线设计后，平台会继承项目上下文，并明确列出 TCAD、互连电磁或 SPICE 还需要的专业输入。",
  "tutorial.device.requirement": "需要器件结构、互连端口或晶体管级网表；不使用固定示例冒充当前设计结果",
  "tutorial.key.none": "无需 API Key", "tutorial.device.action": "打开器件支线 →",
  "tutorial.4.title": "生成基线 2D 版图", "tutorial.4.help": "选择已登记 RTL，使用基线模式，从逻辑综合运行至最终 GDS。",
  "tutorial.backend.action": "打开后端实现 →", "tutorial.5.title": "创建阶段感知批量实验",
  "tutorial.5.help": "切换到阶段感知批量模式，审查有界参数候选，只提交用户批准的实验。",
  "tutorial.6.title": "创建 Agent 引导的搜索计划", "tutorial.6.help": "使用 Agent 引导模式进行受监控实验和有界纠错；内部编排不会成为额外的用户操作入口。",
  "tutorial.agent.status": "计划审查与用户确认后的执行均已可用", "tutorial.7.title": "查看固定版本的 3D IC 工作流",
  "tutorial.7.help": "对于官方 gcd 验收设计，可打开 TaiWei 支线查看双层布局、跨层指标、3D 视图与重放证据。",
  "tutorial.8.title": "检查结果并收集验证经验", "tutorial.8.help": "对比版图与 QoR，再将成功执行证据明确收集到学习库；公开论文与 benchmark 元数据已按来源登记。",
  "tutorial.learning.status": "学习入库需明确触发，避免失败或未验证结果成为事实", "tutorial.results.action": "打开结果管理 →",
  "api.eyebrow": "模型访问", "api.title": "直接使用共享模型，或连接私有 Provider。",
  "api.help": "平台登录负责隔离 EDA 工作。Spec-to-RTL 默认使用服务器共享模型；私有 Provider 只作为受支持研究扩展的可选配置。",
  "api.login.title": "登录个人工作区", "api.login.help": "创建简单的平台账户。登录后只显示你自己的设计、任务、报告和学习记录。",
  "api.provider.title": "直接使用共享模型", "api.provider.help": "Spec-to-RTL 登录后即可使用服务器模型，不要求用户填写个人 API Key。",
  "api.secret.title": "API Key 仅保留在会话中", "api.secret.help": "Key 只在当前服务会话的内存中使用，不写入项目文件、设计数据库、产物或 Git。",
  "api.run.title": "只启动你明确选择的功能", "api.run.help": "连接 Provider 不会自动启动任务。只有主动运行大模型 Spec-to-RTL 或 RTL 探索时才会调用 API。",
  "api.required": "私有 Provider 可选", "api.required.help": "共享模型已覆盖 Spec-to-RTL。Tool-Evolve 与完整自定义 Provider RTLScout 尚未在当前预览站启用，因此连接私有 Key 不会解锁隐藏的生产流程。",
  "switch.frontend": "前端设计", "switch.backend": "后端实现", "switch.results": "运行结果",
  "frontend.kicker": "交互式设计工作区", "frontend.title": "前端设计",
  "frontend.subtitle": "按清晰顺序创建或导入 RTL、完成综合并查看电路结果。",
  "frontend.source.title": "创建或上传 RTL", "frontend.source.subtitle": "选择一种输入方式，最终都会生成统一的设计记录。",
  "frontend.upload.title": "上传 Verilog / SystemVerilog", "frontend.upload.help": "加载本地 .v 或 .sv 文件，确认内容后进行综合。",
  "frontend.upload.file": "RTL 源文件", "frontend.upload.review": "查看或粘贴源码", "common.filename": "文件名",
  "frontend.rtl.source": "RTL 源码", "frontend.upload.action": "导入并综合",
  "frontend.spec.title": "根据自然语言生成 RTL", "frontend.spec.help": "描述功能和接口，在物理实现前先审查生成结果。",
  "frontend.spec.label": "自然语言规格", "frontend.spec.placeholder": "设计一个带使能和高电平复位的四位同步计数器。",
  "frontend.spec.action": "创建规格会话", "frontend.spec.note": "服务器共享模型可直接生成供审查的 RTL，无需用户填写 API Key；私有 Provider 仍可选。",
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
  "backend.run.title": "运行 RTL-to-GDS 并监控阶段", "backend.run.subtitle": "查看当前设计的进度、恢复状态、指标和生成文件。",
  "backend.run.select": "设计任务", "backend.run.action": "开始 RTL-to-GDS", "backend.run.compare": "比较该设计的历史任务",
  "backend.run.empty": "尚未选择任务", "backend.run.empty.help": "排队、运行和已完成的记录会显示在这里。",
  "backend.evidence.title": "版图、QoR 与实现证据", "backend.evidence.subtitle": "查看版图、时序、面积、功耗、DRC、报告和下载产物。",
  "backend.evidence.empty": "设计结果将在这里显示。", "backend.evidence.empty.help": "选择当前设计已完成的任务以读取版图和报告。",
  "backend.extensions.title": "可选研究支线", "backend.extensions.subtitle": "自动继承当前设计和成功主线；只有扩展输入兼容时才能运行。",
  "backend.extensions.digital": "物理设计支线", "backend.extensions.device": "器件与电路支线",
  "backend.extensions.contract": "当前设计 → 选择 2D 或 3D 分支 → 验证证据",
  "backend.extensions.contract.help": "2D 与 3D 都从已登记 RTL 独立启动；可选的 2D 基线只用于结果对比。其他扩展会在执行前明确额外输入。",
  "backend.ext.flow": "批量实验、参数搜索、状态监控和有界纠错。", "backend.ext.3d": "从已登记 RTL 独立启动双层实现，产出 HBT、跨层指标与 3D 视图。",
  "backend.ext.evolve": "可选的 OpenROAD 源码候选优化长任务。", "backend.ext.tcad": "三维半导体器件结构与有界物理仿真。",
  "backend.ext.mom": "互连电磁分析与 S 参数提取。", "backend.ext.ckt": "SPICE 级模拟与射频电路仿真。"
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
  "Projects & results": "项目与结果", "A clear record": "清晰记录", "for every design.": "每一个设计。",
  "All records": "全部记录", "Designs": "设计", "Design tasks": "设计任务", "Refresh": "刷新",
  "Self-evolution": "自演化", "Learning from": "从可验证的", "verified design experience.": "设计经验中学习。",
  "Learning workflow": "学习流程", "How experience becomes a better next run.": "如何把经验转化为更好的下一次运行。",
  "Knowledge": "知识", "Research & RAG": "论文与 RAG", "Experience": "经验", "Verified observations": "验证观测",
  "Models": "模型", "Decision": "决策", "Human review": "人工审查", "Action": "执行", "Reviewed experiment": "已审查实验",
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
  "Baseline flow": "基线流程", "Stage-aware batch": "阶段感知批量实验", "Agent-guided search": "Agent 引导搜索",
  "Finish · GDS": "完成 · GDS", "Routing": "布线", "Clock tree": "时钟树", "Placement": "布局", "Floorplan": "布局规划", "Synthesis": "逻辑综合",
  "Execution ready": "执行就绪", "Console ready": "控制台就绪", "API unavailable": "API 不可用", "Connecting": "连接中",
  "Platform API status": "平台 API 状态",
  "Clarification": "补充说明", "Update specification": "更新规格", "Approve and register RTL": "批准并登记 RTL",
  "Approve and start selected plan": "批准并启动该计划"
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
  if (state.platform) state.extensions = buildExtensions(state.platform);
  if ($("#rtlscoutMode")) updateRtlscoutControls();
  if (state.selectedRun) selectRun(state.selectedRun.run.run_id);
  if (state.selectedExtension) selectExtension(state.selectedExtension);
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

function renderAuth() {
  const signedIn = state.auth?.authenticated === true;
  const button = $("#accountButton");
  const internal = signedIn && state.auth?.user?.username === "local-user";
  button.textContent = internal ? ui("Internal · shared workspace", "内部模式 · 共享工作区") : (signedIn ? state.auth.user.username : ui("Sign in", "登录"));
  button.classList.toggle("authenticated", signedIn);
  if ($("#developerResultControls")) {
    $("#developerResultControls").hidden = !(signedIn && state.auth.developer);
  }
}

function openAuth(note = "") {
  if (state.auth?.authenticated) return;
  if (state.auth?.user?.username === "local-user") return;  // internal no-auth mode
  $("#authModal").hidden = false;
  message("#authMessage", note);
  setTimeout(() => $("#authUsername").focus(), 0);
}

function closeAuth() { $("#authModal").hidden = true; message("#authMessage", ""); }

async function loadAuth() {
  try { state.auth = await api("/api/auth/session"); }
  catch (_) { state.auth = {authenticated: false}; }
  renderAuth();
  return state.auth;
}

async function submitAuth(mode) {
  const username = $("#authUsername").value.trim();
  const password = $("#authPassword").value;
  if (!username || !password) return message("#authMessage", ui("Enter a username and password.", "请输入用户名和密码。"), true);
  $("#authLogin").disabled = true;
  $("#authRegister").disabled = true;
  message("#authMessage", mode === "register" ? ui("Creating your isolated workspace…", "正在创建隔离工作区……") : ui("Signing in…", "正在登录……"));
  try {
    state.auth = await post(`/api/auth/${mode}`, {username, password});
    $("#authPassword").value = "";
    renderAuth();
    closeAuth();
    await loadAuthenticatedWorkspace();
    route("frontend");
  } catch (error) {
    message("#authMessage", error.message, true);
  } finally {
    $("#authLogin").disabled = false;
    $("#authRegister").disabled = false;
  }
}

async function logout() {
  try { await post("/api/auth/logout", {}); } catch (_) { /* clear local view anyway */ }
  state.auth = {authenticated: false};
  state.workspaceLoaded = false;
  state.designs = []; state.runs = []; state.results = [];
  state.selectedDesign = null; state.selectedRun = null;
  state.specSession = null; state.pendingCampaign = null;
  state.developerView = false;
  if (state.runtimePoll) clearTimeout(state.runtimePoll);
  if (state.rtlscoutPoll) clearTimeout(state.rtlscoutPoll);
  renderAuth(); resetDesignResult(); resetRunResult(); route("overview");
}

async function loadAuthenticatedWorkspace() {
  if (!state.auth?.authenticated) return;
  await Promise.all([loadRtlscoutStatus(), loadExamples()]);
  await loadDesigns();
  await loadRuns();
  state.workspaceLoaded = true;
}

function message(selector, value, error = false) {
  const element = $(selector);
  if (!element) return;
  element.textContent = value || "";
  element.classList.toggle("error", error);
}

function route(name, options = {}) {
  if (!$(`#page-${name}`)) name = "overview";
  if (name !== "overview" && !state.auth?.authenticated) {
    openAuth(ui("Sign in to open your personal workspace.", "请先登录个人工作区。"));
    name = "overview";
  }
  $$(".page").forEach(page => page.classList.toggle("active", page.id === `page-${name}`));
  $$(".tab").forEach(tab => tab.classList.toggle("active", tab.dataset.route === name));
  history.replaceState(null, "", `#${name}`);
  if (!options.preserveScroll) window.scrollTo({top: 0, behavior: "instant"});
  if (name === "projects") loadResults();
  if (name === "evolution") loadEvolution();
}

function selectInputMode(name) {
  $$('[data-input-mode]').forEach(button => button.classList.toggle("active", button.dataset.inputMode === name));
  $$(".input-mode").forEach(panel => panel.classList.toggle("active", panel.id === `input-${name}`));
}

function buildExtensions(platform) {
  const special = [
    {
      id: "taiwei-3d", name: "TaiWei 3D IC", layer: ui("3D physical design", "3D 物理设计"), status_label: ui("Pinned flow", "固定工具链"),
      summary: ui("Two-tier gcd implementation with HBT, cross-tier metrics, 3D views, and replay evidence.", "双层 gcd 实现，包含 HBT、跨层指标、3D 视图与重放证据。"),
      execution_class: ui("Pinned isolated 3D toolchain", "隔离的固定 3D 工具链"), input: ui("RTL, 3D platform files, clock and implementation constraints", "RTL、3D 平台文件、时钟与实现约束"),
      safety_note: "The 3D toolchain is isolated from the default 2D OpenROAD/ORFS environment.",
      workflow: ["2D bootstrap", "Tier partition and 3D floorplan", "Upper/bottom placement and 3D CTS", "Routing, metrics, views, and final evidence"],
    },
    {
      id: "dplevolve", name: "DPLEvolve / Tool-Evolve", layer: ui("source optimization", "源码优化"), status_label: ui("On demand", "按需启用"),
      summary: ui("OpenROAD source-code candidate generation, validation, QoR evaluation, and best-candidate tracking.", "生成 OpenROAD 源码候选，完成验证、QoR 评估与最优候选跟踪。"),
      execution_class: ui("User-configured long-running task", "由用户配置的长时任务"), input: ui("Source request, model provider/API key, validation target and compute budget", "源码优化请求、模型配置、验证目标与计算预算"),
      safety_note: "Optional candidate generator. It never runs automatically and remains outside the primary RTL-to-GDS path.",
      workflow: ["Audit request and source baseline", "Generate reviewable candidates", "Compile and validate each candidate", "Measure QoR and retain the best verified result"],
    },
  ];
  const craft = (platform.extensions?.components || []).map(component => {
    const slug = component.plugin_id.replace("edacraft-", "");
    const inputs = {
      tcadcraft: ui("Device dimensions for structure and invariant validation", "用于结构与一致性验证的器件尺寸"),
      momcraft: ui("Microstrip geometry, effective permittivity, mesh, and frequency", "微带几何、有效介电常数、网格与频率"),
      cktcraft: ui("Bounded component- or transistor-level SPICE .op netlist", "有界的元件级或晶体管级 SPICE .op 网表"),
      rtlcraft: ui("Python hardware DSL and generation configuration", "Python 硬件 DSL 与生成配置"),
      edacode: ui("Analog or mixed-signal design workspace and model provider", "模拟/混合信号设计工作区与模型 Provider"),
      implcraft: ui("Registered RTL and implementation configuration", "已登记 RTL 与实现配置"),
    };
    const localized = {
      tcadcraft: {layer: ui("device", "器件"), summary: ui("Parameterized device structure and bounded physics validation.", "参数化器件结构与有界物理一致性验证。"), execution: ui("Local structure validation", "本地结构验证")},
      momcraft: {layer: ui("interconnect", "互连"), summary: ui("Numerical microstrip S-parameter extraction with the upstream solver.", "使用上游数值求解器提取微带 S 参数。"), execution: ui("Bounded numerical solver", "有界数值求解")},
      cktcraft: {layer: ui("circuit", "电路"), summary: ui("SPICE operating-point simulation with the upstream rfsim solver.", "使用上游 rfsim 求解器进行 SPICE 工作点仿真。"), execution: ui("Bounded netlist solver", "有界网表求解")},
    }[slug];
    return {
      ...component, slug, id: component.plugin_id,
      ...(localized ? {layer: localized.layer, summary: localized.summary, execution_class: localized.execution} : {}),
      source_commit: platform.extensions.source_commit,
      status_label: slug === "implcraft" ? ui("Main-flow adapter", "主流程适配器") : (["tcadcraft", "momcraft", "cktcraft"].includes(slug) ? ui("Runnable", "可运行") : ui("Research adapter", "研究适配器")),
      input: inputs[slug] || "Component-specific research input",
      workflow: ["Inherit the current project context", "Validate the component-specific input", "Submit an isolated design task", "Register artifacts, metrics, versions, and status"],
    };
  });
  return [...special, ...craft];
}

async function loadPlatform() {
  try {
    const [platform, health] = await Promise.all([api("/api/platform"), api("/api/health")]);
    state.platform = platform;
    state.health = health;
    state.extensions = buildExtensions(platform);
    renderWorkerHealth(health);
    if (state.requestedExtension) selectExtension(state.requestedExtension);
  } catch (error) {
    $("#healthDot").className = "bad";
    $("#healthText").textContent = ui("API unavailable", "API 不可用");
    if ($("#workerState")) $("#workerState").textContent = ui("Execution status unavailable", "无法读取执行状态");
  }
  if (state.healthPoll) clearTimeout(state.healthPoll);
  state.healthPoll = setTimeout(loadHealth, 5000);
}

async function loadHealth() {
  try { state.health = await api("/api/health"); renderWorkerHealth(state.health); }
  catch (_) { renderWorkerHealth({ok: false, runtime_worker_ready: false}); }
  if (state.healthPoll) clearTimeout(state.healthPoll);
  state.healthPoll = setTimeout(loadHealth, 5000);
}

function renderWorkerHealth(health) {
  const ready = health.ok && health.runtime_worker_ready;
  $("#healthDot").className = ready ? "ok" : "bad";
  $("#healthText").textContent = ready ? ui("System ready", "系统就绪") : ui("Service offline", "服务离线");
  const indicator = $("#workerIndicator");
  if (indicator) {
    indicator.classList.toggle("ready", ready);
    $("#workerState").textContent = ready
      ? (health.runtime_worker_status === "running" ? ui("Execution in progress", "正在执行任务") : ui("Execution service ready", "执行服务就绪"))
      : ui("Execution service offline", "执行服务离线");
  }
}

async function loadRtlscoutStatus() {
  try {
    const status = await api("/api/extensions/rtlscout");
    state.rtlscoutStatus = status;
    const scanned = status.offline_demo?.benchmarks || [];
    const benchmarks = scanned.length ? scanned : ["simple_adder"];
    $("#rtlscoutBenchmark").innerHTML = benchmarks.map(name => `<option value="${esc(name)}">${esc(name)}</option>`).join("");
    $("#runRtlscout").disabled = !status.ready;
    if (!status.byok?.input_enabled) {
      $("#providerState").textContent = ui("Secure HTTPS connection required", "需要安全 HTTPS 连接");
      $("#providerHint").textContent = ui("The offline demo needs no API key. Custom-provider profiles are accepted only through HTTPS; keys remain memory-only and never enter the project database.", "离线演示不需要 API Key。自定义 Provider 仅通过 HTTPS 接收，密钥只保存在内存中，不写入项目数据库。");
    }
    if (!status.ready) message("#rtlscoutMessage", `${ui("RTLScout is unavailable", "RTLScout 当前不可用")}: ${status.reason}`, true);
    updateRtlscoutControls();
  } catch (error) {
    $("#rtlscoutBenchmark").innerHTML = '<option value="simple_adder">simple_adder</option>';
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
    const options = `<option value="">${ui("Select a registered design", "选择已登记设计")}</option>` + state.designs.map((design, index) => `<option value="${esc(design.id)}">${ui("Design", "设计")} ${String(index + 1).padStart(2, "0")} · ${esc(design.module)}</option>`).join("");
    $("#frontendDesign").innerHTML = options;
    $("#backendDesign").innerHTML = options;
    renderDesignChips();
    const id = preferred || state.selectedDesign?.id;
    if (id) {
      $("#frontendDesign").value = id;
      $("#backendDesign").value = id;
      await selectDesign(id);
    } else {
      state.selectedDesign = null;
      $("#frontendDesign").value = "";
      $("#backendDesign").value = "";
      resetDesignResult();
    }
  } catch (error) {
    message("#specMessage", error.message, true);
  }
}

function resetDesignResult() {
  $("#designMeta").innerHTML = `<div><b>${ui("No design selected", "尚未选择设计")}</b><span>${ui("Choose an example, upload RTL, or select a registered design.", "请选择示例、上传 RTL，或主动选择一个已登记设计。")}</span></div><div class="metric"><strong>—</strong><small>${ui("Gate instances", "门级实例")}</small></div><div class="metric"><strong>—</strong><small>${ui("Ports", "端口")}</small></div>`;
  $("#frontendCanvas").innerHTML = `<div class="empty"><span>◇</span><h3>${ui("Your synthesized circuit will appear here.", "综合后的电路将在这里显示。")}</h3><p>${ui("Nothing from an earlier session is selected automatically.", "平台不会自动加载上一次会话的结果。")}</p></div>`;
}

function renderDesignChips() {
  const root = $("#backendDesignChips");
  if (!root) return;
  const selected = $("#backendDesign")?.value || state.selectedDesign?.id;
  root.innerHTML = state.designs.length ? state.designs.map((design, index) => `<button type="button" class="${design.id === selected ? "active" : ""}" data-backend-design="${esc(design.id)}">▶ ${ui("Design", "设计")} ${String(index + 1).padStart(2, "0")} · ${esc(design.module)}</button>`).join("") : `<div class="empty-row">${ui("No frontend designs are registered yet.", "尚未登记前端设计。")}</div>`;
  $$('[data-backend-design]', root).forEach(button => button.addEventListener("click", () => selectDesign(button.dataset.backendDesign)));
}

async function selectDesign(id) {
  if (!id) return;
  if (state.selectedDesign?.id && state.selectedDesign.id !== id) {
    state.pendingCampaign = null;
    if ($("#batchPlanReview")) $("#batchPlanReview").hidden = true;
  }
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
  await loadRuns();
  if (state.selectedExtension) selectExtension(state.selectedExtension);
}

async function renderDesignView() {
  const design = state.selectedDesign;
  if (!design) return;
  const canvas = $("#frontendCanvas");
  $$('[data-design-view]').forEach(button => button.classList.toggle("active", button.dataset.designView === state.designView));
  if (state.designView === "schematic") {
    const schematic = `/api/designs/${encodeURIComponent(design.id)}/schematic.svg`;
    canvas.innerHTML = `<div class="schematic-toolbar"><span>${ui("Click the schematic to inspect it at full resolution.", "点击电路图可按原始分辨率查看。")}</span><div><a href="${schematic}" target="_blank" rel="noopener">${ui("Open full size", "放大查看")}</a><a href="${schematic}" download="${esc(design.module)}-schematic.svg">${ui("Download SVG", "下载 SVG")}</a></div></div><a class="schematic-zoom" href="${schematic}" target="_blank" rel="noopener"><img src="${schematic}" alt="Synthesized circuit schematic"></a>`;
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
    const payload = {
      message: prompt,
      provider: state.health?.server_spec_model_ready ? "codex-cli" : "deterministic",
      model: state.health?.server_spec_model || "gpt-5.6-sol",
    };
    if (state.providerProfile?.secret?.handle) {
      payload.provider = "openai-compatible-byok";
      payload.profile_id = state.providerProfile.profile_id;
      payload.secret_handle = state.providerProfile.secret.handle;
      payload.model = state.providerProfile.model;
    }
    if (state.selectedDesign) payload.design_id = state.selectedDesign.id;
    const result = await post("/api/spec/sessions", payload);
    state.specSession = result;
    renderSpecReview(result);
    message("#specMessage", ui("Specification draft created by the shared server model. Review the structured RTL below.", "服务器共享模型已生成规格草案，请在下方审查结构化 RTL。"));
  } catch (error) {
    message("#specMessage", error.message, true);
  } finally {
    button.disabled = false;
  }
}

function renderSpecReview(session) {
  const root = $("#specReview");
  const proposal = session?.state || {};
  root.hidden = false;
  const questions = proposal.clarification_questions || [];
  const assumptions = proposal.assumptions || [];
  $("#specReviewSummary").innerHTML = `<b>${esc(proposal.top || ui("Top module pending", "顶层模块待确认"))}</b><br>${esc(proposal.functionality || proposal.objective || "")}<br>${assumptions.length ? `${ui("Assumptions", "假设")}: ${esc(assumptions.join("; "))}` : ""}${questions.length ? `<br>${ui("Questions", "待确认问题")}: ${esc(questions.join("; "))}` : ""}`;
  $("#specRtlPreview").textContent = proposal.rtl_source || "";
  const needsClarification = session.status === "clarification_required";
  $("#specClarificationLabel").hidden = !needsClarification;
  $("#continueSpec").hidden = !needsClarification;
  $("#approveSpecRtl").hidden = !(proposal.ready_for_execution && proposal.rtl_source && !session.design_id);
}

async function continueSpec() {
  const answer = $("#specClarification").value.trim();
  if (!state.specSession || !answer) return message("#specMessage", ui("Answer the clarification question first.", "请先回答待确认问题。"), true);
  const button = $("#continueSpec");
  button.disabled = true;
  try {
    const result = await post(`/api/spec/sessions/${encodeURIComponent(state.specSession.session_id)}/turn`, {message: answer});
    state.specSession = result;
    $("#specClarification").value = "";
    renderSpecReview(result);
    message("#specMessage", ui("Specification updated. Review it again before approval.", "规格已更新，请再次审查后确认。"));
  } catch (error) { message("#specMessage", error.message, true); }
  finally { button.disabled = false; }
}

async function approveSpecRtl() {
  if (!state.specSession) return;
  const button = $("#approveSpecRtl");
  button.disabled = true;
  try {
    const result = await post(`/api/spec/sessions/${encodeURIComponent(state.specSession.session_id)}/register-rtl`, {confirmed: true});
    state.specSession = result.session;
    renderSpecReview(result.session);
    message("#specMessage", ui(`${result.design.module} is registered and synthesized. Continue in Backend Design when ready.`, `${result.design.module} 已登记并完成综合；准备好后可进入后端设计。`));
    await loadDesigns(result.design.id);
  } catch (error) { message("#specMessage", error.message, true); }
  finally { button.disabled = false; }
}

async function saveProvider() {
  const key = $("#providerKey").value;
  if (!key) return message("#rtlscoutMessage", "Enter an API key before connecting the provider.", true);
  try {
    const result = await post("/api/providers", {profile_id: `web-provider-${Date.now()}`, base_url: $("#providerUrl").value, model: $("#providerModel").value, api_key: key});
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
      ? (state.rtlscoutStatus?.byok?.input_enabled ? ui("Not connected", "尚未连接") : ui("Secure HTTPS connection required", "需要安全 HTTPS 连接"))
      : ui("Not required for offline demo", "离线演示无需 Provider");
  }
  $("#rtlscoutModeNote").textContent = byok
    ? ui("Custom-provider execution is not enabled in this preview. HTTPS accepts a session-only key, while full agent execution still requires the secure model bridge.", "当前预览站尚未启用自定义 Provider 的完整执行。HTTPS 可接收仅限会话的密钥，完整 Agent 执行仍需安全模型桥接。")
    : ui("The offline demo uses the official deterministic model while real Verilator and Yosys verify and score every generated candidate.", "离线演示使用官方确定性模型，真实 Verilator 与 Yosys 负责验证和评分。") ;
  $("#rtlscoutLaunchSummary").textContent = state.locale === "zh" ? `${byok ? "自定义 Provider" : "离线验证演示"} · ${benchmark} · 最小化 ${cost.replaceAll("_", " ")} · ${steps} 步` : `${byok ? "Custom provider" : "Offline verified demo"} · ${benchmark} · minimize ${cost.replaceAll("_", " ")} · ${steps} steps`;
  $("#runRtlscout").textContent = byok ? ui("Unavailable in Preview", "预览站暂不可用") : ui("Run Offline Demo →", "运行离线演示 →");
  $("#runRtlscout").disabled = byok || state.rtlscoutStatus?.ready === false;
}

async function submitRtlscout() {
  const mode = $("#rtlscoutMode").value;
  if (mode === "byok") return message("#rtlscoutMessage", ui("Full BYOK exploration is not enabled in this preview. Use the verified offline demo.", "当前预览站尚未启用完整 BYOK 探索，请使用可验证的离线演示。"), true);
  const button = $("#runRtlscout");
  button.disabled = true;
  message("#rtlscoutMessage", ui("Saving the verified RTLScout experiment…", "正在保存可验证的 RTLScout 实验……"));
  try {
    const result = await post("/api/extensions/rtlscout/runs", {
      mode,
      benchmark: $("#rtlscoutBenchmark").value,
      cost_metric: $("#rtlscoutCost").value,
      max_steps: Number($("#rtlscoutSteps").value),
    });
    const runId = result.run?.run?.run_id;
    state.selectedRtlscoutRun = runId || null;
    message("#rtlscoutMessage", ui("The RTLScout task is saved. Its live status appears below.", "RTLScout 任务已保存，实时状态显示在下方。"));
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
    const selectedDesignId = state.selectedDesign?.id;
    const physicalRuns = state.runs.filter(run => selectedDesignId && run.design_id === selectedDesignId && (["orfs", "taiwei-pin-3d", "implcraft"].includes(run.plugin_id) || ["edacraft-tcadcraft", "edacraft-momcraft", "edacraft-cktcraft"].includes(run.plugin_id)));
    $("#runSelect").innerHTML = `<option value="">${selectedDesignId ? ui("Choose a design task", "选择该设计的任务") : ui("Select a design first", "请先选择设计")}</option>` + physicalRuns.map((run, index) => `<option value="${esc(run.run_id)}">${ui("Task", "任务")} ${String(index + 1).padStart(2, "0")} · ${esc(humanStatus(run.status))}</option>`).join("");
    const preferredPhysical = physicalRuns.find(run => run.run_id === preferred)?.run_id;
    const selectedPhysical = physicalRuns.find(run => run.run_id === state.selectedRun?.run?.run_id)?.run_id;
    const id = preferredPhysical || selectedPhysical;
    if (id) {
      $("#runSelect").value = id;
      await selectRun(id);
    } else resetRunResult();
    await renderRtlscoutDashboard();
    if (state.selectedExtension) selectExtension(state.selectedExtension);
  } catch (error) {
    message("#flowMessage", error.message, true);
  }
}

function designModule(designId) {
  return state.designs.find(design => design.id === designId)?.module || ui("registered design", "已登记设计");
}

function runDisplayName(runId) {
  const physical = state.runs.filter(run => run.design_id === state.selectedDesign?.id && (["orfs", "taiwei-pin-3d", "implcraft"].includes(run.plugin_id) || ["edacraft-tcadcraft", "edacraft-momcraft", "edacraft-cktcraft"].includes(run.plugin_id)));
  const index = physical.findIndex(run => run.run_id === runId);
  return `${ui("Run", "任务")} ${String(index >= 0 ? index + 1 : 1).padStart(2, "0")}`;
}

function humanStatus(status) {
  const labels = {
    queued: ["Waiting", "等待中"], preparing: ["Starting", "正在启动"],
    running: ["In progress", "运行中"], retry_wait: ["Retry scheduled", "等待重试"],
    cancel_requested: ["Stopping", "正在停止"], cancelled: ["Stopped", "已停止"],
    succeeded: ["Completed", "已完成"], failed: ["Needs attention", "需要处理"],
  };
  const value = labels[status] || [status, status];
  return ui(value[0], value[1]);
}

function waitText(wait) {
  const people = Number(wait?.people_ahead || 0);
  const seconds = Number(wait?.estimated_wait_seconds || 0);
  const estimate = seconds < 60 ? ui("under a minute", "不到 1 分钟")
    : seconds < 3600 ? ui(`about ${Math.max(1, Math.round(seconds / 60))} min`, `约 ${Math.max(1, Math.round(seconds / 60))} 分钟`)
      : ui(`about ${(seconds / 3600).toFixed(1)} h`, `约 ${(seconds / 3600).toFixed(1)} 小时`);
  if (!people) return ui("No one is ahead · expected to start shortly", "前面无人 · 预计很快开始");
  return ui(`${people} ${people === 1 ? "person" : "people"} ahead · estimated wait ${estimate}`, `前面有 ${people} 位用户 · 预计等待 ${estimate}`);
}

function resetRunResult() {
  if (state.runtimePoll) clearTimeout(state.runtimePoll);
  state.selectedRun = null;
  $("#runSelect").value = "";
  $("#runHeading").innerHTML = `<div><b>${ui("No run selected", "尚未选择任务")}</b><span>${ui("Choose a run explicitly; previous results are never opened automatically.", "请主动选择任务；平台不会自动打开历史结果。")}</span></div>`;
  renderStageRail(new Map());
  $("#backendEvidence").innerHTML = `<div class="empty"><span>□</span><h3>${ui("Run evidence will appear here.", "运行证据将在这里显示。")}</h3><p>${ui("Start a new run or choose a completed record above.", "请开始新任务，或从上方主动选择已完成记录。")}</p></div>`;
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
  const latest = state.runs.find(run => run.plugin_id === "rtlscout" && run.run_id === state.selectedRtlscoutRun);
  if (!latest) {
    $("#rtlscoutRunLabel").textContent = ui("No run started in this session", "本次会话尚未启动任务");
    $("#rtlscoutStatus").textContent = ui("Idle", "空闲");
    $("#rtlscoutStatus").className = "status";
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
  $("#rtlscoutRunLabel").textContent = `${ui("Current session run", "本次会话任务")} · ${run.task_spec?.inputs?.benchmark || run.task_spec?.design_id || "experiment"}`;
  $("#rtlscoutStatus").textContent = status;
  $("#rtlscoutStatus").className = `status ${status}`;
  const started = run.started_at ? new Date(run.started_at).getTime() : null;
  const ended = run.ended_at ? new Date(run.ended_at).getTime() : Date.now();
  $("#rtlscoutRuntime").textContent = started ? `${Math.max(0, (ended - started) / 1000).toFixed(1)} s` : "Waiting";
  $("#rtlscoutCurrentStep").textContent = status === "queued" ? ui("Waiting to start", "等待开始") : status === "running" ? ui("Agent evaluation", "Agent 评估") : status === "succeeded" ? ui("Complete", "完成") : ui("Stopped", "已停止");
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
    $("#rtlscoutCandidateRows").textContent = status === "failed" ? ui("The task stopped before verified candidate evidence was registered.", "任务在登记候选验证证据前停止。") : ui("Waiting for verified candidate evidence.", "正在等待候选验证证据。" );
    const attempts = (detail.stages || []).flatMap(stage => stage.attempts || []);
    const failure = attempts.at(-1)?.failure;
    $("#rtlscoutBestSummary").textContent = failure?.message || (status === "queued" ? ui("The task is saved and waiting to start.", "任务已保存，正在等待开始。") : ui("Verified artifacts will appear after the task completes.", "任务完成后会显示验证产物。"));
  }
  if (state.rtlscoutPoll) clearTimeout(state.rtlscoutPoll);
  if (["queued", "running", "cancel_requested"].includes(status)) {
    state.rtlscoutPoll = setTimeout(() => loadRuns(), 4000);
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
  if (state.runtimePoll) clearTimeout(state.runtimePoll);
  if (!id) return resetRunResult();
  const detail = await api(`/api/runtime/runs/${encodeURIComponent(id)}`);
  state.selectedRun = detail;
  const run = detail.run;
  const task = run.task_spec || {};
  $("#runHeading").innerHTML = `<div><b>${esc(runDisplayName(run.run_id))} · ${esc(designModule(task.design_id))}</b><span>${esc(task.parameters?.target_stage || ui("extension task", "扩展任务"))} · ${esc(formatDate(run.created_at))}</span></div><span class="status ${esc(run.status)}">${esc(humanStatus(run.status))}</span>`;
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
    const workerReady = $("#workerIndicator")?.classList.contains("ready");
    $("#backendEvidence").innerHTML = `<div class="empty"><span>⋯</span><h3>${workerReady ? esc(waitText(detail.wait)) : ui("Execution service is temporarily offline.", "执行服务暂时离线。")}</h3><p>${workerReady ? ui("This page will update automatically when your design starts.", "设计开始后，本页面会自动更新。") : ui("Your task is saved and will continue after the service recovers.", "任务已经保存，服务恢复后会继续执行。")}</p></div>`;
    if (["queued", "preparing", "running", "retry_wait", "cancel_requested"].includes(run.status)) state.runtimePoll = setTimeout(() => loadRuns(run.run_id), 3000);
    return;
  }
  const artifacts = attempt.artifacts || [];
  const metrics = attempt.metrics || [];
  renderBackendEvidence(detail, artifacts, metrics, attempt.failure);
  if (["queued", "preparing", "running", "retry_wait", "cancel_requested"].includes(run.status)) state.runtimePoll = setTimeout(() => loadRuns(run.run_id), 3000);
}

const QOR_LABELS = {
  instance_count: ["Standard cells", "标准单元数"], area_um2: ["Instance area", "实例面积"],
  die_area_um2: ["Die area", "Die 面积"], core_area_um2: ["Core area", "Core 面积"],
  utilization_pct: ["Utilization", "利用率"], setup_wns_ns: ["Setup WNS", "Setup WNS"],
  setup_tns_ns: ["Setup TNS", "Setup TNS"], hold_wns_ns: ["Hold WNS", "Hold WNS"],
  hold_tns_ns: ["Hold TNS", "Hold TNS"], drc_errors: ["DRC violations", "DRC 违例"],
  wirelength_um: ["Wirelength", "总线长"], estimated_wirelength_um: ["Estimated wirelength", "预估线长"],
  power_W: ["Total power", "总功耗"], fmax_mhz: ["Maximum frequency", "最大频率"],
  clock_period_ns: ["Clock period", "时钟周期"], via_count: ["Total vias", "Via 总数"],
  via_singlecut_count: ["Single-cut vias", "单孔 Via"], via_multicut_count: ["Multi-cut vias", "多层 Via"],
  antenna_violations: ["Antenna violations", "天线违例"], congestion_overflow: ["Congestion overflow", "拥塞溢出"],
  skew_ns: ["Clock skew", "时钟偏移"], grt_overflow_iterations: ["Overflow iterations", "拥塞迭代次数"],
};

const QOR_UNITS = {
  area_um2: "µm²", die_area_um2: "µm²", core_area_um2: "µm²", utilization_pct: "%",
  setup_wns_ns: "ns", setup_tns_ns: "ns", hold_wns_ns: "ns", hold_tns_ns: "ns",
  wirelength_um: "µm", estimated_wirelength_um: "µm", power_W: "W", fmax_mhz: "MHz",
  clock_period_ns: "ns", skew_ns: "ns",
};

function reportMetric(report, key) {
  const direct = report?.kpi?.[key];
  if (direct !== null && direct !== undefined) return direct;
  for (const stage of ["finish", "route", "cts", "place", "floorplan", "synth"]) {
    const value = report?.stages?.[stage]?.metrics?.[key];
    if (value !== null && value !== undefined) return value;
  }
  return null;
}

function metricText(key, value) {
  if (value === null || value === undefined) return "—";
  const number = Number(value);
  let text = String(value);
  if (Number.isFinite(number)) {
    if (number !== 0 && Math.abs(number) < .001) text = number.toExponential(2);
    else text = Number.isInteger(number) ? String(number) : number.toFixed(Math.abs(number) >= 100 ? 2 : 4).replace(/0+$/, "").replace(/\.$/, "");
  }
  return `${text}${QOR_UNITS[key] ? ` ${QOR_UNITS[key]}` : ""}`;
}

function metricCard(key, value, featured = false) {
  const label = QOR_LABELS[key] || [key, key];
  return `<div class="qor-report-card ${featured ? "featured" : ""}"><b>${esc(metricText(key, value))}</b><small>${esc(ui(label[0], label[1]))}</small></div>`;
}

function artifactTitle(artifact) {
  const presentation = artifact.presentation || {};
  return state.locale === "zh" ? (presentation.title_zh || artifact.kind) : (presentation.title_en || artifact.kind);
}

function renderArtifacts(artifacts) {
  if (!artifacts.length) return "";
  const order = {synth: 1, floorplan: 2, place: 3, cts: 4, route: 5, finish: 6};
  const sorted = [...artifacts].sort((a, b) => (order[a.presentation?.stage] || 20) - (order[b.presentation?.stage] || 20) || artifactTitle(a).localeCompare(artifactTitle(b)));
  return `<section class="evidence-block"><div class="evidence-block-head"><b>${ui("Implementation files", "实现产物")}</b><span>${ui("Stage and purpose are shown before the raw filename.", "标题优先说明阶段和用途，原始文件名保留在下方。")}</span></div><div class="artifact-grid readable-artifacts">${sorted.map(artifact => `<a class="artifact-link" href="${esc(artifact.url)}" target="_blank" rel="noopener"><b>${esc(artifactTitle(artifact))}</b><span>${esc(artifact.presentation?.filename || artifact.store_key)} · SHA-256 ${esc((artifact.sha256 || "").slice(0, 10))}…</span></a>`).join("")}</div></section>`;
}

function renderBackendEvidence(detail, artifacts, runtimeMetrics, failure) {
  const report = detail.analysis_report?.report;
  const views = artifacts.filter(artifact => ["layout_view", "three_d_view"].includes(artifact.kind));
  const visual = views.length ? `<div class="layout-report-grid">${views.map(view => `<figure class="layout-figure"><img src="${esc(view.url)}" alt="${esc(artifactTitle(view))}"><figcaption><b>${esc(artifactTitle(view))}</b><span>${esc(view.presentation?.filename || view.store_key)}</span></figcaption></figure>`).join("")}${report?.cell_density?.available ? `<figure class="density-figure"><canvas id="densityHeatmap" width="420" height="420"></canvas><figcaption><b>${ui("Placement-density heatmap", "布局密度热力图")}</b><span>${esc(report.cell_density.density_unit || ui("Normalized density", "归一化密度"))}</span></figcaption></figure>` : ""}</div>` : `<div class="empty compact-empty"><span>□</span><h3>${ui("No registered layout preview in this attempt.", "该次尝试尚未登记版图预览。")}</h3><p>${ui("The report and implementation files remain available below.", "分析报告与实现产物仍会显示在下方。")}</p></div>`;

  if (!report) {
    const metricCards = runtimeMetrics.length ? `<div class="qor-grid">${runtimeMetrics.slice(0, 15).map(metric => `<div class="qor-card"><b>${esc(metric.value)}${metric.unit ? ` ${esc(metric.unit)}` : ""}</b><small>${esc(metric.name)}</small></div>`).join("")}</div>` : "";
    $("#backendEvidence").innerHTML = `${visual}${metricCards}${renderArtifacts(artifacts)}${failure ? `<div class="message error">${esc(failure.message || failure.category)}</div>` : ""}`;
    return;
  }

  const featuredKeys = ["instance_count", "area_um2", "utilization_pct", "setup_wns_ns", "drc_errors"];
  const detailKeys = ["setup_tns_ns", "hold_wns_ns", "hold_tns_ns", "power_W", "fmax_mhz", "clock_period_ns", "wirelength_um", "via_count", "via_singlecut_count", "via_multicut_count", "antenna_violations", "congestion_overflow"];
  const featured = featuredKeys.map(key => metricCard(key, reportMetric(report, key), true)).join("");
  const detailed = detailKeys.filter(key => reportMetric(report, key) !== null).map(key => metricCard(key, reportMetric(report, key))).join("");
  const diagnosis = [...(report.diagnosis?.violations || []), ...(report.diagnosis?.observations || [])];
  const diagnosisHtml = diagnosis.length ? diagnosis.map(item => `<article class="diagnosis-row ${esc(item.severity || "info")}"><b>${esc(item.type || ui("Observation", "观察"))}</b><p>${esc(item.message || "")}</p>${item.recommendation ? `<span>${esc(item.recommendation)}</span>` : ""}</article>`).join("") : `<article class="diagnosis-row clean"><b>${ui("No rule-based violation", "未发现规则违例")}</b><p>${esc(report.diagnosis?.summary || ui("The available timing and DRC evidence is clean.", "现有时序与 DRC 证据正常。"))}</p></article>`;
  const period = Number(reportMetric(report, "clock_period_ns"));
  const wns = Number(reportMetric(report, "setup_wns_ns"));
  const dataDelay = Number.isFinite(period) && Number.isFinite(wns) ? period - wns : null;
  const timingPct = dataDelay !== null && period > 0 ? Math.max(0, Math.min(100, dataDelay / period * 100)) : 0;
  const timing = dataDelay !== null ? `<section class="timing-panel"><div class="evidence-block-head"><b>${ui("Timing analysis", "时序分析")}</b><span>${ui("Target period and measured setup slack", "目标周期与实测 Setup 裕量")}</span></div><div class="timing-track"><span style="width:${timingPct.toFixed(1)}%"></span></div><div class="timing-values"><span>${ui("Data-path delay", "数据路径延迟")}: ${esc(metricText("clock_period_ns", dataDelay))}</span><span>WNS: ${esc(metricText("setup_wns_ns", wns))}</span><span>Fmax: ${esc(metricText("fmax_mhz", reportMetric(report, "fmax_mhz")))}</span></div></section>` : "";
  const stageCount = Object.values(report.stages || {}).filter(stage => stage.status === "completed").length;
  const narrative = `<section class="engineering-report"><div class="evidence-block-head"><b>${ui("Evidence-based engineering report", "基于证据的工程分析报告")}</b><span>${ui("Deterministic report from registered OpenROAD metrics; no model-generated numbers.", "由已登记 OpenROAD 指标确定性生成，不使用模型编造数字。")}</span></div><div class="report-copy"><p><b>${ui("1. Flow and area", "1. 流程与面积")}</b>${ui(`The ${report.design || "design"} flow completed ${stageCount}/6 physical stages in ${Number(report.runtime_seconds || 0).toFixed(1)} s. Instance area is ${metricText("area_um2", reportMetric(report, "area_um2"))} at ${metricText("utilization_pct", reportMetric(report, "utilization_pct"))} utilization.`, `${report.design || "该设计"} 完成 ${stageCount}/6 个物理设计阶段，总耗时 ${Number(report.runtime_seconds || 0).toFixed(1)} 秒。实例面积为 ${metricText("area_um2", reportMetric(report, "area_um2"))}，利用率为 ${metricText("utilization_pct", reportMetric(report, "utilization_pct"))}。`)}</p><p><b>${ui("2. Timing", "2. 时序")}</b>${ui(`Setup WNS is ${metricText("setup_wns_ns", reportMetric(report, "setup_wns_ns"))}; hold WNS is ${metricText("hold_wns_ns", reportMetric(report, "hold_wns_ns"))}.`, `Setup WNS 为 ${metricText("setup_wns_ns", reportMetric(report, "setup_wns_ns"))}；Hold WNS 为 ${metricText("hold_wns_ns", reportMetric(report, "hold_wns_ns"))}。`)}</p><p><b>${ui("3. Power and routing", "3. 功耗与布线")}</b>${ui(`Total power is ${metricText("power_W", reportMetric(report, "power_W"))}; routed wirelength is ${metricText("wirelength_um", reportMetric(report, "wirelength_um"))} with ${metricText("via_count", reportMetric(report, "via_count"))} vias.`, `总功耗为 ${metricText("power_W", reportMetric(report, "power_W"))}；布线总线长为 ${metricText("wirelength_um", reportMetric(report, "wirelength_um"))}，Via 总数为 ${metricText("via_count", reportMetric(report, "via_count"))}。`)}</p><p><b>${ui("4. Signoff checks", "4. 规则检查")}</b>${ui(`The registered report contains ${metricText("drc_errors", reportMetric(report, "drc_errors"))} DRC violations and ${metricText("antenna_violations", reportMetric(report, "antenna_violations"))} antenna violations.`, `已登记报告包含 ${metricText("drc_errors", reportMetric(report, "drc_errors"))} 个 DRC 违例和 ${metricText("antenna_violations", reportMetric(report, "antenna_violations"))} 个天线违例。`)}</p></div>${report.disclaimer ? `<p class="report-disclaimer">${esc(report.disclaimer)}</p>` : ""}</section>`;

  $("#backendEvidence").innerHTML = `<section class="qor-report"><div class="qor-report-head"><div><small>${esc(report.platform || "OpenROAD")}</small><h3>${esc(report.design || "Design")} · ${ui("physical-design report", "物理设计报告")}</h3><p>${esc(report.diagnosis?.summary || report.flow_status || "")}</p></div><span class="verdict ${esc(report.verdict || "")}">${esc(report.verdict || report.flow_status || "recorded")}</span></div><div class="qor-featured">${featured}</div><div class="diagnosis-list">${diagnosisHtml}</div><div class="qor-detail-grid">${detailed}</div>${timing}</section>${visual}${narrative}${renderArtifacts(artifacts)}${failure ? `<div class="message error">${esc(failure.message || failure.category)}</div>` : ""}`;
  if (report.cell_density?.available) paintDensityHeatmap(report.cell_density.density_map || []);
}

function paintDensityHeatmap(matrix) {
  const canvas = $("#densityHeatmap");
  if (!canvas || !Array.isArray(matrix) || !matrix.length) return;
  const context = canvas.getContext("2d");
  const rows = matrix.length;
  const columns = Math.max(...matrix.map(row => Array.isArray(row) ? row.length : 0));
  const cellWidth = canvas.width / Math.max(columns, 1);
  const cellHeight = canvas.height / rows;
  context.fillStyle = "#f7f8fa";
  context.fillRect(0, 0, canvas.width, canvas.height);
  matrix.forEach((row, y) => (row || []).forEach((raw, x) => {
    const value = Math.max(0, Math.min(1, Number(raw) || 0));
    context.fillStyle = `hsl(${220 - value * 210} 78% ${96 - value * 48}%)`;
    context.fillRect(x * cellWidth, y * cellHeight, Math.ceil(cellWidth), Math.ceil(cellHeight));
  }));
}

async function submitFlow() {
  const id = $("#backendDesign").value;
  if (!id) return message("#flowMessage", ui("Select a registered design first.", "请先选择已登记设计。"), true);
  const button = $("#submitFlow");
  button.disabled = true;
  const mode = $("#flowMode").value;
  if (mode === "baseline") {
    state.pendingCampaign = null;
    $("#batchPlanReview").hidden = true;
  }
  const objective = $('input[name="flowObjective"]:checked')?.value || "balanced";
  message("#flowMessage", mode === "baseline" ? ui("Saving the design task…", "正在保存设计任务……") : ui("Creating a bounded experiment plan for review…", "正在创建有界实验计划，等待审查……"));
  try {
    const base = {design_id: id, clock: $("#flowClock").value.trim() || null, clock_period_ns: Number($("#flowPeriod").value), core_utilization_pct: Number($("#flowUtil").value), place_density: Number($("#flowDensity").value), target_stage: $("#flowTarget").value, objective, flow_mode: mode};
    if (mode === "baseline") {
      const detail = await post("/api/runtime/runs/from-design", base);
      message("#flowMessage", ui("The design task is saved. Its position, estimated wait, and live stage status appear below.", "设计任务已保存；下方会显示前面人数、预计等待时间和实时阶段状态。"));
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
      state.pendingCampaign = campaign;
      renderBatchPlan(campaign);
      message("#flowMessage", ui(`Batch experiment plan created with ${campaign.members.length} candidates. It has not been submitted for execution.`, `批量实验计划已创建，共 ${campaign.members.length} 个候选；尚未提交执行。`));
    }
  } catch (error) {
    message("#flowMessage", error.message, true);
  } finally {
    button.disabled = false;
    updateFlowMode();
  }
}

function renderBatchPlan(campaign) {
  const root = $("#batchPlanReview");
  root.hidden = false;
  $("#batchPlanCandidates").innerHTML = (campaign.members || []).map((member, index) => {
    const parameters = Object.entries(member.parameters || {})
      .filter(([name]) => ["clock_period_ns", "core_utilization_pct", "place_density", "target_stage"].includes(name))
      .map(([name, value]) => `${name.replaceAll("_", " ")} = ${value}`).join(" · ");
    return `<div class="batch-candidate"><span>${String(index + 1).padStart(2, "0")}</span><b>${esc(parameters || ui("Inherited baseline parameters", "继承基线参数"))}</b></div>`;
  }).join("");
}

async function approveBatchPlan() {
  if (!state.pendingCampaign) return;
  const button = $("#approveBatchPlan");
  button.disabled = true;
  message("#flowMessage", ui("Starting the approved batch experiment…", "正在启动已批准的批量实验……"));
  try {
    const result = await post(`/api/campaigns/${encodeURIComponent(state.pendingCampaign.campaign_id)}/submit`, {});
    $("#batchPlanReview").hidden = true;
    state.pendingCampaign = null;
    message("#flowMessage", ui(`${result.run_ids.length} design tasks started. Progress is shown below one task at a time.`, `已启动 ${result.run_ids.length} 个设计任务；下方会逐个显示当前任务进度。`));
    await loadRuns(result.run_ids[0] || null);
  } catch (error) { message("#flowMessage", error.message, true); }
  finally { button.disabled = false; }
}

function updateFlowMode() {
  const mode = $("#flowMode").value;
  const baseline = mode === "baseline";
  $("#submitFlow").textContent = baseline ? ui("Start RTL-to-GDS", "开始 RTL-to-GDS") : ui("Create Batch Plan", "创建批量实验计划");
  $("#flowModeNote").textContent = baseline
    ? ui("Baseline starts one design task using the values above.", "基线模式会按照上方参数启动一个设计任务。")
    : ui("Batch modes create three bounded candidates for review; they do not execute automatically.", "批量模式会创建三个有界候选供审查，不会自动执行。")
}

function openExtension(id) {
  if (!state.auth?.authenticated) return openAuth(ui(
    "Sign in and select a design before opening an extension.",
    "请先登录并选择设计，再打开扩展。"
  ));
  state.requestedExtension = id;
  route("backend");
  if (state.extensions.length) selectExtension(id);
  else if ($("#embeddedExtensionDetail")) $("#embeddedExtensionDetail").innerHTML = `<div class="empty-row">${ui("Loading the selected research branch…", "正在加载所选研究支线……")}</div>`;
}

function successfulBaselineForDesign(designId) {
  if (!designId) return null;
  const selected = state.selectedRun?.run;
  if (selected?.status === "succeeded" && selected.task_spec?.plugin_id === "orfs" && selected.task_spec?.design_id === designId) {
    return state.runs.find(run => run.run_id === selected.run_id) || {
      run_id: selected.run_id, status: selected.status, plugin_id: "orfs", design_id: designId,
    };
  }
  return state.runs.find(run => run.plugin_id === "orfs" && run.design_id === designId && run.status === "succeeded") || null;
}

function extensionCompatibility(id, design, baseline) {
  const inherited = Boolean(design);
  if (id === "taiwei-3d") {
    if (!design) return {tone: "blocked", label: ui("Choose a design", "请先选择设计"), reason: ui("Select a registered design in step ① before opening 3D implementation.", "请先在步骤①选择已登记设计，再打开 3D 实现。")};
    if (!state.health?.taiwei_3d_ready) return {tone: "blocked", label: ui("Toolchain unavailable", "工具链不可用"), reason: state.health?.taiwei_3d_reason || ui("The pinned 3D toolchain is not ready.", "固定 3D 工具链尚未就绪。")};
    const baselineNote = baseline ? ui("A successful 2D baseline for this design will be associated for comparison.", "该设计的成功 2D 基线会作为对比证据关联。") : ui("No 2D baseline required: the 3D flow runs its own synthesis and 2D partition internally.", "无需 2D 基线：3D 流程内部自带综合与 2D 分层阶段。");
    return {tone: "ready", label: ui("Ready for standalone 3D run", "可以启动独立 3D 任务"), reason: baselineNote, action: "taiwei"};
  }
  if (id === "edacraft-tcadcraft") return {tone: "ready", label: ui("Device structure validation available", "器件结构验证可用"), reason: ui("Enter device dimensions below to run upstream TCADCraft geometry and physics-invariant checks. The pinned upstream full PDE solver does not compile because its implementation and header declarations disagree, so full TCAD convergence is not claimed.", "在下方输入器件尺寸即可运行上游 TCADCraft 几何与物理一致性检查。固定上游版本的完整 PDE 求解器因实现与头文件声明不一致而无法编译，因此这里不宣称完整 TCAD 收敛仿真。"), action: "tcadcraft"};
  if (id === "edacraft-momcraft") return {tone: "ready", label: ui("S-parameter solver available", "S 参数求解可用"), reason: ui("Enter microstrip geometry, effective permittivity, mesh size, and frequency. The compiled upstream MoM solver will produce a real Touchstone result. Automatic GDS interconnect extraction is a separate future adapter.", "输入微带几何、有效介电常数、网格数和频率后，平台会调用已编译的上游 MoM 求解器并生成真实 Touchstone 结果。GDS 互连自动提取属于后续独立适配器。"), action: "momcraft"};
  if (id === "edacraft-cktcraft") return {tone: "ready", label: ui("SPICE operating-point solver available", "SPICE 工作点求解可用"), reason: ui("Paste a bounded transistor- or component-level SPICE netlist. The upstream rfsim binary executes a real .op solve; external model includes remain disabled in the public service.", "粘贴有界的晶体管级或元件级 SPICE 网表后，上游 rfsim 会执行真实 .op 求解；公网服务暂不允许外部模型 include。"), action: "cktcraft"};
  if (id === "dplevolve") return {tone: "separate", label: ui("Validated adapter; candidate pipeline not enabled", "适配器已验证；候选流水线尚未开放"), reason: ui("Your understanding is correct: AI should propose an OpenROAD source patch, compile an isolated candidate, run the same designs with baseline and candidate binaries, compare PPA/DRC/runtime and retain only a verified improvement. Today only the pinned repository audit and low-cost adapter smoke are enabled; no expensive candidate generation is exposed yet.", "你的理解是正确的：AI 应提出 OpenROAD 源码补丁，隔离编译候选版本，用同一组设计分别运行基线与候选工具，比较 PPA、DRC 和运行时间，仅保留验证后确实更优的候选。目前只启用了固定仓库审计和低成本适配器 smoke，尚未开放高消耗的候选生成流水线。")};
  return {tone: inherited ? "blocked" : "separate", label: ui("No linked action", "暂无关联动作"), reason: ui("No compatible current-design adapter is available for this component.", "该组件目前没有兼容当前设计的输入适配器。")};
}

function selectExtension(id) {
  const extension = state.extensions.find(item => item.id === id);
  const root = $("#embeddedExtensionDetail");
  if (!extension || !root) return;
  state.requestedExtension = null;
  state.selectedExtension = id;
  const design = state.selectedDesign;
  const baseline = successfulBaselineForDesign(design?.id);
  const compatibility = extensionCompatibility(id, design, baseline);
  const designLabel = design ? `${ui("Design", "设计")} · ${design.module}` : ui("No design selected", "尚未选择设计");
  const baselineLabel = baseline ? `${runDisplayName(baseline.run_id)} · ${ui("2D baseline succeeded", "2D 基线成功")}` : ui("No successful 2D baseline for this design", "当前设计尚无成功 2D 基线");
  let action = "";
  if (compatibility.action === "taiwei") {
    action = `<button class="button primary" data-run-taiwei>${ui("Generate 3D", "生成 3D")} <span>→</span></button>`;
  } else if (compatibility.action === "taiwei-disabled") {
    action = `<button class="button primary" disabled title="${esc(compatibility.reason)}">${ui("Generate 3D", "生成 3D")} <span>→</span></button>`;
  } else {
    action = specialistExtensionForm(compatibility.action);
  }
  const taiweiSupport = id === "taiwei-3d" ? `<div style="margin-top:10px;padding:9px 11px;border:1px dashed var(--line-strong);display:grid;gap:4px"><span style="color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em">${ui("Supported scope", "支持范围")}</span><b style="color:var(--heading);font-size:11px">${ui("Registered design + 3D platform; 2D baseline optional", "已登记设计 + 3D 工艺库；2D 基线可选")}</b></div>` : "";
  root.innerHTML = `<div class="embedded-extension-head"><div><small>${esc(extension.layer)}</small><b>${esc(extension.name)}</b><span>${esc(extension.summary)}</span></div><button type="button" aria-label="Close extension detail" id="closeExtensionDetail">×</button></div>
    <div class="embedded-extension-body"><div class="extension-context"><div><span>${ui("Current design", "当前设计")}</span><b>${esc(designLabel)}</b></div><div><span>${ui("Main-flow evidence", "主线证据")}</span><b>${esc(baselineLabel)}</b></div></div>${taiweiSupport}<div class="extension-meta"><div><span>${ui("Required component input", "扩展所需输入")}</span><b>${esc(extension.input)}</b></div><div><span>${ui("Execution", "执行方式")}</span><b>${esc(extension.execution_class)}</b></div></div>
    <div class="compatibility ${esc(compatibility.tone)}"><span>${ui("Compatibility", "兼容性")}</span><b>${esc(compatibility.label)}</b><p>${esc(compatibility.reason)}</p></div>${id === "taiwei-3d" ? taiwei3dConfigForm() : ""}${action ? `<div class="extension-actions">${action}</div>` : ""}<p class="message" id="extensionMessage"></p></div>`;
  $("#closeExtensionDetail").addEventListener("click", () => { root.innerHTML = ""; state.selectedExtension = null; });
  const taiwei = $('[data-run-taiwei]', root);
  if (taiwei) taiwei.addEventListener("click", () => submitTaiweiExtension(design.id, baseline?.run_id || ""));
  const specialist = $('[data-run-specialist]', root);
  if (specialist) specialist.addEventListener("click", () => submitSpecialistExtension(compatibility.action, design?.id));
  root.scrollIntoView({behavior: "smooth", block: "nearest"});
}

function taiwei3dConfigForm() {
  const period = $("#flowPeriod")?.value || "10";
  const clock = $("#flowClock")?.value?.trim() || "clk";
  return `<div class="specialist-form taiwei3d-form" style="margin-top:12px;display:grid;gap:9px">
    <b style="color:var(--heading);font-size:12px">${ui("3D implementation configuration", "3D 实现配置")}</b>
    <div class="form-grid three">
      <label><span>${ui("3D platform", "3D 工艺库")}</span><select id="taiweiTech"><option value="asap7_3D">ASAP7 3D</option><option value="nangate45_3D">Nangate45 3D</option><option value="asap7_nangate45_3D">ASAP7 · Nangate45 3D</option></select></label>
      <label><span>${ui("Core utilization · %", "核心利用率 · %")}</span><input id="taiweiUtil" type="number" min="1" max="99" value="60"></label>
      <label><span>${ui("Parallel cores", "并行核数")}</span><input id="taiweiCores" type="number" min="1" max="256" value="32"></label>
      <label><span>${ui("CTS layer", "CTS 层")}</span><select id="taiweiCtsLayer"><option value="bottom">Bottom</option><option value="upper">Upper</option></select></label>
      <label><span>${ui("Outer iterations", "分层迭代次数")}</span><input id="taiweiOuter" type="number" min="1" max="16" value="1"></label>
      <label><span>${ui("Clock period · ns", "时钟周期 · ns")}</span><input id="taiweiPeriod" type="number" min="0.01" step="0.1" value="${esc(period)}"></label>
    </div>
    <div class="form-grid">
      <label><span>${ui("Clock port", "时钟端口")}</span><input id="taiweiClock" value="${esc(clock)}"></label>
      <label><span>${ui("Skip engine 2D partition", "跳过引擎 2D 分层")}</span><input id="taiweiSkip2d" type="checkbox" style="width:auto;justify-self:start"></label>
      <label><span>${ui("Split-net flow", "跨层网络切分")}</span><input id="taiweiSplitNet" type="checkbox" checked style="width:auto;justify-self:start"></label>
      <label><span>${ui("Allow-net flow", "允许跨层网络")}</span><input id="taiweiAllowNet" type="checkbox" checked style="width:auto;justify-self:start"></label>
      <label><span>${ui("ABC area mode", "ABC 面积模式")}</span><input id="taiweiAbcArea" type="checkbox" checked style="width:auto;justify-self:start"></label>
    </div>
    <small style="color:var(--muted)">${ui("These engine-native knobs map to CORE_UTILIZATION / NUM_CORES / CTS_LAYER / OUTER_ITERATIONS / SKIP_2D_PART / PIN3D_* / ABC_AREA and are carried into the pinned 3D toolchain.", "这些引擎原生参数将映射为 CORE_UTILIZATION / NUM_CORES / CTS_LAYER / OUTER_ITERATIONS / SKIP_2D_PART / PIN3D_* / ABC_AREA 并注入固定 3D 工具链。")}</small>
  </div>`;
}

function specialistExtensionForm(action) {
  if (action === "tcadcraft") return `<div class="specialist-form"><div class="form-grid three"><label><span>${ui("Length (nm)", "长度（nm）")}</span><input id="tcadLength" type="number" min="1" max="10000" value="10"></label><label><span>${ui("Width (nm)", "宽度（nm）")}</span><input id="tcadWidth" type="number" min="1" max="10000" value="5"></label><label><span>${ui("Height (nm)", "高度（nm）")}</span><input id="tcadHeight" type="number" min="1" max="10000" value="3"></label></div><button class="button primary" data-run-specialist>${ui("Validate device structure", "验证器件结构")} <span>→</span></button></div>`;
  if (action === "momcraft") return `<div class="specialist-form"><div class="form-grid"><label><span>${ui("Length (mm)", "长度（mm）")}</span><input id="momLength" type="number" min="0.01" max="100" step="0.01" value="2"></label><label><span>${ui("Width (mm)", "宽度（mm）")}</span><input id="momWidth" type="number" min="0.001" max="20" step="0.001" value="0.5"></label><label><span>${ui("Height (mm)", "高度（mm）")}</span><input id="momHeight" type="number" min="0.001" max="20" step="0.001" value="0.3"></label><label><span>${ui("Effective ε", "有效介电常数 ε")}</span><input id="momEps" type="number" min="1" max="30" step="0.1" value="3.2"></label><label><span>${ui("Mesh segments", "网格段数")}</span><input id="momMesh" type="number" min="2" max="64" step="1" value="4"></label><label><span>${ui("Frequency (GHz)", "频率（GHz）")}</span><input id="momFrequency" type="number" min="0.001" max="300" step="0.1" value="1"></label></div><button class="button primary" data-run-specialist>${ui("Run S-parameter solve", "运行 S 参数求解")} <span>→</span></button></div>`;
  if (action === "cktcraft") return `<div class="specialist-form"><label><span>${ui("SPICE .op netlist", "SPICE .op 网表")}</span><textarea id="cktNetlist" rows="11">* Resistor-divider operating point\nV1 in 0 5.0\nR1 in mid 2k\nR2 mid 0 3k\nI1 mid 0 1m\n\n.op\n.print v(in) v(mid) i(v1)\n\n.end</textarea></label><small>${ui("This editable template is specialist input, not a result inferred from the selected RTL.", "这是可编辑的专业输入模板，不是从当前 RTL 推导出的结果。")}</small><button class="button primary" data-run-specialist>${ui("Run operating-point simulation", "运行工作点仿真")} <span>→</span></button></div>`;
  return "";
}

async function submitSpecialistExtension(slug, designId) {
  const button = $('[data-run-specialist]', $("#embeddedExtensionDetail"));
  const payload = {design_id: designId || ""};
  if (slug === "tcadcraft") Object.assign(payload, {length_nm: Number($("#tcadLength").value), width_nm: Number($("#tcadWidth").value), height_nm: Number($("#tcadHeight").value)});
  if (slug === "momcraft") Object.assign(payload, {length_mm: Number($("#momLength").value), width_mm: Number($("#momWidth").value), height_mm: Number($("#momHeight").value), eps_eff: Number($("#momEps").value), mesh_segments: Number($("#momMesh").value), frequency_ghz: Number($("#momFrequency").value)});
  if (slug === "cktcraft") payload.spice_netlist = $("#cktNetlist").value;
  button.disabled = true;
  message("#extensionMessage", ui("Saving the specialist analysis task…", "正在保存专业分析任务……"));
  try {
    const detail = await post(`/api/extensions/edacraft/${encodeURIComponent(slug)}/run`, payload);
    message("#extensionMessage", ui("Task saved. Live progress and generated evidence are shown in the design-task dashboard below.", "任务已保存；下方设计任务仪表盘会显示实时进度与生成证据。"));
    await loadRuns(detail.run?.run?.run_id || detail.run?.run_id);
  } catch (error) { message("#extensionMessage", error.message, true); }
  finally { button.disabled = false; }
}

async function submitTaiweiExtension(designId, baselineRunId) {
  const button = $('[data-run-taiwei]', $("#embeddedExtensionDetail"));
  if (!window.confirm(ui("This is a long-running 3D implementation flow. Start it for the selected design with the configured 3D platform?", "这是耗时较长的 3D 实现流程。确认按当前 3D 工艺库配置对所选设计启动吗？"))) return;
  button.disabled = true;
  message("#extensionMessage", ui("Saving the 3D task…", "正在保存 3D 任务……"));
  const payload = {design_id: designId};
  if (baselineRunId) payload.baseline_run_id = baselineRunId;
  const tech = $("#taiweiTech")?.value;
  if (tech) payload.tech = tech;
  const clock = $("#taiweiClock")?.value?.trim();
  if (clock) payload.clock = clock;
  const period = Number($("#taiweiPeriod")?.value);
  if (Number.isFinite(period) && period > 0) payload.clock_period_ns = period;
  const util = Number($("#taiweiUtil")?.value);
  if (Number.isFinite(util) && util >= 1 && util <= 99) payload.core_utilization_pct = util;
  const cores = Number($("#taiweiCores")?.value);
  if (Number.isInteger(cores) && cores >= 1 && cores <= 256) payload.num_cores = cores;
  const cts = $("#taiweiCtsLayer")?.value;
  if (cts) payload.cts_layer = cts;
  const outer = Number($("#taiweiOuter")?.value);
  if (Number.isInteger(outer) && outer >= 1 && outer <= 16) payload.outer_iterations = outer;
  const skip = $("#taiweiSkip2d")?.checked;
  if (typeof skip === "boolean") payload.skip_2d_part = skip;
  const split = $("#taiweiSplitNet")?.checked;
  if (typeof split === "boolean") payload.pin3d_split_net_flow = split;
  const allow = $("#taiweiAllowNet")?.checked;
  if (typeof allow === "boolean") payload.pin3d_allow_net_flow = allow;
  const abc = $("#taiweiAbcArea")?.checked;
  if (typeof abc === "boolean") payload.abc_area = abc;
  try {
    const detail = await post("/api/extensions/taiwei/run", payload);
    if (detail?.status === "guidance_required") {
      message("#extensionMessage", state.locale === "zh" ? (detail.message_zh || detail.message) : detail.message, true);
      return;
    }
    message("#extensionMessage", ui("The 3D task is saved. Configured parameters and evidence remain recorded with the run.", "3D 任务已保存；配置参数与运行证据会随任务记录。"));
    await loadRuns(detail.run?.run_id);
  } catch (error) {
    message("#extensionMessage", error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function loadResults() {
  try {
    const suffix = state.developerView ? "?scope=all" : "";
    state.results = (await api(`/api/platform/results${suffix}`)).records || [];
    renderResults();
  } catch (error) {
    $("#resultList").innerHTML = `<div class="empty-row">${esc(error.message)}</div>`;
  }
}

function renderResults() {
  const records = state.results.filter(record => state.resultFilter === "all" || record.record_type === state.resultFilter);
  $("#resultList").innerHTML = records.length ? records.map(record => `<button class="result-row" data-result="${esc(record.id)}"><span class="record-kind">${esc(record.project_type)}</span><div><b>${esc(resultDisplayName(record))} · ${esc(record.name)}</b><span>${state.developerView ? `${esc(record.owner_username || "Legacy / system")} · ` : ""}${esc(record.summary)} · ${esc(humanStatus(record.status))}</span></div><time>${formatDate(record.created_at)}</time><i>→</i></button>`).join("") : `<div class="empty"><span>○</span><h3>${ui("No work recorded yet.", "还没有设计记录。")}</h3><p>${ui("Create or import a design to begin your personal workspace.", "创建或导入一个设计，开始使用个人工作区。")}</p></div>`;
  $$('[data-result]').forEach(button => button.addEventListener("click", () => selectResult(button.dataset.result)));
}

function resultDisplayName(record) {
  const sameType = state.results.filter(item => item.record_type === record.record_type);
  const index = sameType.findIndex(item => item.id === record.id);
  return `${record.record_type === "design" ? ui("Design", "设计") : ui("Run", "任务")} ${String(index >= 0 ? index + 1 : 1).padStart(2, "0")}`;
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
    const learningAction = record.record_type === "runtime_run" && record.status === "succeeded"
      ? `<div class="learning-collection"><div><b>${ui("Add verified experience to Self-Evolution", "将验证经验加入自演化")}</b><span>${ui("This explicit action preserves provenance and prevents failed runs from becoming training truth.", "该操作会保留来源，并避免失败任务成为学习事实。")}</span></div><button class="button primary" id="collectLearning">${ui("Collect verified run", "收集验证结果")}</button><p class="message" id="collectLearningMessage"></p></div>` : "";
    const designArtifacts = record.record_type === "design" ? `<div class="artifact-grid"><a class="artifact-link" href="/api/designs/${encodeURIComponent(record.id)}/source?kind=rtl" target="_blank"><b>RTL source</b><span>${esc(detail.rtl_file)}</span></a><a class="artifact-link" href="/api/designs/${encodeURIComponent(record.id)}/source?kind=netlist" target="_blank"><b>Gate netlist</b><span>${esc(detail.netlist_file)}</span></a><a class="artifact-link" href="/api/designs/${encodeURIComponent(record.id)}/schematic.svg" target="_blank"><b>Circuit schematic</b><span>${esc(detail.schematic_file)}</span></a></div>` : "";
    const metricTable = metrics.length ? `<div class="metric-record"><h3>Implementation metrics</h3><div class="kv-grid">${metrics.map(metric => `<div class="kv"><span>${esc(metric.name)}</span><b>${esc(metric.value)} ${esc(metric.unit || "")}</b></div>`).join("")}</div></div>` : "";
    $("#resultDetail").innerHTML = `<div class="project-detail-head"><span class="availability">${esc(record.record_type)} · ${esc(record.status)}</span><h2>${esc(resultDisplayName(record))} · ${esc(record.name)}</h2><p>${ui("Readable project label; the authoritative identifier remains in the expandable raw record.", "此处使用可读项目名称；权威标识保留在下方可展开的原始记录中。")}</p></div><div class="project-overview"><div>${visual}</div><div class="kv-grid"><div class="kv"><span>Project type</span><b>${esc(record.project_type)}</b></div><div class="kv"><span>Module / design</span><b>${esc(detail.module || task.design_id || record.name)}</b></div><div class="kv"><span>Gate instances</span><b>${esc(analysis.instance_count ?? "Available in implementation reports")}</b></div><div class="kv"><span>Runtime plugin</span><b>${esc(task.plugin_id || "Frontend design service")}</b></div><div class="kv"><span>Runtime stages</span><b>${esc((detail.stages || []).length || "Frontend only")}</b></div><div class="kv"><span>Artifacts</span><b>${esc(artifacts.length || (record.record_type === "design" ? "RTL, netlist, schematic" : "Pending"))}</b></div><div class="kv"><span>Replay context</span><b>${record.replayable ? "Registered" : "Pending / not applicable"}</b></div></div></div>${designArtifacts}${artifacts.length ? `<div class="artifact-grid">${artifacts.map(artifact => `<a class="artifact-link" href="${esc(artifact.url)}" target="_blank" rel="noopener"><b>${esc(artifact.kind)}</b><span>${esc(artifact.store_key)} · ${esc((artifact.sha256 || "").slice(0, 10))}…</span></a>`).join("")}</div>` : ""}${metricTable}${learningAction}<details class="raw-record"><summary>Authoritative project record</summary><pre class="detail-code">${esc(JSON.stringify(detail, null, 2))}</pre></details>`;
    if ($("#collectLearning")) $("#collectLearning").addEventListener("click", () => collectLearning(record.id, task));
    $("#projectDetailSection").classList.add("open");
    $("#projectDetailSection").scrollIntoView({behavior: "smooth", block: "start"});
  } catch (error) {
    $("#resultDetail").innerHTML = `<div class="empty-row">${esc(error.message)}</div>`;
  }
}

async function collectLearning(runId, task) {
  const button = $("#collectLearning");
  button.disabled = true;
  message("#collectLearningMessage", ui("Collecting verified metrics with provenance…", "正在连同来源信息收集验证指标……"));
  try {
    const receipt = await post(`/api/runtime/runs/${encodeURIComponent(runId)}/collect-learning`, {
      project_id: task.project_id || "openroad-platform",
      pdk_id: task.parameters?.platform || "registered-platform",
      toolchain_id: `${task.plugin_id || "design"}-${task.plugin_version || "registered"}`,
      metric_parser_version: "web-evidence-v1",
    });
    message("#collectLearningMessage", ui(`Verified experience collected · ${receipt.status || "recorded"}.`, `验证经验已收集 · ${receipt.status || "已登记"}。`));
  } catch (error) {
    message("#collectLearningMessage", error.message, true);
  } finally { button.disabled = false; }
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
  message("#evolutionActionMessage", action === "accepted" ? ui("Creating a reviewed experiment plan…", "正在创建已审查的实验计划……") : ui("Recording rejection…", "正在记录拒绝决定……"));
  try {
    const result = await post(`/api/recommendations/${encodeURIComponent(id)}/decision`, {action, create_campaign: action === "accepted", submit: false});
    await loadEvolution();
    if (result.campaign_created) {
      $("#evolutionActionMessage").innerHTML = `${ui("The experiment plan is ready and has not started.", "实验计划已经就绪，尚未开始执行。")} <button class="button small" id="submitApprovedCampaign">${ui("Confirm execution", "确认执行")}</button>`;
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
  message("#evolutionActionMessage", ui("Starting the approved experiment…", "正在启动已批准的实验……"));
  try {
    const result = await post(`/api/recommendations/${encodeURIComponent(id)}/decision`, {action: "accepted", create_campaign: true, submit: true});
    message("#evolutionActionMessage", ui(`${result.run_ids.length} design task started.`, `已启动 ${result.run_ids.length} 个设计任务。`));
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
$("#accountButton").addEventListener("click", () => {
  if (state.auth?.user?.username === "local-user") {
    message("#demoNotice", ui("Internal mode: this deployment shares one local workspace without registration.", "内部模式：当前部署为共享本地工作区，无需注册登录。"));
    return;
  }
  if (state.auth?.authenticated) {
    if (window.confirm(ui("Sign out of this workspace?", "确认退出当前工作区？"))) logout();
  } else {
    openAuth();
  }
});
$("#authClose").addEventListener("click", closeAuth);
$("#authLogin").addEventListener("click", () => submitAuth("login"));
$("#authRegister").addEventListener("click", () => submitAuth("register"));
$("#authPassword").addEventListener("keydown", event => { if (event.key === "Enter") submitAuth("login"); });
$("#apiAuthAction").addEventListener("click", () => {
  if (!state.auth?.authenticated) return openAuth(ui("Sign in first, then configure your model provider.", "请先登录，再配置模型 Provider。"));
  route("frontend");
  setTimeout(() => $("#providerConfiguration")?.scrollIntoView({behavior: "smooth", block: "center"}), 100);
});
$("#exampleSelect").addEventListener("change", updateExampleDescription);
$("#useExample").addEventListener("click", useExample);
$("#rtlFile").addEventListener("change", event => { const file = event.target.files?.[0]; if (!file) return; $("#rtlFilename").value = file.name; const reader = new FileReader(); reader.onload = () => { $("#rtlSource").value = String(reader.result || ""); }; reader.readAsText(file); });
$("#frontendDesign").addEventListener("change", event => selectDesign(event.target.value));
$("#backendDesign").addEventListener("change", event => selectDesign(event.target.value));
$("#importRtl").addEventListener("click", importRtl);
$("#createSpec").addEventListener("click", createSpec);
$("#continueSpec").addEventListener("click", continueSpec);
$("#approveSpecRtl").addEventListener("click", approveSpecRtl);
$("#saveProvider").addEventListener("click", saveProvider);
$("#rtlscoutMode").addEventListener("change", updateRtlscoutControls);
$("#rtlscoutBenchmark").addEventListener("change", updateRtlscoutControls);
$("#rtlscoutCost").addEventListener("change", updateRtlscoutControls);
$("#rtlscoutSteps").addEventListener("input", updateRtlscoutControls);
$("#runRtlscout").addEventListener("click", submitRtlscout);
$("#runSelect").addEventListener("change", event => selectRun(event.target.value));
$("#submitFlow").addEventListener("click", submitFlow);
$("#approveBatchPlan").addEventListener("click", approveBatchPlan);
$("#flowMode").addEventListener("change", updateFlowMode);
$$('[data-locale]').forEach(button => button.addEventListener("click", () => { applyLocale(button.dataset.locale); updateFlowMode(); if (!state.selectedRun) renderStageRail(new Map()); }));
$("#refreshResults").addEventListener("click", loadResults);
$("#developerScope").addEventListener("click", async () => {
  state.developerView = !state.developerView;
  $("#developerScope").textContent = state.developerView ? ui("Show only my work", "只看我的记录") : ui("Show all users", "查看所有用户");
  await loadResults();
});
$("#closeResultDetail").addEventListener("click", () => { $("#projectDetailSection").classList.remove("open"); $("#resultList").scrollIntoView({behavior: "smooth"}); });
$$('#resultFilters button').forEach(button => button.addEventListener("click", () => { $$('#resultFilters button').forEach(item => item.classList.remove("active")); button.classList.add("active"); state.resultFilter = button.dataset.filter; renderResults(); }));

let initialLocale = "en";
try { initialLocale = new URLSearchParams(location.search).get("lang") || localStorage.getItem("openroad-platform-locale") || "en"; } catch (_) { /* storage may be disabled */ }
applyLocale(initialLocale);
updateFlowMode();
(async () => {
  await Promise.all([loadPlatform(), loadAuth()]);
  route(location.hash.slice(1) || "overview");
  if (state.auth?.authenticated) await loadAuthenticatedWorkspace();
})();
