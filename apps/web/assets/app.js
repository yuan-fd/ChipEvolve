"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = {platform:null, designs:[], runs:[], results:[], selectedDesign:null, selectedRun:null, resultFilter:"all"};
const stages = ["synth","floorplan","place","cts","route","finish"];

function esc(value) { return String(value ?? "").replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c])); }
async function api(path, options={}) {
  const response = await fetch(path, {headers:{"Content-Type":"application/json",...(options.headers||{})},...options});
  const type = response.headers.get("content-type") || "";
  const body = type.includes("json") ? await response.json() : await response.text();
  if (!response.ok) throw new Error(body?.error || body || `Request failed (${response.status})`);
  return body;
}
const post = (path, body) => api(path, {method:"POST",body:JSON.stringify(body)});
function message(selector, value, error=false) { const el=$(selector); el.textContent=value || ""; el.classList.toggle("error", error); }

function route(name) {
  if (!$( `#page-${name}`)) name="overview";
  $$(".page").forEach(page => page.classList.toggle("active", page.id === `page-${name}`));
  $$(".tab").forEach(tab => tab.classList.toggle("active", tab.dataset.route === name));
  history.replaceState(null, "", `#${name}`);
  window.scrollTo({top:0,behavior:"instant"});
  if (name === "projects") loadResults();
  if (name === "evolution") loadEvolution();
}

function extensionCard(item) {
  return `<article class="extension-card"><div class="extension-top"><span class="layer">${esc(item.layer)}</span><span class="pill optional">Optional</span></div><h3>${esc(item.name)}</h3><p>${esc(item.summary)}</p><small>${esc(item.execution_class)} · ${esc(item.smoke_mode)}</small></article>`;
}

async function loadPlatform() {
  try {
    const [platform, health] = await Promise.all([api("/api/platform"),api("/api/health")]);
    state.platform=platform;
    $("#healthDot").className=health.ok ? "ok" : "bad";
    $("#healthText").textContent=health.execution_ready ? "Execution ready" : "Console ready";
    const c=platform.counts;
    $("#countDesigns").textContent=c.designs; $("#countRuns").textContent=c.runtime_runs;
    $("#countCampaigns").textContent=c.campaigns; $("#countKnowledge").textContent=c.knowledge_sources;
    const components=platform.extensions.components || [];
    $("#overviewExtensions").innerHTML=components.map(extensionCard).join("");
    $("#frontendExtensions").innerHTML=components.filter(x=>x.layer==="frontend").map(extensionCard).join("");
  } catch (error) {
    $("#healthDot").className="bad"; $("#healthText").textContent="API unavailable";
    $("#overviewExtensions").innerHTML=`<div class="empty-row">${esc(error.message)}</div>`;
  }
}

async function loadDesigns(preferred=null) {
  try {
    state.designs=(await api("/api/designs")).designs || [];
    const options='<option value="">Select a registered design</option>'+state.designs.map(x=>`<option value="${esc(x.id)}">${esc(x.module)} · ${esc(x.id.slice(-8))}</option>`).join("");
    $("#frontendDesign").innerHTML=options; $("#backendDesign").innerHTML=options;
    const id=preferred || state.selectedDesign?.id || state.designs[0]?.id;
    if (id) { $("#frontendDesign").value=id; $("#backendDesign").value=id; await selectDesign(id); }
  } catch (error) { message("#specMessage",error.message,true); }
}

async function selectDesign(id) {
  if (!id) return;
  const design=await api(`/api/designs/${encodeURIComponent(id)}`);
  state.selectedDesign=design;
  $("#frontendDesign").value=id; $("#backendDesign").value=id;
  $("#designMeta").innerHTML=`<b>${esc(design.module)}</b><span>${esc(design.description)} · ${esc(design.origin)}</span>`;
  await renderDesignView();
}

async function renderDesignView() {
  const design=state.selectedDesign, kind=$("#frontendView").value;
  if (!design) return;
  const canvas=$("#frontendCanvas");
  if (kind==="schematic") canvas.innerHTML=`<img src="/api/designs/${encodeURIComponent(design.id)}/schematic.svg" alt="Synthesized circuit schematic">`;
  else if (kind==="analysis") canvas.innerHTML=`<pre>${esc(JSON.stringify(design.analysis || {},null,2))}</pre>`;
  else { const text=await api(`/api/designs/${encodeURIComponent(design.id)}/source?kind=${kind}`); canvas.innerHTML=`<pre>${esc(text)}</pre>`; }
}

