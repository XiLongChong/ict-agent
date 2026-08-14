import { reactive } from "vue";
import { api } from "./lib";

export const workspace = reactive({
  cases: [],
  overview: null,
  business: null,
  warningOverview: null,
  healthScores: [],
  recommendations: [],
  alerts: [],
  sentiments: [],
  projects: [],
  loading: true,
  scanning: false,
  status: { text: "正在连接数据", error: false },
});

export function resetWorkspaceStatus() {
  if (workspace.loading || workspace.scanning) return;
  workspace.status = { text: "数据与案件已就绪", error: false };
}

export async function loadRiskData() {
  const [riskOverview, caseList] = await Promise.all([api("/api/v1/risk/overview"), api("/api/v1/cases")]);
  workspace.overview = riskOverview;
  workspace.cases = caseList;
}

const warningEndpoints = [
  ["warningOverview", "/api/v1/warning/overview"],
  ["healthScores", "/api/v1/health-scores"],
  ["recommendations", "/api/v1/list-recommendations"],
  ["alerts", "/api/v1/alerts"],
  ["sentiments", "/api/v1/sentiments"],
  ["projects", "/api/v1/projects"],
];

export async function loadWarningData() {
  const results = await Promise.all(
    warningEndpoints.map(async ([, path]) => {
      try {
        return await api(path);
      } catch {
        return undefined; // 失败不阻塞已有数据
      }
    })
  );
  warningEndpoints.forEach(([key], index) => {
    if (results[index] !== undefined) workspace[key] = results[index];
  });
  return results.some((item) => item !== undefined);
}

export async function loadAll() {
  workspace.loading = true;
  try {
    const [, businessData] = await Promise.all([loadRiskData(), api("/api/v1/overview")]);
    workspace.business = businessData;
    await loadWarningData();
    workspace.status = { text: "数据与案件已就绪", error: false };
  } catch (error) {
    workspace.status = { text: error.message, error: true };
  } finally {
    workspace.loading = false;
  }
}

async function refresh(key) {
  try {
    const data = await api(warningEndpoints.find(([k]) => k === key)[1]);
    workspace[key] = data;
  } catch {
    // 刷新失败保留已有数据
  }
}

export async function runScan() {
  workspace.scanning = true;
  try {
    const result = await api("/api/v1/rule-runs", { method: "POST" });
    await Promise.all([loadRiskData(), loadWarningData()]);
    workspace.status = { text: `扫描完成 · ${result.cases_detected} 个案件`, error: false };
  } catch (error) {
    workspace.status = { text: error.message, error: true };
  } finally {
    workspace.scanning = false;
  }
}

export async function recalcHealth() {
  try {
    const result = await api("/api/v1/health-scores/recalculate", { method: "POST" });
    await refresh("healthScores");
    workspace.status = { text: `健康度已重新计算 · ${result.count} 个主体`, error: false };
    return result;
  } catch (error) {
    workspace.status = { text: error.message, error: true };
    throw error;
  }
}

export async function reviewRecommendation(id, body) {
  try {
    const updated = await api(`/api/v1/list-recommendations/${id}/reviews`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    await Promise.all([refresh("recommendations"), refresh("warningOverview")]);
    workspace.status = { text: "名单建议已审批", error: false };
    return updated;
  } catch (error) {
    workspace.status = { text: error.message, error: true };
    throw error;
  }
}

export async function acknowledgeAlert(id) {
  try {
    await api(`/api/v1/alerts/${id}/acknowledge`, { method: "POST" });
    await Promise.all([refresh("alerts"), refresh("warningOverview")]);
    workspace.status = { text: "预警已确认", error: false };
  } catch (error) {
    workspace.status = { text: error.message, error: true };
    throw error;
  }
}

export async function verifySentiment(id, body) {
  try {
    const updated = await api(`/api/v1/sentiments/${id}/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    await Promise.all([refresh("sentiments"), refresh("warningOverview")]);
    workspace.status = { text: "舆情已核验", error: false };
    return updated;
  } catch (error) {
    workspace.status = { text: error.message, error: true };
    throw error;
  }
}

export async function runPreAssessment(id) {
  try {
    const result = await api(`/api/v1/projects/${id}/pre-assessment/run`, { method: "POST" });
    await refresh("projects");
    workspace.status = { text: "事前评估完成", error: false };
    return result;
  } catch (error) {
    workspace.status = { text: error.message, error: true };
    throw error;
  }
}
