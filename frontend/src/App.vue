<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useDisplay } from "vuetify";
import RiskOverview from "./components/RiskOverview.vue";
import CaseQueue from "./components/CaseQueue.vue";
import BusinessView from "./components/BusinessView.vue";
import CaseWorkspace from "./components/CaseWorkspace.vue";
import { api } from "./lib";

const views = [
  { id: "risk", label: "风险总览", icon: "mdi-view-dashboard-outline" },
  { id: "cases", label: "案件队列", icon: "mdi-format-list-bulleted-square" },
  { id: "business", label: "经营分析", icon: "mdi-chart-line" },
];
const activeView = ref("risk");
const { mdAndUp, smAndDown } = useDisplay();
const cases = ref([]);
const overview = ref(null);
const business = ref(null);
const loading = ref(true);
const scanning = ref(false);
const status = ref({ text: "正在连接数据", error: false });
const activeCase = ref(null);
const caseOpen = ref(false);
const mobileNav = ref(false);
const sidebarExpanded = ref(true);
watch(mdAndUp, (isDesktop) => { mobileNav.value = isDesktop; }, { immediate: true });

const page = computed(() => views.find((item) => item.id === activeView.value));

async function loadRiskData() {
  const [riskOverview, caseList] = await Promise.all([api("/api/v1/risk/overview"), api("/api/v1/cases")]);
  overview.value = riskOverview;
  cases.value = caseList;
}

async function loadAll() {
  loading.value = true;
  try {
    const [, businessData] = await Promise.all([loadRiskData(), api("/api/v1/overview")]);
    business.value = businessData;
    status.value = { text: "数据与案件已就绪", error: false };
  } catch (error) {
    status.value = { text: error.message, error: true };
  } finally {
    loading.value = false;
  }
}

async function runScan() {
  scanning.value = true;
  try {
    const result = await api("/api/v1/rule-runs", { method: "POST" });
    await loadRiskData();
    status.value = { text: `扫描完成 · ${result.cases_detected} 个案件`, error: false };
  } catch (error) {
    status.value = { text: error.message, error: true };
  } finally {
    scanning.value = false;
  }
}

async function openCase(caseId) {
  activeCase.value = null;
  caseOpen.value = true;
  try {
    activeCase.value = await api(`/api/v1/cases/${encodeURIComponent(caseId)}`);
  } catch (error) {
    status.value = { text: error.message, error: true };
    caseOpen.value = false;
  }
}

async function refreshCase() {
  if (!activeCase.value) return;
  activeCase.value = await api(`/api/v1/cases/${encodeURIComponent(activeCase.value.case_id)}`);
  await loadRiskData();
}

function navigate(view) {
  activeView.value = view;
  if (smAndDown.value) mobileNav.value = false;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function toggleNavigation() {
  if (mdAndUp.value) sidebarExpanded.value = !sidebarExpanded.value;
  else mobileNav.value = !mobileNav.value;
}

onMounted(loadAll);
</script>

<template>
  <v-app class="admin-workspace">
    <v-navigation-drawer v-model="mobileNav" class="app-sidebar" :permanent="mdAndUp" :rail="mdAndUp && !sidebarExpanded" :rail-width="88" :width="260">
      <div class="brand-block">
        <span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i></span>
        <div v-show="!mdAndUp || sidebarExpanded" class="brand-copy"><strong>佳华智审</strong><small>风险调查工作台</small></div>
      </div>
      <span v-show="!mdAndUp || sidebarExpanded" class="nav-caption">工作台</span>
      <v-list class="nav-list" nav density="compact">
        <v-list-item v-for="item in views" :key="item.id" :active="activeView === item.id" :prepend-icon="item.icon" :title="item.label" @click="navigate(item.id)" />
      </v-list>
      <template #append>
        <div v-show="!mdAndUp || sidebarExpanded" class="sidebar-boundary">
          <span class="status-dot"></span>
          <div><strong>只读调查模式</strong><small>Agent 不执行自动业务处置</small></div>
        </div>
      </template>
    </v-navigation-drawer>

    <v-app-bar class="app-topbar" flat height="72">
      <v-btn class="nav-toggle" icon="mdi-menu" variant="outlined" aria-label="收起、展开或打开导航" @click="toggleNavigation" />
      <v-spacer />
      <div class="system-state" :class="{ error: status.error }"><span></span>{{ status.text }}</div>
      <v-btn color="primary" variant="outlined" prepend-icon="mdi-radar" :loading="scanning" @click="runScan">重新扫描</v-btn>
    </v-app-bar>

    <v-main>
      <div class="page-content">
        <div class="page-heading"><span>佳华智审 / {{ page.label }}</span><h1>{{ page.label }}</h1></div>
        <RiskOverview v-if="activeView === 'risk'" :overview="overview" :cases="cases" :loading="loading" @open-case="openCase" @show-cases="navigate('cases')" />
        <CaseQueue v-else-if="activeView === 'cases'" :cases="cases" :loading="loading" @open-case="openCase" />
        <BusinessView v-else :data="business" :loading="loading" />
      </div>
    </v-main>

    <CaseWorkspace v-model="caseOpen" :case-item="activeCase" @refresh="refreshCase" />
    <v-snackbar v-model="status.error" color="error" timeout="6000">{{ status.text }}</v-snackbar>
  </v-app>
</template>
