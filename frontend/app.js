const state = { cases: [], activeCase: null, history: [] };

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const statusLabels = {
  OPEN: "等待调查", INVESTIGATING: "调查中", PENDING_REVIEW: "等待审核",
  MONITORING: "持续观察", ACTION_REQUIRED: "需要处置",
  CLOSED_FALSE_POSITIVE: "确认误报", CLOSED_RESOLVED: "已经解决",
};
const priorityLabels = { LOW: "低", MEDIUM: "一般", HIGH: "高", CRITICAL: "关键" };
const caseTypeLabels = { ACCOUNTS_RECEIVABLE: "客户应收", INVENTORY: "库存积压" };
const hypothesisLabels = { SUPPORTED: "证据支持", WEAKENED: "证据削弱", UNRESOLVED: "待补证" };

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

function formatMoney(value) {
  const number = Number(value || 0);
  if (Math.abs(number) >= 100000000) return `${(number / 100000000).toFixed(2)} 亿元`;
  if (Math.abs(number) >= 10000) return `${(number / 10000).toFixed(2)} 万元`;
  return `${number.toFixed(2)} 元`;
}

function formatPercent(value) {
  return value === null || value === undefined ? "—" : `${(Number(value) * 100).toFixed(1)}%`;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `请求失败（${response.status}）`);
  return payload;
}

function setSystemStatus(copy, kind = "ready") {
  const element = $("#system-status");
  element.textContent = copy;
  element.className = `system-status ${kind}`;
}

function switchView(viewId) {
  $$(".view").forEach((view) => view.classList.toggle("active", view.id === viewId));
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.viewTarget === viewId));
  const labels = { "risk-view": "风险总览", "cases-view": "案件队列", "business-view": "经营分析", "chat-view": "数据问答" };
  $("#page-title").textContent = labels[viewId];
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function renderRiskOverview(data) {
  $("#hero-case-count").textContent = data.total_cases;
  $("#critical-count").textContent = data.critical_cases;
  $("#open-count").textContent = data.open_cases;
  $("#pending-count").textContent = data.pending_review_cases;
  $("#risk-exposure").textContent = formatMoney(data.exposure_amount);
  const ar = data.cases_by_type.ACCOUNTS_RECEIVABLE || 0;
  const inventory = data.cases_by_type.INVENTORY || 0;
  const total = Math.max(ar + inventory, 1);
  $("#ar-case-count").textContent = `${ar} 件`;
  $("#inventory-case-count").textContent = `${inventory} 件`;
  $("#ar-case-bar").style.width = `${(ar / total) * 100}%`;
  $("#inventory-case-bar").style.width = `${(inventory / total) * 100}%`;
  $("#latest-run-copy").textContent = data.latest_run
    ? `规则集 ${data.latest_run.rule_set_version} · 观察期 ${data.latest_run.observation_date} · 命中 ${data.latest_run.rule_hits} 条规则`
    : "尚未执行规则扫描，请点击右上角“重新扫描”。";
}

function caseRow(caseItem) {
  return `<tr class="clickable-row" data-case-id="${escapeHtml(caseItem.case_id)}" tabindex="0">
    <td><span class="priority-chip ${caseItem.priority.toLowerCase()}">${priorityLabels[caseItem.priority]}</span></td>
    <td><strong>${escapeHtml(caseItem.entity_label)}</strong><small>${caseTypeLabels[caseItem.case_type]}</small></td>
    <td class="summary-cell">${escapeHtml(caseItem.summary)}</td>
    <td>${formatMoney(caseItem.exposure_amount)}</td>
    <td><span class="status-chip ${caseItem.status.toLowerCase()}">${statusLabels[caseItem.status]}</span></td>
    <td>${escapeHtml(caseItem.observation_date)}</td>
  </tr>`;
}

function bindCaseRows() {
  $$("[data-case-id]").forEach((row) => {
    const open = () => void openCase(row.dataset.caseId);
    row.addEventListener("click", open);
    row.addEventListener("keydown", (event) => { if (event.key === "Enter") open(); });
  });
}

