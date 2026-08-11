import { reactive } from "vue";
import { api } from "./lib";

export const workspace = reactive({
  cases: [],
  overview: null,
  business: null,
  loading: true,
  scanning: false,
  status: { text: "正在连接数据", error: false },
});

export async function loadRiskData() {
  const [riskOverview, caseList] = await Promise.all([api("/api/v1/risk/overview"), api("/api/v1/cases")]);
  workspace.overview = riskOverview;
  workspace.cases = caseList;
}

export async function loadAll() {
  workspace.loading = true;
  try {
    const [, businessData] = await Promise.all([loadRiskData(), api("/api/v1/overview")]);
    workspace.business = businessData;
    workspace.status = { text: "数据与案件已就绪", error: false };
  } catch (error) {
    workspace.status = { text: error.message, error: true };
  } finally {
    workspace.loading = false;
  }
}

export async function runScan() {
  workspace.scanning = true;
  try {
    const result = await api("/api/v1/rule-runs", { method: "POST" });
    await loadRiskData();
    workspace.status = { text: `扫描完成 · ${result.cases_detected} 个案件`, error: false };
  } catch (error) {
    workspace.status = { text: error.message, error: true };
  } finally {
    workspace.scanning = false;
  }
}
