const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const stageOrder = ["synth", "floorplan", "place", "cts", "route", "finish"];
const stageLabels = {synth:"综合",floorplan:"布局规划",place:"放置",cts:"时钟树",route:"布线",finish:"GDS 输出"};

const state = {
  health: null,
  projects: [],
  designs: [],
  runs: [],
  runtimeRuns: [],
  campaigns: [],
  selectedDesign: null,
  selectedRun: null,
  selectedThreeD: null,
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  let payload;
  try { payload = await response.json(); } catch { payload = {error: `HTTP ${response.status}`}; }
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function post(path, payload) {
  return api(path, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
}

function navigate(view) {
  $$(".view").forEach((item) => item.classList.toggle("active", item.id === `view-${view}`));
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.nav === view));
  $("#crumb").textContent = ({home:"PROJECT HUB",design:"CIRCUIT STUDIO",flow:"RTL-TO-GDS","three-d":"TAIWEI 3D"})[view] || view;
  history.replaceState(null, "", `#${view}`);
  window.scrollTo({top:0, behavior:"smooth"});
}

function setMessage(selector, message, isError = false) {
  const element = $(selector);
  element.textContent = message;
  element.classList.toggle("error", isError);
}

async function loadHealth() {
  try {
    state.health = await api("/api/health");
    const ready = state.health.execution_ready && state.health.generator_ready && state.health.yosys_ready;
    $("#systemDot").classList.toggle("good", ready);
    $("#systemLabel").textContent = ready ? "系统就绪" : "部分能力不可用";
    $("#systemDetail").textContent = ready ? "LLM / Yosys / OpenROAD / ORFS" : "请查看健康检查接口";
  } catch (error) {
    $("#systemLabel").textContent = "服务不可用";
    $("#systemDetail").textContent = error.message;
  }
}

async function loadProjects() {
  try {
    state.projects = (await api("/api/projects")).projects;
    $("#projectCount").textContent = state.projects.filter((item) => item.status === "available").length;
  } catch { state.projects = []; }
}

async function loadDesigns(preferredId = null) {
  state.designs = (await api("/api/designs")).designs;
  $("#designCount").textContent = state.designs.length;
  const options = '<option value="">选择设计</option>' + state.designs.map((design) =>
    `<option value="${escapeHtml(design.id)}">${escapeHtml(design.module)} · ${escapeHtml(design.description.slice(0, 28))}</option>`
  ).join("");
  $("#designSelect").innerHTML = options;
  $("#flowDesign").innerHTML = '<option value="">请选择已生成或上传的设计</option>' + state.designs.map((design) =>
    `<option value="${escapeHtml(design.id)}">${escapeHtml(design.module)} · ${escapeHtml(design.origin)}</option>`
  ).join("");
  if (preferredId) {
    $("#designSelect").value = preferredId;
    $("#flowDesign").value = preferredId;
    await selectDesign(preferredId);
  }
}

async function selectDesign(id) {
  if (!id) {
    state.selectedDesign = null;
    $("#designEmpty").hidden = false;
    $("#designResult").hidden = true;
    return;
  }
  const design = await api(`/api/designs/${encodeURIComponent(id)}`);
  state.selectedDesign = design;
  $("#designSelect").value = id;
  $("#designEmpty").hidden = true;
  $("#designResult").hidden = false;
  $("#designOrigin").textContent = design.origin === "natural_language" ? "NATURAL LANGUAGE" : "IMPORTED RTL";
  $("#designName").textContent = design.module;
  $("#designDescription").textContent = design.description;
  const analysis = design.analysis || {};
  const cellTypes = analysis.cell_types || {};
  $("#designKpis").innerHTML = [
    [analysis.instance_count ?? 0, "Instances"],
    [Object.keys(cellTypes).length, "Cell types"],
    [analysis.dff_count ?? 0, "Registers"],
    [analysis.max_combinational_depth ?? 0, "Logic depth"],
  ].map(([value,label]) => `<div class="kpi"><b>${escapeHtml(value)}</b><small>${label}</small></div>`).join("");
  $("#schematicImage").src = `/api/designs/${encodeURIComponent(id)}/schematic.svg?t=${Date.now()}`;
  $("#rtlCode").textContent = design.rtl_source || "";
  $("#netlistCode").textContent = design.netlist_source || "";
  $("#designAnalysis").innerHTML = [
    ["顶层模块", analysis.module],
    ["输入端口", (analysis.inputs || []).join(", ") || "--"],
    ["输出端口", (analysis.outputs || []).join(", ") || "--"],
    ["门类型分布", Object.entries(cellTypes).map(([key,value]) => `${key}: ${value}`).join(" · ") || "--"],
    ["最大组合深度", analysis.max_combinational_depth ?? 0],
    ["最长路径", analysis.max_depth_path ? `${analysis.max_depth_path.source} → ${analysis.max_depth_path.destination}` : "无可达组合路径"],
  ].map(([label,value]) => `<div class="analysis-item"><small>${label}</small><b>${escapeHtml(value)}</b></div>`).join("");
  $("#flowDesign").value = id;
  $("#flowTop").value = design.module;
}