async function importRtl() {
  const button=$("#importRtl"); button.disabled=true; message("#specMessage","Synthesizing the imported RTL…");
  try {
    const design=await post("/api/designs/import",{filename:$("#rtlFilename").value.trim(),rtl_source:$("#rtlSource").value,description:"Imported from the web workspace"});
    message("#specMessage",`Registered ${design.module}.`); await loadDesigns(design.id);
  } catch(error){message("#specMessage",error.message,true)} finally{button.disabled=false}
}

async function createSpec() {
  const prompt=$("#specPrompt").value.trim(); if(!prompt) return message("#specMessage","Enter a circuit specification first.",true);
  const button=$("#createSpec"); button.disabled=true; message("#specMessage","Building a review session…");
  try {
    const payload={message:prompt,provider:"deterministic"}; if(state.selectedDesign) payload.design_id=state.selectedDesign.id;
    const result=await post("/api/spec/sessions",payload);
    message("#specMessage",`Session ${result.session_id.slice(0,12)} is ${result.status}. Review and confirmation remain separate.`);
  } catch(error){message("#specMessage",error.message,true)} finally{button.disabled=false}
}

async function saveProvider() {
  const key=$("#providerKey").value; if(!key) return message("#specMessage","Enter an API key for this session.",true);
  try {
    const result=await post("/api/providers",{owner_id:"local-user",session_id:`web-${Date.now()}`,profile_id:`web-provider-${Date.now()}`,base_url:$("#providerUrl").value,model:$("#providerModel").value,api_key:key});
    $("#providerKey").value=""; message("#specMessage",`Provider ${result.profile_id} saved; the key was not persisted.`);
  } catch(error){message("#specMessage",error.message,true)}
}

async function loadRuns(preferred=null) {
  try {
    state.runs=(await api("/api/runtime/runs")).runs || [];
    $("#runSelect").innerHTML='<option value="">Choose a Runtime run</option>'+state.runs.map(x=>`<option value="${esc(x.run_id)}">${esc(x.design_id)} · ${esc(x.plugin_id)} · ${esc(x.status)}</option>`).join("");
    const id=preferred || state.selectedRun?.run?.run_id || state.runs[0]?.run_id;
    if(id){$("#runSelect").value=id; await selectRun(id)} else renderStageRail(new Map());
  } catch(error){message("#flowMessage",error.message,true)}
}

function renderStageRail(values) {
  $("#stageRail").innerHTML=stages.map((name,index)=>{const v=values.get(name);const cls=v?.status==="succeeded"?"done":v?.status==="failed"?"failed":"";return `<div class="stage ${cls}"><i></i><b>0${index+1} · ${name}</b><small>${esc(v?.status || "waiting")}${v?.seconds?` · ${Number(v.seconds).toFixed(1)}s`:""}</small></div>`}).join("");
}

async function selectRun(id) {
  if(!id)return; const detail=await api(`/api/runtime/runs/${encodeURIComponent(id)}`); state.selectedRun=detail;
  const run=detail.run, task=run.task_spec || {};
  $("#runHeading").innerHTML=`<div><b>${esc(task.design_id)} · ${esc(task.plugin_id)}</b><span>${esc(run.run_id)} · ${esc(task.parameters?.target_stage || "extension task")}</span></div><span class="status ${esc(run.status)}">${esc(run.status)}</span>`;
  const values=new Map(); (detail.events||[]).forEach(e=>{const n=e.payload?.tool_stage;if(e.event_type==="tool.stage.started"&&n)values.set(n,{status:"running"});if(e.event_type==="tool.stage.finished"&&n)values.set(n,{status:e.payload.status,seconds:e.payload.seconds})}); renderStageRail(values);
  const attempts=(detail.stages||[]).flatMap(s=>s.attempts||[]), attempt=attempts.at(-1);
  if(!attempt){$("#backendEvidence").innerHTML='<div class="empty"><span>⋯</span><h3>Waiting for a Runtime worker.</h3><p>The durable queued state is already recorded.</p></div>';return}
  const artifacts=attempt.artifacts||[], views=artifacts.filter(a=>["layout_view","three_d_view"].includes(a.kind));
  $("#backendEvidence").innerHTML=`${views.map(v=>`<figure class="layout-figure"><img src="${esc(v.url)}" alt="Registered layout view"><figcaption>${esc(v.store_key)} · SHA-256 ${esc((v.sha256||"").slice(0,12))}…</figcaption></figure>`).join("")}<div class="artifact-grid">${artifacts.map(a=>`<a class="artifact-link" href="${esc(a.url)}" target="_blank" rel="noopener"><b>${esc(a.kind)}</b><span>${esc(a.store_key)} · ${esc((a.sha256||"").slice(0,10))}…</span></a>`).join("")}</div>${attempt.failure?`<div class="message error">${esc(attempt.failure.message||attempt.failure.category)}</div>`:""}`;
}

