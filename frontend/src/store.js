import { reactive } from "vue";
import { api } from "./lib";

export const workspace = reactive({
  cases: [],
  overview: null,
  business: null,
  session: { authenticated: false, actor_type: "WEB_VISITOR", display_name: "网页访客" },
  loading: true,
  scanning: false,
  status: { text: "", error: false },
});

export async function loadRiskData() {
  const [riskOverview, caseList] = await Promise.all([api("/api/v1/risk/overview"), api("/api/v1/cases")]);
  workspace.overview = riskOverview;
  workspace.cases = caseList;
}

export async function loadAll() {
  workspace.loading = true;
  try {
    const [, businessData, session] = await Promise.all([
      loadRiskData(),
      api("/api/v1/overview"),
      api("/api/v1/session"),
    ]);
    workspace.business = businessData;
    workspace.session = session;
  } catch (error) {
    workspace.status = { text: error.message, error: true };
  } finally {
    workspace.loading = false;
  }
}

export async function runScan() {
  workspace.scanning = true;
  try {
    await api("/api/v1/rule-runs", { method: "POST" });
    await loadRiskData();
  } catch (error) {
    workspace.status = { text: error.message, error: true };
  } finally {
    workspace.scanning = false;
  }
}
