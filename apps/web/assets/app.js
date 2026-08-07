"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = {
  platform: null, designs: [], examples: [], runs: [], results: [],
  selectedDesign: null, selectedRun: null, designView: "schematic",
  resultFilter: "all", extensions: [], selectedExtension: null,
};
const stages = ["synth", "floorplan", "place", "cts", "route", "finish"];

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
    $("#healthText").textContent = health.execution_ready ? "Execution ready" : "Console ready";
    renderExtensions();
  } catch (error) {
    $("#healthDot").className = "bad";
    $("#healthText").textContent = "API unavailable";
    $("#extensionCatalog").innerHTML = `<div class="empty-row">${esc(error.message)}</div>`;
  }
}

async function loadExamples() {
  try {
    state.examples = (await api("/api/designs/examples")).examples || [];
    $("#exampleSelect").innerHTML = state.examples.map(example => `<option value="${esc(example.id)}">${esc(example.level === "advanced" ? "Advanced" : "Starter")} · ${esc(example.name)}</option>`).join("");
    updateExampleDescription();
  } catch (error) {
    $("#exampleSelect").innerHTML = '<option value="">Examples unavailable</option>';
    message("#specMessage", error.message, true);
  }
}

function updateExampleDescription() {
  const example = state.examples.find(item => item.id === $("#exampleSelect").value);
  $("#exampleDescription").textContent = example?.description || "";
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
    const options = '<option value="">Select a registered design</option>' + state.designs.map(design => `<option value="${esc(design.id)}">${esc(design.module)} · ${esc(design.id.slice(-8))}</option>`).join("");
    $("#frontendDesign").innerHTML = options;
    $("#backendDesign").innerHTML = options;
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

async function selectDesign(id) {
  if (!id) return;
  const design = await api(`/api/designs/${encodeURIComponent(id)}`);
  state.selectedDesign = design;
  $("#frontendDesign").value = id;
  $("#backendDesign").value = id;
  const analysis = design.analysis || {};
  const ports = (analysis.inputs || []).length + (analysis.outputs || []).length;
  $("#designMeta").innerHTML = `<div><b>${esc(design.module)}</b><span>${esc(design.description)} · ${esc(design.origin)}</span></div><div class="metric"><strong>${esc(analysis.instance_count ?? "—")}</strong><small>Gate instances</small></div><div class="metric"><strong>${esc(ports)}</strong><small>Ports</small></div>`;
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
    canvas.innerHTML = `<pre>${esc(text)}</pre>`;
  }
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
  if (!key) return message("#specMessage", "Enter an API key for this browser session.", true);
  try {
    const result = await post("/api/providers", {owner_id: "local-user", session_id: `web-${Date.now()}`, profile_id: `web-provider-${Date.now()}`, base_url: $("#providerUrl").value, model: $("#providerModel").value, api_key: key});
    $("#providerKey").value = "";
    message("#specMessage", `Provider ${result.profile_id} saved; the key was not persisted.`);
  } catch (error) {
    message("#specMessage", error.message, true);
  }
}

async function loadRuns(preferred = null) {
  try {
    state.runs = (await api("/api/runtime/runs")).runs || [];
    $("#runSelect").innerHTML = '<option value="">Choose a Runtime run</option>' + state.runs.map(run => `<option value="${esc(run.run_id)}">${esc(run.design_id)} · ${esc(run.plugin_id)} · ${esc(run.status)}</option>`).join("");
    const id = preferred || state.selectedRun?.run?.run_id || state.runs[0]?.run_id;
    if (id) {
      $("#runSelect").value = id;
      await selectRun(id);
    } else renderStageRail(new Map());
    renderDplevolveDashboard();
  } catch (error) {
    message("#flowMessage", error.message, true);
  }
}

function renderStageRail(values) {
  $("#stageRail").innerHTML = stages.map((name, index) => {
    const value = values.get(name);
    const css = value?.status === "succeeded" ? "done" : value?.status === "failed" ? "failed" : "";
    return `<div class="stage ${css}"><i></i><b>0${index + 1} · ${name}</b><small>${esc(value?.status || "waiting")}${value?.seconds ? ` · ${Number(value.seconds).toFixed(1)}s` : ""}</small></div>`;
  }).join("");
}