async function submitFlow() {
  const id=$("#backendDesign").value; if(!id)return message("#flowMessage","Select a registered design first.",true);
  const button=$("#submitFlow");button.disabled=true;message("#flowMessage","Submitting an immutable Runtime task…");
  try { const detail=await post("/api/runtime/runs/from-design",{design_id:id,clock:$("#flowClock").value.trim()||null,clock_period_ns:Number($("#flowPeriod").value),core_utilization_pct:Number($("#flowUtil").value),place_density:Number($("#flowDensity").value),target_stage:$("#flowTarget").value});message("#flowMessage",`Run ${detail.run.run_id.slice(0,12)} is queued.`);await loadRuns(detail.run.run_id)}catch(error){message("#flowMessage",error.message,true)}finally{button.disabled=false}
}

async function loadResults() {
  try { const payload=await api("/api/platform/results");state.results=payload.records||[];renderResults() } catch(error){$("#resultList").innerHTML=`<div class="empty-row">${esc(error.message)}</div>`}
}
function renderResults() {
  const records=state.results.filter(x=>state.resultFilter==="all"||x.record_type===state.resultFilter);
  $("#resultList").innerHTML=records.length?records.map(x=>`<button class="result-row" data-result="${esc(x.id)}"><div><small>${esc(x.project_type)} · ${esc(x.status)}</small><b>${esc(x.name)}</b><span>${esc(x.summary)}</span></div><time>${formatDate(x.created_at)}</time></button>`).join(""):'<div class="empty"><span>○</span><h3>No matching records.</h3><p>Results appear only after a design or Runtime task is registered.</p></div>';
  $$("[data-result]").forEach(button=>button.addEventListener("click",()=>selectResult(button.dataset.result)));
}
async function selectResult(id) {
  const record=state.results.find(x=>x.id===id);if(!record)return;
  $$("[data-result]").forEach(x=>x.classList.toggle("active",x.dataset.result===id));
  try { const detail=await api(record.detail_url); const visual=record.visualization_url?`<img src="${esc(record.visualization_url)}" alt="Project visualization">`:""; const run=detail.run||{}; const task=run.task_spec||{}; $("#resultDetail").innerHTML=`<div class="detail-head"><span class="pill ${record.record_type==="design"?"core":"optional"}">${esc(record.record_type)}</span><h2>${esc(record.name)}</h2><p>${esc(record.id)}</p></div><div class="detail-body">${visual}<div class="kv"><span>Status</span><b>${esc(record.status)}</b></div><div class="kv"><span>Type</span><b>${esc(record.project_type)}</b></div>${task.plugin_id?`<div class="kv"><span>Plugin</span><b>${esc(task.plugin_id)}</b></div>`:""}<div class="kv"><span>Replay context</span><b>${record.replayable?"Registered":"Pending / not applicable"}</b></div><details class="inline-details"><summary>Raw authoritative record</summary><pre class="detail-code">${esc(JSON.stringify(detail,null,2))}</pre></details></div>` } catch(error){$("#resultDetail").innerHTML=`<div class="empty-row">${esc(error.message)}</div>`}
}