function renderCases() {
  const type = $("#case-type-filter").value;
  const status = $("#case-status-filter").value;
  const filtered = state.cases.filter((item) => (!type || item.case_type === type) && (!status || item.status === status));
  $("#case-result-count").textContent = `共 ${filtered.length} 个案件`;
  $("#case-table-body").innerHTML = filtered.length
    ? filtered.map(caseRow).join("")
    : '<tr><td colspan="6">当前筛选条件下没有案件</td></tr>';
  const priority = state.cases.slice(0, 5);
  $("#priority-cases").classList.remove("loading-block");
  $("#priority-cases").innerHTML = priority.length ? priority.map((item) => `
    <button class="case-preview" data-case-id="${escapeHtml(item.case_id)}" type="button">
      <span class="priority-line ${item.priority.toLowerCase()}"></span>
      <span class="case-preview-main"><strong>${escapeHtml(item.entity_label)}</strong><small>${escapeHtml(item.summary)}</small></span>
      <span class="case-preview-meta"><strong>${formatMoney(item.exposure_amount)}</strong><small>${statusLabels[item.status]}</small></span>
    </button>`).join("") : '<p class="empty-copy">尚无风险案件。</p>';
  bindCaseRows();
}

async function loadRiskData() {
  const [overview, cases] = await Promise.all([api("/api/v1/risk/overview"), api("/api/v1/cases")]);
  state.cases = cases;
  renderRiskOverview(overview);
  renderCases();
}

function metricMap(toolResult) {
  return Object.fromEntries(toolResult.rows.map(([name, value]) => [name, value]));
}

async function loadBusinessOverview() {
  const data = await api("/api/v1/overview");
  const overview = metricMap(data.overview);
  const ar = metricMap(data.latest_ar);
  const cards = [
    ["累计销售额", formatMoney(overview["销售额"]), "含退货负值"],
    ["累计回款额", formatMoney(overview["回款额"]), "全数据窗口"],
    ["最新应收余额", formatMoney(ar["应收余额"]), data.latest_ar.period],
    ["最新超期率", formatPercent(ar["超期率"]), `超期 ${formatMoney(ar["超期应收"])}`],
  ];
  $("#business-cards").innerHTML = cards.map(([label, value, note]) => `
    <article class="metric-card"><span>${label}</span><strong>${value}</strong><small>${note}</small></article>`).join("");
  $("#business-period").textContent = `${data.overview.period} · 确定性指标，不消耗模型额度`;
  $("#trend-body").innerHTML = data.ar_trend.rows.slice(-8).reverse().map((row) => `
    <tr><td>${escapeHtml(row[0])}</td><td>${formatMoney(row[1])}</td><td>${formatMoney(row[2])}</td><td>${formatPercent(row[3])}</td></tr>`).join("");
}

function evidenceIndex(investigation) {
  return Object.fromEntries((investigation?.evidence || []).map((item) => [item.evidence_id, item]));
}

function evidenceTags(ids, evidence) {
  if (!ids?.length) return '<span class="muted">无引用</span>';
  return ids.map((id) => {
    const item = evidence[id];
    return item ? `<span class="evidence-tag" title="${escapeHtml(item.summary)}">${escapeHtml(item.tool_name)} · ${escapeHtml(item.period)}</span>` : "";
  }).join("");
}

function investigationHtml(record) {
  if (!record) return `<div class="empty-investigation">
    <span class="empty-icon">AI</span><h3>尚未执行案件调查</h3>
    <p>调查Agent会根据案件类型逐步查询至少两项独立证据，并把原因标记为支持、削弱或待补证。</p>
    <button id="investigate-button" class="button primary" type="button">开始 Agent 调查</button>
  </div>`;
  const report = record.report;
  const evidence = evidenceIndex(record);
  return `<div class="investigation-report">
    <div class="report-summary"><span>调查结论</span><h3>${escapeHtml(report.investigation_summary)}</h3>
      <div><span class="priority-chip ${report.recommended_priority.toLowerCase()}">建议${priorityLabels[report.recommended_priority]}</span>
      <span class="completeness">证据完整度 ${report.evidence_completeness}</span></div></div>
    <div class="hypothesis-list">${report.hypotheses.map((item) => `<article class="hypothesis ${item.status.toLowerCase()}">
      <header><span>${hypothesisLabels[item.status]}</span><strong>${escapeHtml(item.statement)}</strong></header>
      <div class="evidence-tags">${evidenceTags(item.supporting_evidence_ids, evidence)}</div>
      ${item.missing_evidence.length ? `<p><b>缺失证据：</b>${escapeHtml(item.missing_evidence.join("；"))}</p>` : ""}
    </article>`).join("")}</div>
    <div class="report-columns">
      <div><h4>建议动作</h4><ul>${report.recommended_actions.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>
      <div><h4>数据限制</h4><ul>${report.limitations.length ? report.limitations.map((item) => `<li>${escapeHtml(item)}</li>`).join("") : "<li>未报告额外限制</li>"}</ul></div>
    </div>
    <details class="evidence-detail"><summary>查看本轮 ${record.evidence.length} 项工具证据</summary>
      ${record.evidence.map((item) => `<div><strong>${escapeHtml(item.tool_name)}</strong><span>${escapeHtml(item.period)} · ${escapeHtml(item.sources.join(" / "))}</span><p>${escapeHtml(item.summary)}</p></div>`).join("")}
    </details>
    <button id="investigate-button" class="button secondary" type="button">重新调查</button>
  </div>`;
}