async function selectRun(id) {
  if (!id) return;
  const detail = await api(`/api/runtime/runs/${encodeURIComponent(id)}`);
  state.selectedRun = detail;
  const run = detail.run;
  const task = run.task_spec || {};
  $("#runHeading").innerHTML = `<div><b>${esc(task.design_id)} · ${esc(task.plugin_id)}</b><span>${esc(run.run_id)} · ${esc(task.parameters?.target_stage || "extension task")}</span></div><span class="status ${esc(run.status)}">${esc(run.status)}</span>`;
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
    $("#backendEvidence").innerHTML = '<div class="empty"><span>⋯</span><h3>Waiting for a Runtime worker.</h3><p>The queued task and its recovery state are already recorded.</p></div>';
    return;
  }
  const artifacts = attempt.artifacts || [];
  const views = artifacts.filter(artifact => ["layout_view", "three_d_view"].includes(artifact.kind));
  $("#backendEvidence").innerHTML = `<div class="layout-gallery">${views.map(view => `<figure class="layout-figure"><img src="${esc(view.url)}" alt="Registered layout view"><figcaption>${esc(view.store_key)} · SHA-256 ${esc((view.sha256 || "").slice(0, 12))}…</figcaption></figure>`).join("")}</div><div class="artifact-grid">${artifacts.map(artifact => `<a class="artifact-link" href="${esc(artifact.url)}" target="_blank" rel="noopener"><b>${esc(artifact.kind)}</b><span>${esc(artifact.store_key)} · ${esc((artifact.sha256 || "").slice(0, 10))}…</span></a>`).join("")}</div>${attempt.failure ? `<div class="message error">${esc(attempt.failure.message || attempt.failure.category)}</div>` : ""}`;
}

async function submitFlow() {
  const id = $("#backendDesign").value;
  if (!id) return message("#flowMessage", "Select a registered design first.", true);
  const button = $("#submitFlow");
  button.disabled = true;
  message("#flowMessage", "Submitting a recoverable Runtime task…");
  try {
    const detail = await post("/api/runtime/runs/from-design", {design_id: id, clock: $("#flowClock").value.trim() || null, clock_period_ns: Number($("#flowPeriod").value), core_utilization_pct: Number($("#flowUtil").value), place_density: Number($("#flowDensity").value), target_stage: $("#flowTarget").value});
    message("#flowMessage", `Run ${detail.run.run_id.slice(0, 12)} is queued.`);
    await loadRuns(detail.run.run_id);
  } catch (error) {
    message("#flowMessage", error.message, true);
  } finally {
    button.disabled = false;
  }
}

function renderExtensions() {
  if (!$("#extensionCatalog") || !state.extensions.length) return;
  $("#extensionCatalog").innerHTML = state.extensions.map(extensionCard).join("");
  $$('[data-extension-id]').forEach(button => button.addEventListener("click", () => selectExtension(button.dataset.extensionId)));
  if (state.selectedExtension) $$('[data-extension-id]').forEach(button => button.classList.toggle("active", button.dataset.extensionId === state.selectedExtension));
}

function openExtension(id) {
  route("extensions");
  selectExtension(id);
}

function selectExtension(id) {
  const extension = state.extensions.find(item => item.id === id);
  if (!extension) return;
  state.selectedExtension = id;
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
$("#runSelect").addEventListener("change", event => selectRun(event.target.value));
$("#submitFlow").addEventListener("click", submitFlow);
$("#refreshResults").addEventListener("click", loadResults);
$("#closeResultDetail").addEventListener("click", () => { $("#projectDetailSection").classList.remove("open"); $("#resultList").scrollIntoView({behavior: "smooth"}); });
$$('#resultFilters button').forEach(button => button.addEventListener("click", () => { $$('#resultFilters button').forEach(item => item.classList.remove("active")); button.classList.add("active"); state.resultFilter = button.dataset.filter; renderResults(); }));

route(location.hash.slice(1) || "overview");
Promise.all([loadPlatform(), loadExamples(), loadDesigns(), loadRuns()]);
