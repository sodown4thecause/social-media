const state = {
  summary: null,
  signals: [],
  selectedId: null,
  currentDetail: null,
  view: "inbox",
};

const els = {
  metricGrid: document.querySelector("#metricGrid"),
  sourceFilter: document.querySelector("#sourceFilter"),
  scoreFilter: document.querySelector("#scoreFilter"),
  searchInput: document.querySelector("#searchInput"),
  signalList: document.querySelector("#signalList"),
  queueCount: document.querySelector("#queueCount"),
  detailPanel: document.querySelector("#detailPanel"),
  refreshBtn: document.querySelector("#refreshBtn"),
  leadStatusFilter: document.querySelector("#leadStatusFilter"),
  leadTable: document.querySelector("#leadTable"),
  intentBars: document.querySelector("#intentBars"),
  sourceBars: document.querySelector("#sourceBars"),
  providerHealth: document.querySelector("#providerHealth"),
  sourceHealth: document.querySelector("#sourceHealth"),
  sourceCheckBtn: document.querySelector("#sourceCheckBtn"),
  deepSourceCheckBtn: document.querySelector("#deepSourceCheckBtn"),
  pipelineState: document.querySelector("#pipelineState"),
  pipelineLog: document.querySelector("#pipelineLog"),
  toast: document.querySelector("#toast"),
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  let payload;
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`Expected JSON from ${path}, got ${text.slice(0, 40) || "empty response"}`);
  }
  if (!response.ok) {
    throw new Error(payload.error || "Request failed");
  }
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function toast(message) {
  els.toast.textContent = message;
  els.toast.classList.add("is-visible");
  window.setTimeout(() => els.toast.classList.remove("is-visible"), 2600);
}

function metric(label, value) {
  return `
    <div class="metric">
      <div class="metric-label">${escapeHtml(label)}</div>
      <div class="metric-value">${escapeHtml(value)}</div>
    </div>
  `;
}

function renderSummary() {
  const stats = state.summary?.stats || {};
  els.metricGrid.innerHTML = [
    metric("Pending review", stats.pending ?? 0),
    metric("Saved leads", stats.leads ?? 0),
    metric("High value", stats.high_value_leads ?? 0),
    metric("Enrichment spend", `$${(((stats.enrichment_cost_cents ?? 0) / 100).toFixed(2))}`),
  ].join("");

  const sources = ["all", ...(state.summary?.sources || [])];
  els.sourceFilter.innerHTML = sources
    .map((source) => `<option value="${escapeHtml(source)}">${source === "all" ? "All sources" : escapeHtml(source)}</option>`)
    .join("");

  renderBars(els.intentBars, state.summary?.by_intent || [], "cluster");
  renderBars(els.sourceBars, state.summary?.by_source || [], "source");
}

function renderBars(container, rows, labelKey) {
  if (!rows.length) {
    container.innerHTML = `<div class="muted">No data yet.</div>`;
    return;
  }
  const max = Math.max(...rows.map((row) => row.count || 0), 1);
  container.innerHTML = rows
    .map((row) => {
      const width = Math.max(4, Math.round(((row.count || 0) / max) * 100));
      return `
        <div class="bar-row">
          <div class="bar-meta">
            <strong>${escapeHtml(row[labelKey])}</strong>
            <span>${escapeHtml(row.count)}</span>
          </div>
          <div class="bar-track"><div class="bar-fill" style="width: ${width}%"></div></div>
        </div>
      `;
    })
    .join("");
}

function signalRow(item) {
  const active = item.candidate_id === state.selectedId ? " is-active" : "";
  return `
    <button class="signal-row${active}" type="button" data-candidate-id="${item.candidate_id}">
      <div class="signal-top">
        <div>
          <div class="signal-title">${escapeHtml(item.cluster)}</div>
          <div class="meta">${escapeHtml(item.source)} · ${escapeHtml(item.created_label)}</div>
        </div>
        <span class="score-pill">${Math.round(item.total_score)}</span>
      </div>
      <div class="muted">${escapeHtml(item.snippet)}</div>
      <div class="tag-row">
        <span class="tag">${escapeHtml(item.angle)}</span>
        <span class="tag">${Math.round((item.confidence || 0) * 100)}% intent</span>
        <span class="contact-pill">${escapeHtml(item.contact.label)}</span>
        ${item.enriched ? `<span class="tag">enriched</span>` : ""}
      </div>
    </button>
  `;
}