async function loadEvolution() {
  try {
    const x=await api("/api/platform/evolution"), c=x.counts;
    $("#evoSources").textContent=c.knowledge_sources;$("#evoBenchmarks").textContent=c.benchmarks;$("#evoObservations").textContent=c.observed_samples;$("#evoStudies").textContent=c.optimization_studies;$("#evoRecommendations").textContent=c.recommendations;
    $("#learningLoop").innerHTML=x.learning_loop.map((item,i)=>`<div class="loop-step"><span>0${i+1}</span><p>${esc(item)}</p></div>`).join("");
    $("#knowledgeList").innerHTML=[...x.knowledge_sources,...x.benchmarks].map(item=>`<div class="data-item"><div><b>${esc(item.title||item.source_id||item.benchmark_id)}</b><span>${esc(item.organization||item.version||item.entrypoint)}</span></div><small>${esc(item.content_kind||item.license_id)}</small></div>`).join("")||'<div class="empty-row">No audited public sources are registered.</div>';
    $("#studyList").innerHTML=x.studies.map(item=>`<div class="data-item"><div><b>${esc(item.design_id)}</b><span>${esc(item.observation_count)} observations · ${esc(item.proposal_count)} proposals</span></div><small>${esc(item.status)}</small></div>`).join("")||'<div class="empty-row">No optimization study has been created in this database yet.</div>';
    $("#researchMethodList").innerHTML=(x.research_methods||[]).map(item=>`<div class="data-item"><div><b>${esc(item.title)}</b><span>${esc(item.role)} · ${esc((item.implementation||[]).join(" / "))}</span></div><small>DOI ${esc(item.doi)}</small></div>`).join("")||'<div class="empty-row">No research methods are registered.</div>';
    $("#recommendationList").innerHTML=x.recommendations.map(item=>{const r=item.recommendation||item,c=r.confidence||{};return `<div class="data-item recommendation-item"><div><b>${esc(r.policy_kind||"Optimizer advice")}</b><span>${esc(JSON.stringify(r.parameters||{}))}</span><span>Confidence ${Number(c.overall||0).toFixed(2)} · ${c.ood?"Out of distribution":"In observed support"} · ${esc((c.reasons||[]).join("; "))}</span></div><div class="decision-actions"><small>${esc(r.permission_tier||"T1 advice")}</small><button class="button small" data-recommendation-action="accepted" data-recommendation-id="${esc(r.recommendation_id)}">Approve plan</button><button class="button small" data-recommendation-action="rejected" data-recommendation-id="${esc(r.recommendation_id)}">Reject</button></div></div>`}).join("")||'<div class="empty-row">No user-facing recommendation is recorded yet. Advice will appear with confidence and OOD evidence.</div>';
    $$('[data-recommendation-action]').forEach(button=>button.addEventListener("click",()=>decideRecommendation(button.dataset.recommendationId,button.dataset.recommendationAction)));
  } catch(error){$("#knowledgeList").innerHTML=`<div class="empty-row">${esc(error.message)}</div>`}
}

async function decideRecommendation(id,action){
  const buttons=$$(`[data-recommendation-id="${id}"]`);buttons.forEach(x=>x.disabled=true);message("#evolutionActionMessage",action==="accepted"?"Creating a reviewed Campaign…":"Recording rejection…");
  try{const result=await post(`/api/recommendations/${encodeURIComponent(id)}/decision`,{owner_id:"local-user",action,create_campaign:action==="accepted",submit:false});await loadEvolution();if(result.campaign_created){$("#evolutionActionMessage").innerHTML=`Campaign ${esc(result.campaign_id)} created and still idle. <button class="button small" id="submitApprovedCampaign">Confirm Runtime submission</button>`;$("#submitApprovedCampaign").addEventListener("click",()=>submitApprovedRecommendation(id))}else{message("#evolutionActionMessage","Decision recorded; no execution was started.")}}catch(error){message("#evolutionActionMessage",error.message,true)}finally{buttons.forEach(x=>x.disabled=false)}
}

async function submitApprovedRecommendation(id){
  const button=$("#submitApprovedCampaign");if(button)button.disabled=true;message("#evolutionActionMessage","Submitting the approved Campaign to Runtime…");
  try{const result=await post(`/api/recommendations/${encodeURIComponent(id)}/decision`,{owner_id:"local-user",action:"accepted",create_campaign:true,submit:true});message("#evolutionActionMessage",`Runtime queued ${result.run_ids.length} run. Campaign ${result.campaign_id}.`);await loadRuns()}catch(error){message("#evolutionActionMessage",error.message,true)}
}

function formatDate(value){if(value===null||value===undefined)return "";const d=typeof value==="number"?new Date(value*1000):new Date(value);return Number.isNaN(d.valueOf())?"":d.toLocaleDateString(undefined,{month:"short",day:"2-digit"})}

$$('[data-route]').forEach(el=>el.addEventListener("click",()=>route(el.dataset.route)));
$("#frontendDesign").addEventListener("change",e=>selectDesign(e.target.value));
$("#backendDesign").addEventListener("change",e=>selectDesign(e.target.value));
$("#frontendView").addEventListener("change",renderDesignView);
$("#importRtl").addEventListener("click",importRtl);$("#createSpec").addEventListener("click",createSpec);$("#saveProvider").addEventListener("click",saveProvider);
$("#runSelect").addEventListener("change",e=>selectRun(e.target.value));$("#submitFlow").addEventListener("click",submitFlow);
$("#refreshResults").addEventListener("click",loadResults);
$$('#resultFilters button').forEach(button=>button.addEventListener("click",()=>{$$('#resultFilters button').forEach(x=>x.classList.remove("active"));button.classList.add("active");state.resultFilter=button.dataset.filter;renderResults()}));

Promise.all([loadPlatform(),loadDesigns(),loadRuns()]).finally(()=>route(location.hash.slice(1)||"overview"));
