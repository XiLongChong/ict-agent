export const labels = {
  status: {
    PENDING_AGENT_REVIEW: "待调查",
    PENDING_HUMAN_REVIEW: "待复核",
    ACTION_IN_PROGRESS: "处理中",
    CLOSED: "已关闭",
  },
  priority: { LOW: "低", MEDIUM: "一般", HIGH: "高" },
  caseType: { ACCOUNTS_RECEIVABLE: "应收", INVENTORY: "库存" },
  hypothesis: { SUPPORTED: "证据支持", WEAKENED: "证据削弱", UNRESOLVED: "无法判断" },
  riskStage: { EARLY_WARNING: "早期预警", DETERIORATING: "风险恶化", LIMITED: "信息有限" },
  tool: {
    discover_evidence_capabilities: "发现证据能力",
    search_business_records: "搜索业务记录",
    query_business_evidence: "受控证据查询",
  },
  event: {
    RUN_STARTED: "调查已启动", TOOL_STARTED: "正在查询", TOOL_COMPLETED: "证据已返回",
    VALIDATION_STARTED: "正在核验证据", REPORT_COMPLETED: "报告已保存", ERROR: "调查遇到问题",
  },
  dataset: {
    receivables: "应收", sales_payments: "销售与回款", extensions: "展期",
    credit: "授信", contracts: "合同", inventory: "库存", sales: "物料销售",
  },
};

export const priorityColor = (value) => ({ HIGH: "danger", MEDIUM: "warning", LOW: "neutral" }[value] || "neutral");
export const statusColor = (value) => ({ PENDING_AGENT_REVIEW: "brand", PENDING_HUMAN_REVIEW: "warning", ACTION_IN_PROGRESS: "danger", CLOSED: "success" }[value] || "brand");
export const stageColor = (value) => ({ DETERIORATING: "danger", EARLY_WARNING: "warning", LIMITED: "neutral" }[value] || "neutral");
export const hypothesisColor = (value) => ({ SUPPORTED: "success", WEAKENED: "neutral", UNRESOLVED: "warning" }[value] || "neutral");

export function formatMoney(value) {
  const number = Number(value || 0);
  if (Math.abs(number) >= 100000000) return `${(number / 100000000).toFixed(2)} 亿元`;
  if (Math.abs(number) >= 10000) return `${(number / 10000).toFixed(2)} 万元`;
  return `${number.toFixed(2)} 元`;
}

export const formatPercent = (value) => value == null ? "—" : `${(Number(value) * 100).toFixed(1)}%`;
export const metricMap = (result) => Object.fromEntries(result.rows.map(([name, value]) => [name, value]));

export function openCaseWorkspace(caseId, sourcePath) {
  const url = new URL(`/cases/${encodeURIComponent(caseId)}`, window.location.origin);
  url.searchParams.set("from", sourcePath);
  const detailWindow = window.open(url.toString(), "_blank");
  if (!detailWindow) throw new Error("浏览器阻止了新标签页，请允许本站打开新标签页后重试。");
}

export function queryArguments(item) {
  const args = item?.arguments || {};
  if (args.dataset) {
    const metrics = Array.isArray(args.metrics) ? args.metrics.join("、") : "";
    return `${labels.dataset[args.dataset] || args.dataset} / ${args.grain || "—"} · ${args.time_window || "—"}${metrics ? ` · ${metrics}` : ""}`;
  }
  return Object.entries(args).map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join("、") : value}`).join(" · ");
}

export async function api(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `请求失败（${response.status}）`);
  return payload;
}

export async function streamNdjson(path, options, onEvent) {
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error("无法连接本地调查服务，请确认服务正在运行后重试。");
  }
  if (!response.ok) {
    const payload = await response.json();
    throw new Error(payload.error || `请求失败（${response.status}）`);
  }
  if (!response.body) throw new Error("浏览器没有收到调查事件流。");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    let chunk;
    try {
      chunk = await reader.read();
    } catch {
      throw new Error("调查连接意外中断，请刷新案件后重新调查。");
    }
    const { value, done } = chunk;
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines.filter(Boolean)) onEvent(JSON.parse(line));
    if (done) break;
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer));
}