function reviewHistoryHtml(reviews) {
  if (!reviews.length) return '<p class="muted">还没有人工审核记录。</p>';
  return reviews.map((review) => `<article class="review-history-item"><strong>${escapeHtml(review.reviewer)} · ${statusLabels[review.decision === "MONITOR" ? "MONITORING" : review.decision === "FALSE_POSITIVE" ? "CLOSED_FALSE_POSITIVE" : review.decision === "RESOLVED" ? "CLOSED_RESOLVED" : "ACTION_REQUIRED"]}</strong><span>${escapeHtml(review.created_at)}</span><p>${escapeHtml(review.reason)}</p></article>`).join("");
}

function caseDetailHtml(caseItem) {
  return `<div class="case-header-strip">
    <div><span class="priority-chip ${caseItem.priority.toLowerCase()}">${priorityLabels[caseItem.priority]}优先级</span><span class="status-chip ${caseItem.status.toLowerCase()}">${statusLabels[caseItem.status]}</span></div>
    <div><span>风险敞口</span><strong>${formatMoney(caseItem.exposure_amount)}</strong></div>
    <div><span>观察日期</span><strong>${escapeHtml(caseItem.observation_date)}</strong></div>
    <div><span>规则版本</span><strong>${escapeHtml(caseItem.rule_set_version)}</strong></div>
  </div>
  <div class="case-workspace-grid">
    <section class="case-column triggers"><div class="column-title"><span>01</span><h3>规则触发</h3></div>
      <p class="case-summary">${escapeHtml(caseItem.summary)}</p>
      ${caseItem.rule_hits.map((hit) => `<article class="rule-hit"><header><span>${escapeHtml(hit.rule_id)}</span><b>${priorityLabels[hit.severity]}</b></header><h4>${escapeHtml(hit.rule_name)}</h4><p>${escapeHtml(hit.reason)}</p><small>${escapeHtml(hit.sources.join(" / "))} · ${escapeHtml(hit.period)}</small></article>`).join("")}
    </section>
    <section class="case-column investigation"><div class="column-title"><span>02</span><h3>Agent 调查</h3></div>${investigationHtml(caseItem.latest_investigation)}</section>
    <section class="case-column review"><div class="column-title"><span>03</span><h3>人工审核</h3></div>
      <form id="review-form" class="review-form">
        <label>审核决定<select id="review-decision" required><option value="">请选择</option><option value="MONITOR">暂时接受，持续观察</option><option value="ACTION_REQUIRED">风险成立，需要处置</option><option value="FALSE_POSITIVE">确认误报或数据问题</option><option value="RESOLVED">风险已经解决</option></select></label>
        <label>审核人<input id="reviewer" maxlength="100" required placeholder="输入审核人姓名" /></label>
        <label>审核原因<textarea id="review-reason" maxlength="1000" required placeholder="说明为什么接受、升级或驳回"></textarea></label>
        <label>后续动作<input id="review-action" maxlength="1000" placeholder="例如：跟踪指定订单回款" /></label>
        <label id="review-date-field" class="hidden">复查日期<input id="review-date" type="date" /></label>
        <button class="button primary" type="submit">提交人工审核</button>
      </form>
      <div class="review-history"><h4>审核历史</h4>${reviewHistoryHtml(caseItem.reviews)}</div>
    </section>
  </div>`;
}