async function loadInbox(selectFirst = false) {
  const query = new URLSearchParams({
    source: els.sourceFilter.value || "all",
    min_score: els.scoreFilter.value || "0",
    q: els.searchInput.value || "",
  });
  state.signals = await api(`/api/inbox?${query.toString()}`);
  els.queueCount.textContent = `${state.signals.length} signals ready for review`;
  els.signalList.innerHTML = state.signals.length
    ? state.signals.map(signalRow).join("")
    : `<div class="empty-state"><h2>No signals match</h2><p>Try a lower score threshold or a different source.</p></div>`;
  if ((selectFirst || !state.selectedId) && state.signals.length) {
    await selectSignal(state.signals[0].candidate_id);
  } else {
    syncActiveSignal();
  }
}

function syncActiveSignal() {
  document.querySelectorAll(".signal-row").forEach((row) => {
    row.classList.toggle("is-active", Number(row.dataset.candidateId) === state.selectedId);
  });
}

async function selectSignal(candidateId) {
  state.selectedId = Number(candidateId);
  syncActiveSignal();
  state.currentDetail = await api(`/api/candidates/${candidateId}`);
  renderDetail();
}

function jsonSummary(value) {
  if (!value) return "No enrichment captured yet.";
  if (typeof value === "string") return value.slice(0, 700);
  return JSON.stringify(value, null, 2).slice(0, 900);
}

function renderCandidateChips(detail) {
  return detail.other_candidates
    .map((candidate) => `
      <button class="candidate-chip ${candidate.id === detail.candidate_id ? "is-active" : ""}"
        type="button"
        data-candidate-option="${candidate.id}"
        data-candidate-text="${escapeHtml(candidate.text)}">
        ${escapeHtml(candidate.angle)} · ${Math.round(candidate.total_score)}
      </button>
    `)
    .join("");
}