async function generateDesign() {
  const description = $("#designPrompt").value.trim();
  if (!description) { setMessage("#designMessage", "请先描述需要生成的电路。", true); return; }
  const button = $("#generateDesign");
  button.disabled = true;
  button.firstChild.textContent = "正在调用 LLM、综合并分析... ";
  setMessage("#designMessage", "生成过程中会自动执行 Yosys，并在失败时让模型修正 RTL。", false);
  try {
    const design = await post("/api/designs/generate", {description});
    await loadDesigns(design.id);
    setMessage("#designMessage", `已生成 ${design.module}，网表、电路图和分析均已就绪。`, false);
  } catch (error) {
    setMessage("#designMessage", error.message, true);
  } finally {
    button.disabled = false;
    button.firstChild.textContent = "生成电路与网表 ";
  }
}

async function importDesign(file) {
  if (!file || !/\.(v|sv)$/i.test(file.name)) { setMessage("#designMessage", "请选择 .v 或 .sv 文件。", true); return; }
  setMessage("#designMessage", "正在综合上传的 RTL 并生成分析证据...", false);
  try {
    const design = await post("/api/designs/import", {filename:file.name, rtl_source:await file.text()});
    await loadDesigns(design.id);
    setMessage("#designMessage", `已导入并完成分析：${design.module}`, false);
  } catch (error) { setMessage("#designMessage", error.message, true); }
}

function statusLabel(status) {
  return ({queued:"排队中",preparing:"准备中",running:"执行中",cancel_requested:"取消中",cancelled:"已取消",succeeded:"已完成",failed:"未通过"})[status] || status;
}

async function loadRuns(preferredId = null) {
  state.runs = (await api("/api/runs")).runs;
  $("#runCount").textContent = state.runs.length;
  $("#runSelect").innerHTML = '<option value="">选择运行记录</option>' + state.runs.map((run) => {
    const name = run.request.labels?.design_id
      ? state.designs.find((item) => item.id === run.request.labels.design_id)?.module
      : run.request.top;
    return `<option value="${escapeHtml(run.id)}">${escapeHtml(name || run.request.top || "design")} · ${statusLabel(run.status)} · ${run.request.target_stage}</option>`;
  }).join("");
  const selected = preferredId || state.selectedRun?.id;
  if (selected && state.runs.some((run) => run.id === selected)) {
    $("#runSelect").value = selected;
    await selectRun(selected);
  } else if (state.runs.length && !state.selectedRun) {
    $("#runSelect").value = state.runs[0].id;
    await selectRun(state.runs[0].id);
  }
}

async function startFlow() {
  const designId = $("#flowDesign").value;
  if (!designId) { setMessage("#flowMessage", "请先选择一个设计。", true); return; }
  const button = $("#startFlow");
  button.disabled = true;
  setMessage("#flowMessage", "正在写入持久化队列...", false);
  try {
    const run = await post("/api/runs/from-design", {
      design_id: designId,
      top: $("#flowTop").value.trim() || null,
      clock: $("#flowClock").value.trim() || null,
      clock_period_ns: Number($("#flowPeriod").value),
      core_utilization_pct: Number($("#flowUtil").value),
      place_density: Number($("#flowDensity").value),
      target_stage: $("#flowTarget").value,
    });
    setMessage("#flowMessage", `任务 ${run.id.slice(0,12)} 已提交，worker 将独立执行。`, false);
    await loadRuns(run.id);
  } catch (error) { setMessage("#flowMessage", error.message, true); }
  finally { button.disabled = false; }
}

async function selectRun(id) {
  if (!id) return;
  const run = await api(`/api/runs/${encodeURIComponent(id)}`);
  state.selectedRun = run;
  $("#runSelect").value = id;
  const design = state.designs.find((item) => item.id === run.request.labels?.design_id);
  const result = run.result || {};
  $("#flowSummary").innerHTML = `<span class="run-status ${escapeHtml(run.status)}">${escapeHtml(run.status.toUpperCase())}</span><div><b>${escapeHtml(design?.module || run.request.top || "Design")} · ${statusLabel(run.status)}</b><small>${escapeHtml(run.id)} · target ${escapeHtml(run.request.target_stage)}</small></div>`;
  const stages = new Map((result.stages || []).map((item) => [item.stage,item]));
  $("#stageRail").innerHTML = stageOrder.map((name,index) => {
    const stage = stages.get(name);
    const className = stage?.status === "succeeded" ? "done" : stage?.status === "failed" ? "failed" : "";
    return `<div class="flow-stage ${className}"><span>0${index+1}</span><b>${name}</b><small>${stage ? `${statusLabel(stage.status)} · ${Number(stage.seconds).toFixed(1)}s` : stageLabels[name]}</small></div>`;
  }).join("");
  renderPhysicalEvidence(run);
}

