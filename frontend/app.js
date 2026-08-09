const state = { cases: [], activeCase: null, investigationEvents: [] };

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const statusLabels = {
  OPEN: "等待调查", INVESTIGATING: "调查中", PENDING_REVIEW: "等待审核",
  MONITORING: "持续观察", ACTION_REQUIRED: "需要处置",
  CLOSED_FALSE_POSITIVE: "确认误报", CLOSED_RESOLVED: "已经解决",
};
const priorityLabels = { LOW: "低", MEDIUM: "一般", HIGH: "高", CRITICAL: "关键" };
const caseTypeLabels = { ACCOUNTS_RECEIVABLE: "客户应收", INVENTORY: "库存积压" };
const hypothesisLabels = { SUPPORTED: "证据支持", WEAKENED: "证据削弱", UNRESOLVED: "无法判断" };
const riskStageLabels = {
  EARLY_WARNING: "早期预警", DETERIORATING: "风险恶化",
  LIMITED: "信息有限",
};
const toolLabels = {
  discover_evidence_capabilities: "发现证据能力",
  search_business_records: "搜索业务记录",
  query_business_evidence: "受控证据查询",
};
const eventLabels = {
  RUN_STARTED: "调查已启动",
  TOOL_STARTED: "正在查询",
  TOOL_COMPLETED: "证据已返回",
  VALIDATION_STARTED: "正在核验证据",
  REPORT_COMPLETED: "报告已保存",
  ERROR: "调查遇到问题",
};
const datasetLabels = {
  receivables: "应收",
  sales_payments: "销售与回款",
  extensions: "展期",
  credit: "授信",
  contracts: "合同",
  inventory: "库存",
  sales: "物料销售",
};

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

function compactSummary(value, maxLength = 190) {
  const copy = String(value || "");
  if (copy.length <= maxLength) return copy;
  const naturalBreak = copy.slice(0, maxLength).lastIndexOf("。");
  return naturalBreak >= 100 ? copy.slice(0, naturalBreak + 1) : `${copy.slice(0, maxLength)}…`;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `请求失败（${response.status}）`);
  return payload;
}

async function streamNdjson(path, options, onEvent) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const payload = await response.json();
    throw new Error(payload.error || `请求失败（${response.status}）`);
  }
  if (!response.body) throw new Error("浏览器没有收到调查事件流。");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    lines.filter(Boolean).forEach((line) => onEvent(JSON.parse(line)));
    if (done) break;
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer));
}

function setSystemStatus(copy, kind = "ready") {
  const element = $("#system-status");
  element.textContent = copy;
  element.className = `system-status ${kind}`;
}

function switchView(viewId) {
  $$(".view").forEach((view) => view.classList.toggle("active", view.id === viewId));
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.viewTarget === viewId));
  const labels = { "risk-view": "风险总览", "cases-view": "案件队列", "business-view": "经营分析" };
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
    return item ? `<wa-tag class="evidence-tag" size="xs" pill appearance="outlined" variant="neutral" title="${escapeHtml(item.summary)}">${escapeHtml(toolLabels[item.tool_name] || item.tool_name)} · ${escapeHtml(item.period)}</wa-tag>` : "";
  }).join("");
}

function riskVariant(stage) {
  return { EARLY_WARNING: "warning", DETERIORATING: "danger", LIMITED: "neutral" }[stage] || "neutral";
}

function hypothesisVariant(status) {
  return { SUPPORTED: "success", WEAKENED: "neutral", UNRESOLVED: "warning" }[status] || "neutral";
}

function completenessValue(value) {
  return { LOW: 33, MEDIUM: 66, HIGH: 100 }[value] || 0;
}