function renderDetail() {
  const detail = state.currentDetail;
  if (!detail) return;
  const score = Math.round(detail.total_score || 0);
  const confidence = Math.round((detail.confidence || 0) * 100);
  els.detailPanel.innerHTML = `
    <div class="detail-title-row">
      <div>
        <h2>${escapeHtml(detail.cluster)}</h2>
        <p class="muted">${escapeHtml(detail.source)} · ${escapeHtml(detail.created_label)} · ${confidence}% intent</p>
      </div>
      <span class="score-pill">${score}</span>
    </div>

    <div class="tag-row">
      <span class="contact-pill">${escapeHtml(detail.contact.label)}</span>
      <span class="tag">${escapeHtml(detail.angle)}</span>
      <span class="tag">${escapeHtml(detail.author || "unknown author")}</span>
    </div>

    <div class="detail-grid">
      <div class="detail-body">
        <h3>Original signal</h3>
        <div class="context-box">${escapeHtml(detail.post_text)}</div>
        <div class="inline-actions">
          <a class="button ghost" href="${escapeHtml(detail.url)}" target="_blank" rel="noreferrer">Open source</a>
        </div>

        <h3 style="margin-top: 18px;">Reviewed reply</h3>
        <div class="candidate-switcher">${renderCandidateChips(detail)}</div>
        <textarea id="replyText">${escapeHtml(detail.candidate_text)}</textarea>
        <input class="note-input" id="reviewNote" type="text" placeholder="Reviewer note, optional">
        <div class="reply-actions">
          <button class="button primary" type="button" data-action="approve-lead">Approve + save lead</button>
          <button class="button" type="button" data-action="approve">Approve only</button>
          <button class="button" type="button" data-action="edit-lead">Save edit + lead</button>
          <button class="button ghost" type="button" data-action="copy">Copy reply</button>
          <button class="button danger" type="button" data-action="reject">Reject</button>
        </div>
      </div>

      <aside class="detail-side">
        <div class="side-box">
          <h3>Contact path</h3>
          <p class="muted">${escapeHtml(detail.contact.guidance)}</p>
        </div>
        <div class="side-box">
          <h3>Enrichment</h3>
          <pre class="muted">${escapeHtml(jsonSummary(detail.perplexity || detail.browser_use || detail.dataforseo || detail.firecrawl))}</pre>
        </div>
        <div class="side-box">
          <h3>Lead state</h3>
          <p class="muted">${
            detail.lead_id
              ? `Saved as lead #${escapeHtml(detail.lead_id)} · ${escapeHtml(detail.lead_status)}`
              : "Not saved as a lead yet."
          }</p>
        </div>
      </aside>
    </div>
  `;
}

async function recordDecision(action) {
  const replyText = document.querySelector("#replyText")?.value.trim() || "";
  const note = document.querySelector("#reviewNote")?.value.trim() || "";
  const body = {
    decision: action === "reject" ? "rejected" : action.startsWith("edit") ? "edited" : "approved",
    edited_text: action.startsWith("edit") ? replyText : null,
    note,
    create_lead: action.endsWith("lead"),
  };
  await api(`/api/candidates/${state.selectedId}/decision`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  toast(action === "reject" ? "Signal rejected." : body.create_lead ? "Approved and saved as a lead." : "Approved.");
  state.selectedId = null;
  state.currentDetail = null;
  await refreshAll(true);
}

async function loadLeads() {
  const status = els.leadStatusFilter.value || "all";
  const leads = await api(`/api/leads?status=${encodeURIComponent(status)}`);
  els.leadTable.innerHTML = leads.length
    ? leads.map(leadRow).join("")
    : `<div class="empty-state"><h2>No leads yet</h2><p>Approve a signal and save it as a lead to start the pipeline.</p></div>`;
}

function leadRow(lead) {
  return `
    <div class="lead-row" data-lead-id="${lead.id}">
      <div>
        <div class="lead-row-top">
          <div class="lead-title">${escapeHtml(lead.intent_cluster)}</div>
          <span class="score-pill">${Math.round(lead.lead_score)}</span>
        </div>
        <div class="muted">${escapeHtml(lead.source)} · ${escapeHtml(lead.author || "unknown author")}</div>
        <div class="muted">${escapeHtml((lead.post_text || "").slice(0, 180))}</div>
      </div>
      <div>
        <span class="contact-pill">${escapeHtml(lead.contact.label)}</span>
      </div>
      <select data-lead-status>
        ${["new", "contacted", "converted", "lost", "ignored"].map((status) => `
          <option value="${status}" ${lead.status === status ? "selected" : ""}>${status}</option>
        `).join("")}
      </select>
    </div>
  `;
}

function switchView(view) {
  state.view = view;
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.view === view);
  });
  document.querySelectorAll(".view").forEach((section) => {
    section.classList.toggle("is-active", section.id === `${view}View`);
  });
  if (view === "leads") loadLeads().catch((error) => toast(error.message));
  if (view === "sources") loadSourceHealth(false).catch((error) => toast(error.message));
  if (view === "pipeline") loadPipelineStatus().catch((error) => toast(error.message));
}

function healthBadge(status) {
  const normalized = status === true ? "ok" : status === false ? "missing" : String(status || "unknown");
  return `<span class="health-badge ${escapeHtml(normalized)}">${escapeHtml(normalized)}</span>`;
}

function renderProviderHealth(providers) {
  els.providerHealth.innerHTML = Object.entries(providers || {})
    .map(([name, present]) => `
      <div class="health-row">
        <strong>${escapeHtml(name)}</strong>
        ${healthBadge(Boolean(present))}
      </div>
    `)
    .join("");
}

function renderSourceHealth(sources) {
  els.sourceHealth.innerHTML = Object.entries(sources || {})
    .map(([name, info]) => {
      const sample = info.samples?.[0]?.text || info.reason || info.error || "No sample returned.";
      return `
        <div class="health-row source">
          <div>
            <strong>${escapeHtml(name)}</strong>
            <p class="muted">${escapeHtml(sample)}</p>
          </div>
          <div>
            ${healthBadge(info.status)}
            <span class="tag">${escapeHtml(info.count || 0)} rows</span>
          </div>
        </div>
      `;
    })
    .join("");
}

async function loadSourceHealth(deep = false) {
  const data = await api(`/api/source-health${deep ? "?deep=true" : ""}`);
  renderProviderHealth(data.providers);
  renderSourceHealth(data.sources);
  toast(deep ? "Deep source check finished." : "Source check finished.");
}

function renderPipelineStatus(status) {
  if (!els.pipelineState || !els.pipelineLog) return;
  els.pipelineState.textContent = status.running ? `Running: ${status.stage || "working"}` : "Idle";
  els.pipelineState.classList.toggle("is-running", Boolean(status.running));
  const history = status.history || [];
  els.pipelineLog.innerHTML = history.length
    ? history.slice().reverse().map((event) => `
      <div class="pipeline-log-row">
        <div>
          <strong>${escapeHtml(event.stage)}</strong>
          <p class="muted">${escapeHtml(event.error || "Completed")} · ${escapeHtml(new Date((event.finished_at || 0) * 1000).toLocaleString())}</p>
        </div>
        ${healthBadge(event.status)}
      </div>
    `).join("")
    : `<div class="muted">No pipeline runs yet.</div>`;
}

async function loadPipelineStatus() {
  const status = await api("/api/pipeline/status");
  renderPipelineStatus(status);
  return status;
}

async function runPipeline(action) {
  await api("/api/pipeline/run", {
    method: "POST",
    body: JSON.stringify({ action }),
  });
  toast("Pipeline started.");
  await loadPipelineStatus();
}

async function refreshAll(selectFirst = false) {
  state.summary = await api("/api/summary");
  renderSummary();
  await loadInbox(selectFirst);
  if (state.view === "leads") await loadLeads();
}

els.signalList.addEventListener("click", (event) => {
  const row = event.target.closest("[data-candidate-id]");
  if (row) selectSignal(row.dataset.candidateId).catch((error) => toast(error.message));
});

els.detailPanel.addEventListener("click", (event) => {
  const option = event.target.closest("[data-candidate-option]");
  if (option) {
    document.querySelectorAll(".candidate-chip").forEach((chip) => chip.classList.remove("is-active"));
    option.classList.add("is-active");
    document.querySelector("#replyText").value = option.dataset.candidateText || "";
    return;
  }
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (!action) return;
  if (action === "copy") {
    navigator.clipboard.writeText(document.querySelector("#replyText")?.value || "");
    toast("Reply copied.");
    return;
  }
  recordDecision(action).catch((error) => toast(error.message));
});

els.leadTable.addEventListener("change", async (event) => {
  if (!event.target.matches("[data-lead-status]")) return;
  const row = event.target.closest("[data-lead-id]");
  await api(`/api/leads/${row.dataset.leadId}/status`, {
    method: "POST",
    body: JSON.stringify({ status: event.target.value }),
  });
  toast("Lead status updated.");
  await loadLeads();
});

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => switchView(button.dataset.view));
});

els.refreshBtn.addEventListener("click", () => refreshAll(false).catch((error) => toast(error.message)));
els.sourceCheckBtn?.addEventListener("click", () => loadSourceHealth(false).catch((error) => toast(error.message)));
els.deepSourceCheckBtn?.addEventListener("click", () => loadSourceHealth(true).catch((error) => toast(error.message)));
document.querySelectorAll("[data-pipeline-action]").forEach((button) => {
  button.addEventListener("click", () => runPipeline(button.dataset.pipelineAction).catch((error) => toast(error.message)));
});
els.sourceFilter.addEventListener("change", () => loadInbox(true).catch((error) => toast(error.message)));
els.scoreFilter.addEventListener("change", () => loadInbox(true).catch((error) => toast(error.message)));
els.leadStatusFilter.addEventListener("change", () => loadLeads().catch((error) => toast(error.message)));

let searchTimer;
els.searchInput.addEventListener("input", () => {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => loadInbox(true).catch((error) => toast(error.message)), 250);
});

refreshAll(true).catch((error) => toast(error.message));

window.setInterval(() => {
  if (state.view === "pipeline") {
    loadPipelineStatus().catch(() => {});
  }
}, 3000);
