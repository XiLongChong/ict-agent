<script setup>
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import VueApexCharts from "vue3-apexcharts";
import {
  Activity,
  ArrowRight,
  CircleDollarSign,
  ClipboardCheck,
  FileWarning,
  ListChecks,
  Newspaper,
  ShieldAlert,
} from "lucide-vue-next";
import Badge from "./ui/Badge.vue";
import {
  formatMoney,
  formatMoneyWan,
  gradeColor,
  labels,
  listColor,
  openCaseWorkspace,
  priorityColor,
  recommendationStatusColor,
  severityColor,
  statusColor,
  verifyStatusColor,
} from "../lib";
import { workspace } from "../store";
import { useResponsiveChart } from "../composables/useResponsiveChart";

const router = useRouter();
const route = useRoute();
const { chartRef: donutChartRef, chartHostRef: donutHostRef } = useResponsiveChart();
const loading = computed(() => workspace.loading);
const priorityCases = computed(() => {
  const cases = workspace.cases || [];
  // 按案件类型交替取高优先案件，避免列表被单一类型（如黑名单应收）淹没
  const byType = {
    ACCOUNTS_RECEIVABLE: cases.filter((c) => c.case_type === "ACCOUNTS_RECEIVABLE"),
    INVENTORY: cases.filter((c) => c.case_type === "INVENTORY"),
  };
  const result = [];
  const ar = byType.ACCOUNTS_RECEIVABLE || [];
  const inv = byType.INVENTORY || [];
  for (let i = 0; i < 5; i += 1) {
    if (i % 2 === 0 && ar[i / 2]) result.push(ar[i / 2]);
    else if (inv[Math.floor(i / 2)]) result.push(inv[Math.floor(i / 2)]);
  }
  // 补足不足 5 条
  for (const item of cases) {
    if (result.length >= 5) break;
    if (!result.includes(item)) result.push(item);
  }
  return result.slice(0, 5);
});

const warning = computed(() => workspace.warningOverview || {});
const gradeDistribution = computed(() => warning.value.grade_distribution || {});
const pendingRecommendations = computed(() => warning.value.pending_recommendations || []);
const openAlerts = computed(() => warning.value.open_alerts || []);

const metrics = computed(() => [
  { label: "待事前评估", value: warning.value.pre_assessment_pending ?? "—", tone: "brand", icon: ClipboardCheck },
  { label: "事中预警", value: warning.value.in_process_alerts ?? "—", tone: "warning", icon: Activity },
  { label: "健康度下降", value: warning.value.health_drop_count ?? "—", tone: "orange", icon: FileWarning },
  { label: "待审批名单", value: warning.value.pending_list_recommendations ?? "—", tone: "danger", icon: ListChecks },
  { label: "未处理舆情", value: warning.value.open_sentiments ?? "—", tone: "warning", icon: Newspaper },
  { label: "高风险客户", value: warning.value.high_risk_count ?? "—", tone: "danger", icon: ShieldAlert },
  { label: "风险敞口", value: warning.value.risk_exposure != null ? formatMoneyWan(warning.value.risk_exposure) : "—", tone: "danger", icon: CircleDollarSign, compact: true },
]);
const toneIcon = {
  danger: "bg-danger-wash text-danger",
  brand: "bg-brand-wash text-brand-deep",
  warning: "bg-warning-wash text-warning-deep",
  orange: "bg-[#fff7ed] text-[#c2410c]",
  success: "bg-success-wash text-success-deep",
};

const gradeSeries = computed(() => [
  gradeDistribution.value.HEALTHY || 0,
  gradeDistribution.value.WATCH || 0,
  gradeDistribution.value.WARNING || 0,
  gradeDistribution.value.HIGH_RISK || 0,
]);
const donutOptions = computed(() => ({
  chart: { type: "donut", animations: { enabled: false }, redrawOnParentResize: false, redrawOnWindowResize: false },
  labels: ["健康", "关注", "预警", "高风险"],
  colors: ["#039855", "#f79009", "#f97316", "#d92d20"],
  stroke: { width: 0 },
  dataLabels: { enabled: false },
  legend: { show: false },
  plotOptions: {
    pie: {
      donut: {
        size: "78%",
        labels: {
          show: true,
          name: { show: false },
          value: { show: true, fontSize: "26px", fontWeight: 700, color: "#101828" },
          total: { show: true, label: "健康度主体", fontSize: "12px", fontWeight: 500, color: "#98a2b3" },
        },
      },
    },
  },
}));

const barTone = { HIGH: "bg-danger", MEDIUM: "bg-warning", LOW: "bg-gray-300" };

function openCase(caseId) {
  try {
    openCaseWorkspace(caseId, route.fullPath);
  } catch (exception) {
    workspace.status = { text: exception.message, error: true };
  }
}
function go(path) {
  router.push(path);
}
</script>