function queryArguments(item) {
  const args = item.arguments || {};
  if (args.dataset) {
    const metrics = Array.isArray(args.metrics) ? args.metrics.join("、") : "";
    return `${datasetLabels[args.dataset] || args.dataset} / ${args.grain || "—"} · ${args.time_window || "—"}${metrics ? ` · ${metrics}` : ""}`;
  }
  return Object.entries(args)
    .map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join("、") : value}`)
    .join(" · ");
}

function riskAssessmentHtml(assessment, evidence) {
  if (!assessment) return `<wa-callout class="risk-assessment" variant="neutral">
    <wa-icon slot="icon" name="circle-info"></wa-icon>
    <strong>风险信号需要重新判断</strong><p>这份历史报告没有单独保存风险信号，请重新调查。</p>
  </wa-callout>`;
  const list = (title, items) => items?.length
    ? `<div><strong>${title}</strong><ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>`
    : "";
  return `<wa-callout class="risk-assessment" variant="${riskVariant(assessment.stage)}">
    <wa-icon slot="icon" name="${assessment.stage === "DETERIORATING" ? "triangle-exclamation" : "chart-line"}"></wa-icon>
    <div class="risk-assessment-head"><div><span class="section-kicker">RISK SIGNAL</span><h4>风险信号判断</h4></div>
      <wa-badge variant="${riskVariant(assessment.stage)}" appearance="filled" pill>${escapeHtml(riskStageLabels[assessment.stage] || assessment.stage)}</wa-badge></div>
    <p>${escapeHtml(assessment.statement)}</p>
    <div class="risk-assessment-grid">${list("主要驱动", assessment.drivers)}${list("反向信号", assessment.counter_signals)}${list("后续监测", assessment.watch_items)}</div>
    <div class="evidence-tags">${evidenceTags(assessment.evidence_ids, evidence)}</div>
  </wa-callout>`;
}

function liveInvestigationHtml(events) {
  const visibleEvents = events.slice(-12).reverse();
  const evidenceCount = events.filter((item) => item.evidence).length;
  const iconNames = {
    RUN_STARTED: "circle-play", TOOL_STARTED: "magnifying-glass",
    TOOL_COMPLETED: "database", VALIDATION_STARTED: "shield",
    REPORT_COMPLETED: "circle-check", ERROR: "triangle-exclamation",
  };
  return `<div class="agent-console" aria-live="polite">
    <wa-card class="agent-live-card" appearance="accent">
      <div class="agent-live-head">
        <span class="agent-orb"><wa-spinner></wa-spinner></span>
        <div><span class="section-kicker">DEEP INVESTIGATION</span><h3>Agent 正在调查</h3>
          <p>高强度思考已开启；界面只展示可核验的查询和证据。</p></div>
        <wa-badge variant="brand" appearance="filled" pill>${evidenceCount} 项证据</wa-badge>
      </div>
      <wa-progress-bar indeterminate label="Agent 调查进行中"></wa-progress-bar>
      <div class="agent-live-boundary"><wa-icon name="lock"></wa-icon>只读业务工具 · 无任意 SQL · 结论生成前自动校验证据引用</div>
    </wa-card>
    <section class="agent-activity">
      <header><div><span class="section-kicker">LIVE ACTIVITY</span><h4>调查活动</h4></div><span>${events.length} 条事件</span></header>
      <div class="activity-list">${visibleEvents.length ? visibleEvents.map((event) => `<article class="activity-item ${event.event_type.toLowerCase()}">
        <span class="activity-icon"><wa-icon name="${iconNames[event.event_type] || "circle"}"></wa-icon></span><div>
          <div class="activity-title"><strong>${escapeHtml(event.tool_name ? toolLabels[event.tool_name] || event.tool_name : eventLabels[event.event_type] || event.event_type)}</strong>
            <wa-badge variant="${event.event_type === "ERROR" ? "danger" : event.evidence ? "success" : "neutral"}" appearance="outlined">${escapeHtml(eventLabels[event.event_type] || "执行中")}</wa-badge></div>
          <p>${escapeHtml(event.message)}</p>
          ${event.arguments ? `<small>${escapeHtml(queryArguments(event))}</small>` : ""}
          ${event.evidence?.arguments ? `<small>${escapeHtml(queryArguments(event.evidence))}</small>` : ""}
          ${event.evidence ? `<small>证据 ${escapeHtml(event.evidence.evidence_id.slice(0, 8))} · ${escapeHtml(event.evidence.period)}</small>` : ""}
        </div></article>`).join("") : '<p class="muted">正在载入案件并准备数据发现。</p>'}</div>
    </section>
  </div>`;
}

function investigationHtml(record) {
  if (!record) return `<wa-card class="empty-investigation" appearance="plain">
    <span class="empty-icon"><wa-icon name="magnifying-glass"></wa-icon></span>
    <wa-badge variant="brand" appearance="outlined" pill>READ-ONLY AGENT</wa-badge>
    <h3>让 Agent 从证据开始调查</h3>
    <p>它会先发现可用数据，再自主组合受控查询。界面只展示实际工具、原始证据和已校验结论，不展示私有思考过程。</p>
    <div class="empty-boundaries"><span>高强度思考</span><span>受控只读查询</span><span>人工最终审核</span></div>
    <button id="investigate-button" class="button primary" type="button">开始 Agent 调查</button>
  </wa-card>`;
  const report = record.report;
  const evidence = evidenceIndex(record);
  const priorityVariant = { CRITICAL: "danger", HIGH: "warning", MEDIUM: "brand", LOW: "neutral" }[report.recommended_priority] || "neutral";
  const summaryPreview = compactSummary(report.investigation_summary);
  const hasFullSummary = summaryPreview !== report.investigation_summary;
  return `<div class="agent-report">
    <wa-card class="report-summary" appearance="filled-outlined">
      <div class="report-summary-head"><div><span class="section-kicker">VERIFIED OUTCOME</span><h3>调查结论</h3></div>
        <wa-badge variant="${priorityVariant}" appearance="filled" pill>建议${priorityLabels[report.recommended_priority]}优先级</wa-badge></div>
      <p>${escapeHtml(summaryPreview)}</p>
      ${hasFullSummary ? `<wa-details class="summary-details" summary="展开完整调查结论"><p>${escapeHtml(report.investigation_summary)}</p></wa-details>` : ""}
      <div class="report-confidence"><span>证据完整度 ${escapeHtml(report.evidence_completeness)}</span><strong>${completenessValue(report.evidence_completeness)}%</strong></div>
      <wa-progress-bar value="${completenessValue(report.evidence_completeness)}" label="证据完整度"></wa-progress-bar>
    </wa-card>
    ${riskAssessmentHtml(report.risk_assessment, evidence)}
    <section class="report-block report-facts"><header><div><span class="section-kicker">FACTS</span><h4>确定事实</h4></div><span>${report.facts.length} 项</span></header>
      <div class="fact-list">${report.facts.length ? report.facts.map((item) => `<wa-card class="fact-card" appearance="plain">
        <p>${escapeHtml(item.statement)}</p><div class="evidence-tags">${evidenceTags(item.evidence_ids, evidence)}</div>
      </wa-card>`).join("") : '<p class="muted">没有形成可引用的确定事实。</p>'}</div>
    </section>
    <section class="report-block"><header><div><span class="section-kicker">ASSESSMENTS</span><h4>证据支持的判断</h4></div><span>${report.hypotheses.length} 项</span></header>
      <div class="hypothesis-list">${report.hypotheses.map((item) => `<wa-card class="hypothesis ${item.status.toLowerCase()}" appearance="outlined">
        <div class="hypothesis-head"><wa-badge variant="${hypothesisVariant(item.status)}" appearance="filled" pill>${hypothesisLabels[item.status]}</wa-badge>
          <strong>${escapeHtml(item.statement)}</strong></div>
        <div class="hypothesis-evidence"><small>支持证据</small><div class="evidence-tags">${evidenceTags(item.supporting_evidence_ids, evidence)}</div></div>
        <div class="hypothesis-evidence"><small>反驳证据</small><div class="evidence-tags">${evidenceTags(item.contradicting_evidence_ids, evidence)}</div></div>
        ${item.missing_evidence.length ? `<p><b>仍需补证：</b>${escapeHtml(item.missing_evidence.join("；"))}</p>` : ""}
      </wa-card>`).join("")}</div>
    </section>
    <div class="report-columns">
      <wa-card appearance="plain"><span class="section-kicker">NEXT ACTION</span><h4>建议动作</h4><ul>${report.recommended_actions.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></wa-card>
      <wa-card appearance="plain"><span class="section-kicker">LIMITATIONS</span><h4>数据限制</h4><ul>${report.limitations.length ? report.limitations.map((item) => `<li>${escapeHtml(item)}</li>`).join("") : "<li>未报告额外限制</li>"}</ul></wa-card>
    </div>
    <wa-details class="agent-details" summary="查看本轮 ${record.evidence.length} 项工具证据">
      <div class="evidence-records">${record.evidence.map((item, index) => `<article class="evidence-record"><header><span class="evidence-number">${String(index + 1).padStart(2, "0")}</span>
        <div><strong>${escapeHtml(toolLabels[item.tool_name] || item.tool_name)}</strong><small>${escapeHtml(item.period)} · ${escapeHtml(item.sources.join(" / "))}</small></div></header>
        <p>${escapeHtml(item.summary)}</p><div class="evidence-meta"><code>${escapeHtml(item.evidence_id)}</code>${item.arguments ? `<span>${escapeHtml(queryArguments(item))}</span>` : ""}</div>
        ${item.warnings?.length ? `<small class="evidence-warning">限制：${escapeHtml(item.warnings.join("；"))}</small>` : ""}</article>`).join("")}</div>
    </wa-details>
    ${report.trace?.length ? `<wa-details class="agent-details" summary="回放调查轨迹"><div class="trace-records">${report.trace.map((item) => `<article class="trace-record"><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.created_at)}${item.tool_name ? ` · ${escapeHtml(toolLabels[item.tool_name] || item.tool_name)}` : ""}</small><p>${escapeHtml(item.detail)}</p></article>`).join("")}</div></wa-details>` : ""}
    <footer class="report-footer"><div><wa-icon name="user-check"></wa-icon><span>Agent 提供调查证据，最终业务处置仍由人工审核决定。</span></div>
      <button id="investigate-button" class="button secondary" type="button">重新调查</button></footer>
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
    <section class="case-column investigation"><div class="column-title"><span>02</span><h3>Agent 调查</h3></div><div id="investigation-content">${investigationHtml(caseItem.latest_investigation)}</div></section>
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
    bindInvestigationButton();
    $("#review-decision").addEventListener("change", (event) => {
      $("#review-date-field").classList.toggle("hidden", event.target.value !== "MONITOR");
      $("#review-date").required = event.target.value === "MONITOR";
    });
    $("#review-form").addEventListener("submit", (event) => void submitReview(event));
  } catch (error) {
    $("#case-dialog-body").innerHTML = `<p class="error-copy">${escapeHtml(error.message)}</p>`;
  }
}