function renderPhysicalEvidence(run) {
  const result = run.result;
  if (!result) {
    $("#physicalEvidence").innerHTML = '<div class="empty-state"><b>任务正在等待或执行</b><p>页面每 5 秒自动读取一次持久化状态。</p></div>';
    return;
  }
  const milestones = result.milestones || {};
  const milestoneLabels = {synthesizable:"可综合",functionally_verified:"功能已验证",implementation_valid:"实现有效",gds_complete:"GDS 完成"};
  const metrics = result.metrics || [];
  const report = run.analysis_report || {};
  const diagnosis = report.diagnosis || {};
  $("#physicalEvidence").innerHTML = `
    <div class="milestone-row">${Object.entries(milestoneLabels).map(([key,label]) => `<div class="milestone"><small>${label}</small><b class="${milestones[key] ? "pass" : ""}">${milestones[key] ? "PASS" : "NOT PROVEN"}</b></div>`).join("")}</div>
    ${result.error ? `<p class="action-message error">${escapeHtml(result.error)}</p>` : ""}
    ${diagnosis.summary ? `<div class="analysis-item" style="margin-top:14px"><small>物理分析结论</small><b>${escapeHtml(diagnosis.summary)}</b></div>` : ""}
    <table class="metrics-table">${metrics.slice(0,12).map((metric) => `<tr><td>${escapeHtml(metric.name)}</td><td>${escapeHtml(metric.value)} ${escapeHtml(metric.unit || "")}</td></tr>`).join("")}</table>
    <div class="artifact-list">${(result.artifacts || []).map((artifact) => `<div class="artifact"><span>${escapeHtml(artifact.kind)}</span><span>${escapeHtml(artifact.path)}</span><span>${(artifact.size_bytes/1024).toFixed(1)} KiB</span></div>`).join("")}</div>`;
}

async function loadThreeDRuns(preferredId = null) {
  const payload = await api("/api/runtime/runs");
  state.runtimeRuns = payload.runs.filter((run) => run.plugin_id === "taiwei-pin-3d");
  try { state.campaigns = (await api("/api/campaigns")).campaigns; } catch { state.campaigns = []; }
  $("#threeDRunSelect").innerHTML = '<option value="">选择 3D 运行</option>' + state.runtimeRuns.map((run) =>
    `<option value="${escapeHtml(run.run_id)}">gcd · ${statusLabel(run.status)} · ${escapeHtml(run.run_id.slice(0,12))}</option>`
  ).join("");
  const selected = preferredId || state.selectedThreeD?.run?.run_id || state.runtimeRuns[0]?.run_id;
  if (selected && state.runtimeRuns.some((run) => run.run_id === selected)) {
    $("#threeDRunSelect").value = selected;
    await selectThreeDRun(selected);
  }
}