<template>
  <div class="space-y-5">
    <div class="grid grid-cols-2 gap-4 xl:grid-cols-4">
      <section v-for="m in metrics" :key="m.label" class="card min-h-[132px] p-5">
        <span class="mb-3 grid h-10 w-10 place-items-center rounded-lg" :class="toneIcon[m.tone]">
          <component :is="m.icon" :size="20" />
        </span>
        <span class="block text-sm font-medium text-muted">{{ m.label }}</span>
        <strong class="mt-1 block leading-tight text-ink" :class="m.compact ? 'text-[19px]' : 'text-[25px]'">{{ m.value }}</strong>
      </section>
    </div>

    <div class="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.45fr)_minmax(340px,0.75fr)]">
      <!-- 待办列表：待审批名单 + 未处理预警 -->
      <section class="card">
        <div class="panel-head">
          <h3>待办事项</h3>
          <button type="button" @click="go('/lists')" class="inline-flex items-center gap-1 text-sm font-semibold text-brand hover:text-brand-dark">
            名单管理 <ArrowRight :size="15" />
          </button>
        </div>
        <div class="px-2.5 py-2">
          <div v-for="item in pendingRecommendations" :key="item.recommendation_id" class="grid grid-cols-[3px_minmax(0,1fr)_auto] items-center gap-3 rounded-md px-2 py-3">
            <span class="h-full w-[3px] rounded bg-danger"></span>
            <span>
              <strong class="block text-[13px] text-ink">{{ item.subject_label }}</strong>
              <span class="mt-1 flex flex-wrap items-center gap-1.5 text-[12px] text-muted">
                <Badge :tone="listColor(item.current_list)">{{ labels.list[item.current_list] }}</Badge>
                <span>→</span>
                <Badge :tone="listColor(item.target_list)">{{ labels.list[item.target_list] }}</Badge>
                <span class="ml-1">{{ item.health_change }}</span>
              </span>
            </span>
            <Badge :tone="recommendationStatusColor(item.status)">{{ labels.recommendationStatus[item.status] }}</Badge>
          </div>

          <div v-for="alert in openAlerts" :key="alert.alert_id" class="grid grid-cols-[3px_minmax(0,1fr)_auto] items-center gap-3 rounded-md px-2 py-3">
            <span class="h-full w-[3px] rounded" :class="alert.severity === 'CRITICAL' || alert.severity === 'HIGH' ? 'bg-danger' : 'bg-warning'"></span>
            <span>
              <strong class="block text-[13px] text-ink">{{ alert.subject_label }}</strong>
              <span class="mt-1 flex flex-wrap items-center gap-1.5 text-[12px] text-muted">
                <Badge :tone="severityColor(alert.severity)">{{ labels.severity[alert.severity] || alert.severity }}</Badge>
                <Badge tone="neutral">{{ labels.alertType[alert.alert_type] || alert.alert_type }}</Badge>
                <span class="truncate">{{ alert.message }}</span>
              </span>
            </span>
            <span class="text-right text-[13px] text-ink">{{ formatMoney(alert.risk_amount) }}</span>
          </div>

          <div v-if="!loading && !pendingRecommendations.length && !openAlerts.length" class="empty-state">暂无待办事项</div>
        </div>
      </section>

      <!-- 健康度分布 -->
      <section class="card pb-4">
        <div class="panel-head"><h3>健康度分布</h3></div>
        <div ref="donutHostRef" class="px-5 pt-3">
          <VueApexCharts ref="donutChartRef" type="donut" height="210" :options="donutOptions" :series="gradeSeries" />
        </div>
        <div class="space-y-3 px-5 pt-1">
          <div v-for="(tone, grade) in { HEALTHY: 'bg-success', WATCH: 'bg-warning', WARNING: 'bg-[#f97316]', HIGH_RISK: 'bg-danger' }" :key="grade" class="flex items-center justify-between text-[13px]">
            <span class="flex items-center gap-2 text-muted"><i class="h-2.5 w-2.5 rounded-sm" :class="tone"></i>{{ labels.grade[grade] }}</span>
            <strong class="text-ink">{{ gradeDistribution[grade] || 0 }}</strong>
          </div>
        </div>
      </section>
    </div>

    <!-- 优先调查 -->
    <section class="card">
      <div class="panel-head">
        <h3>优先调查</h3>
        <button type="button" @click="go('/cases')" class="inline-flex items-center gap-1 text-sm font-semibold text-brand hover:text-brand-dark">
          查看全部 <ArrowRight :size="15" />
        </button>
      </div>
      <div class="px-2.5 py-2">
        <button
          v-for="item in priorityCases"
          :key="item.case_id"
          type="button"
          @click="openCase(item.case_id)"
          class="grid w-full grid-cols-[3px_minmax(0,1fr)_auto] items-center gap-3 rounded-md px-2 py-3 text-left transition-colors hover:bg-canvas"
        >
          <span class="h-full w-[3px] rounded" :class="barTone[item.priority] || 'bg-gray-200'"></span>
          <span>
            <strong class="block text-[13px] text-ink">{{ item.entity_label }}</strong>
            <span class="mt-1 flex max-w-[650px] items-center gap-1.5 overflow-hidden">
              <Badge :tone="priorityColor(item.priority)">{{ labels.priority[item.priority] }}风险</Badge>
              <Badge tone="neutral">{{ labels.caseType[item.case_type] }}</Badge>
              <span class="min-w-0 truncate text-[12px] text-muted">{{ item.risk_overview }}</span>
            </span>
          </span>
          <span class="text-right">
            <strong class="block text-[13px] text-ink">{{ formatMoney(item.exposure_amount) }}</strong>
            <Badge class="mt-1" :tone="statusColor(item.status)">{{ labels.status[item.status] }}</Badge>
          </span>
        </button>
        <div v-if="!loading && !priorityCases.length" class="empty-state">尚无风险案件</div>
      </div>
    </section>
  </div>
</template>