function bindInvestigationButton() {
  $("#investigate-button")?.addEventListener("click", () => void investigateActiveCase());
}

async function investigateActiveCase() {
  if (!state.activeCase) return;
  const caseId = state.activeCase.case_id;
  const content = $("#investigation-content");
  state.investigationEvents = [];
  content.innerHTML = liveInvestigationHtml(state.investigationEvents);
  let finalRecord = null;
  let terminalError = null;
  try {
    await streamNdjson(`/api/v1/cases/${encodeURIComponent(caseId)}/investigations`, { method: "POST" }, (event) => {
      state.investigationEvents.push(event);
      if (event.event_type === "REPORT_COMPLETED" && event.record) {
        finalRecord = event.record;
        content.innerHTML = investigationHtml(finalRecord);
        bindInvestigationButton();
      } else {
        if (event.event_type === "ERROR") terminalError = event.message;
        content.innerHTML = liveInvestigationHtml(state.investigationEvents);
      }
      content.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
    if (!finalRecord) throw new Error(terminalError || "调查事件流结束，但没有生成可保存的报告。");
    await loadRiskData();
    state.activeCase.latest_investigation = finalRecord;
    state.activeCase.status = "PENDING_REVIEW";
  } catch (error) {
    content.innerHTML += `<div class="stream-error"><strong>本次调查未生成报告</strong><p>${escapeHtml(error.message)}</p><button id="investigate-button" class="button secondary" type="button">重新尝试调查</button></div>`;
    bindInvestigationButton();
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

async function init() {
  $$("[data-view-target]").forEach((item) => item.addEventListener("click", () => switchView(item.dataset.viewTarget)));
  $("#scan-button").addEventListener("click", () => void runScan());
  $("#case-dialog-close").addEventListener("click", () => $("#case-dialog").close());
  $("#case-dialog").addEventListener("click", (event) => { if (event.target === $("#case-dialog")) $("#case-dialog").close(); });
  $("#case-type-filter").addEventListener("change", renderCases);
  $("#case-status-filter").addEventListener("change", renderCases);
  try {
    await Promise.all([loadRiskData(), loadBusinessOverview()]);
    setSystemStatus("数据与案件已就绪");
  } catch (error) { setSystemStatus(error.message, "error"); }
}

void init();