async function openCase(caseId) {
  const dialog = $("#case-dialog");
  $("#case-dialog-body").innerHTML = '<div class="dialog-loading">正在装载规则、证据和审核记录</div>';
  if (!dialog.open) dialog.showModal();
  try {
    const caseItem = await api(`/api/v1/cases/${encodeURIComponent(caseId)}`);
    state.activeCase = caseItem;
    $("#case-dialog-eyebrow").textContent = `${caseTypeLabels[caseItem.case_type]} · ${caseItem.case_id}`;
    $("#case-dialog-title").textContent = caseItem.entity_label;
    $("#case-dialog-body").innerHTML = caseDetailHtml(caseItem);
    $("#investigate-button")?.addEventListener("click", () => void investigateActiveCase());
    $("#review-decision").addEventListener("change", (event) => {
      $("#review-date-field").classList.toggle("hidden", event.target.value !== "MONITOR");
      $("#review-date").required = event.target.value === "MONITOR";
    });
    $("#review-form").addEventListener("submit", (event) => void submitReview(event));
  } catch (error) {
    $("#case-dialog-body").innerHTML = `<p class="error-copy">${escapeHtml(error.message)}</p>`;
  }
}

async function investigateActiveCase() {
  if (!state.activeCase) return;
  const button = $("#investigate-button");
  button.disabled = true;
  button.textContent = "Agent 正在分步取证…";
  try {
    await api(`/api/v1/cases/${encodeURIComponent(state.activeCase.case_id)}/investigations`, { method: "POST" });
    await loadRiskData();
    await openCase(state.activeCase.case_id);
  } catch (error) {
    button.disabled = false;
    button.textContent = "重新尝试调查";
    alert(`调查失败：${error.message}`);
  }
}

async function submitReview(event) {
  event.preventDefault();
  if (!state.activeCase) return;
  const decision = $("#review-decision").value;
  const payload = {
    decision, reviewer: $("#reviewer").value.trim(), reason: $("#review-reason").value.trim(),
    action: $("#review-action").value.trim() || null,
    next_review_at: decision === "MONITOR" ? $("#review-date").value : null,
  };
  try {
    await api(`/api/v1/cases/${encodeURIComponent(state.activeCase.case_id)}/reviews`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    await loadRiskData();
    await openCase(state.activeCase.case_id);
  } catch (error) { alert(`审核提交失败：${error.message}`); }
}

async function runScan() {
  const button = $("#scan-button");
  button.disabled = true;
  button.textContent = "扫描中…";
  try {
    const result = await api("/api/v1/rule-runs", { method: "POST" });
    await loadRiskData();
    setSystemStatus(`扫描完成 · ${result.cases_detected} 个案件`);
  } catch (error) { setSystemStatus(error.message, "error"); }
  finally { button.disabled = false; button.textContent = "重新扫描"; }
}

function addMessage(role, content, evidence = []) {
  const message = document.createElement("div");
  message.className = `message ${role}`;
  message.textContent = content;
  $("#messages").appendChild(message);
  if (evidence.length) {
    const detail = document.createElement("div"); detail.className = "chat-evidence";
    detail.textContent = `证据：${evidence.map((item) => `${item.tool_name} · ${item.period}`).join("；")}`;
    $("#messages").appendChild(detail);
  }
  $("#messages").scrollTop = $("#messages").scrollHeight;
}

async function sendMessage(message) {
  addMessage("user", message);
  const button = $("#send-button"); button.disabled = true; button.textContent = "分析中";
  try {
    const payload = await api("/api/v1/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message, history: state.history.slice(-10) }) });
    addMessage("assistant", payload.answer, payload.evidence);
    state.history.push({ role: "user", content: message }, { role: "assistant", content: payload.answer });
  } catch (error) { addMessage("assistant", `请求失败：${error.message}`); }
  finally { button.disabled = false; button.textContent = "发送问题"; }
}

async function init() {
  $$("[data-view-target]").forEach((item) => item.addEventListener("click", () => switchView(item.dataset.viewTarget)));
  $("#scan-button").addEventListener("click", () => void runScan());
  $("#case-dialog-close").addEventListener("click", () => $("#case-dialog").close());
  $("#case-dialog").addEventListener("click", (event) => { if (event.target === $("#case-dialog")) $("#case-dialog").close(); });
  $("#case-type-filter").addEventListener("change", renderCases);
  $("#case-status-filter").addEventListener("change", renderCases);
  $("#chat-form").addEventListener("submit", (event) => { event.preventDefault(); const input = $("#message-input"); const value = input.value.trim(); if (value) { input.value = ""; void sendMessage(value); } });
  $$(".suggestions button").forEach((button) => button.addEventListener("click", () => { $("#message-input").value = button.textContent; $("#message-input").focus(); }));
  try {
    await Promise.all([loadRiskData(), loadBusinessOverview()]);
    setSystemStatus("数据与案件已就绪");
  } catch (error) { setSystemStatus(error.message, "error"); }
}

void init();