async function selectThreeDRun(runId) {
  if (!runId) return;
  const detail = await api(`/api/runtime/runs/${encodeURIComponent(runId)}`);
  state.selectedThreeD = detail;
  const run = detail.run;
  const data = detail.three_d || {tiers:{},metrics:{},toolchain:{},artifacts:[],replayable:false};
  $("#threeDSummary").innerHTML = `<span class="run-status ${escapeHtml(run.status)}">${escapeHtml(run.status.toUpperCase())}</span><div><b>gcd · ${statusLabel(run.status)}</b><small>${escapeHtml(run.run_id)} · ${data.replayable ? "可完整重放" : "证据生成中"}</small></div>`;
  $("#cancelThreeD").disabled = !["queued","running","cancel_requested"].includes(run.status);
  const tiers = data.tiers || {};
  const hbViaCount = metricValue(data.metrics, "finish__route__hb_via__count__phys");
  $("#tierKpis").innerHTML = [
    [tiers.upper_instances ?? "--", "Upper tier"],
    [tiers.bottom_instances ?? "--", "Bottom tier"],
    [hbViaCount !== "--" ? hbViaCount : (tiers.hbt_named_instances ?? "--"), "HB via (physical)"],
    [metricValue(data.metrics, "finish__route__cross_tier_nets__all"), "Cross-tier nets"],
  ].map(([value,label]) => `<div class="kpi"><b>${escapeHtml(value ?? "--")}</b><small>${escapeHtml(label)}</small></div>`).join("");
  const view = data.artifacts.find((artifact) => artifact.kind === "three_d_view");
  $("#threeDView").innerHTML = view
    ? `<img src="${escapeHtml(view.url)}" alt="TaiWei gcd 上下层最终布局视图">`
    : '<div class="empty-state"><b>3D 视图生成中</b><p>完成 final DEF 后自动出现。</p></div>';
  const important = Object.entries(data.metrics || {}).filter(([name]) =>
    /hb_via|cross_tier|timing__setup|power__total|instance__area|drc_errors/.test(name)
  );
  $("#threeDMetrics").innerHTML = important.map(([name,record]) =>
    `<tr><td>${escapeHtml(name)}</td><td>${escapeHtml(record?.value ?? record)}</td></tr>`
  ).join("") || '<tr><td>指标尚未登记</td><td>--</td></tr>';
  const tool = data.toolchain || {};
  $("#threeDToolchain").innerHTML = [
    ["OpenROAD", tool.openroad_commit || "--"], ["Yosys", tool.yosys_version || "--"],
    ["ORFS-Research", tool.orfs_commit || "--"], ["TaiWei", tool.taiwei_commit || "--"],
  ].map(([label,value]) => `<div><small>${label}</small><code>${escapeHtml(value)}</code></div>`).join("");
  $("#threeDArtifacts").innerHTML = data.artifacts.map((artifact) =>
    `<a class="artifact" href="${escapeHtml(artifact.url)}" target="_blank" rel="noopener"><span>${escapeHtml(artifact.kind)}</span><span>${escapeHtml(artifact.store_key)}</span><span>${((artifact.size_bytes || 0)/1024).toFixed(1)} KiB · ${escapeHtml((artifact.sha256 || "").slice(0,10))}</span></a>`
  ).join("");
  const campaigns = state.campaigns.filter((campaign) => (campaign.members || []).some((member) => member.run_id === runId));
  $("#threeDCampaigns").innerHTML = campaigns.length
    ? campaigns.map((campaign) => `<div><small>CAMPAIGN</small><b>${escapeHtml(campaign.name || campaign.campaign_id)}</b><span>${campaign.members.length} members</span></div>`).join("")
    : '<div><small>CAMPAIGN</small><b>独立验收运行</b><span>可由 Campaign 成员引用同一 Runtime run</span></div>';
}

function metricValue(metrics, name) {
  const record = (metrics || {})[name];
  return record && typeof record === "object" ? record.value : (record ?? "--");
}

async function cancelThreeDRun() {
  const runId = state.selectedThreeD?.run?.run_id;
  if (!runId) return;
  await post(`/api/runtime/runs/${encodeURIComponent(runId)}/cancel`, {});
  await loadThreeDRuns(runId);
}

$$('[data-nav]').forEach((button) => button.addEventListener("click", (event) => { event.preventDefault(); navigate(button.dataset.nav); }));
$$('[data-prompt]').forEach((button) => button.addEventListener("click", () => { $("#designPrompt").value = button.dataset.prompt; }));
$$('[data-evidence]').forEach((button) => button.addEventListener("click", () => {
  $$('[data-evidence]').forEach((item) => item.classList.toggle("active", item === button));
  $$(".evidence-pane").forEach((pane) => pane.classList.toggle("active", pane.id === `evidence-${button.dataset.evidence}`));
}));
$("#generateDesign").addEventListener("click", generateDesign);
$("#designFile").addEventListener("change", (event) => importDesign(event.target.files[0]));
$("#designSelect").addEventListener("change", (event) => selectDesign(event.target.value));
$("#flowDesign").addEventListener("change", (event) => {
  const design = state.designs.find((item) => item.id === event.target.value);
  $("#flowTop").value = design?.module || "";
});
$("#useInFlow").addEventListener("click", () => { if (state.selectedDesign) { $("#flowDesign").value = state.selectedDesign.id; $("#flowTop").value = state.selectedDesign.module; navigate("flow"); } });
$("#startFlow").addEventListener("click", startFlow);
$("#runSelect").addEventListener("change", (event) => selectRun(event.target.value));
$("#threeDRunSelect").addEventListener("change", (event) => selectThreeDRun(event.target.value));
$("#cancelThreeD").addEventListener("click", () => cancelThreeDRun().catch(() => {}));
$("#refreshAll").addEventListener("click", () => refreshAll());

async function refreshAll() {
  await Promise.all([loadHealth(), loadProjects(), loadDesigns()]);
  await Promise.all([loadRuns(), loadThreeDRuns()]);
}

function updateClock() { $("#clock").textContent = new Date().toLocaleString("zh-CN", {hour12:false}); }
updateClock();
setInterval(updateClock, 1000);

const initialView = ["home","design","flow","three-d"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "home";
navigate(initialView);
refreshAll().catch((error) => { $("#systemLabel").textContent = "加载失败"; $("#systemDetail").textContent = error.message; });
setInterval(() => loadRuns().catch(() => {}), 5000);
setInterval(() => loadThreeDRuns().catch(() => {}), 5000);
