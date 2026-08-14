<script setup>
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import VueApexCharts from "vue3-apexcharts";
import {
  ArrowRight,
  ClipboardCheck,
  FileWarning,
  ListChecks,
  ListTodo,
  Newspaper,
  ShieldAlert,
} from "lucide-vue-next";
import Badge from "./ui/Badge.vue";
import {
  formatMoney,
  labels,
  listColor,
  openCaseWorkspace,
  priorityColor,
  recommendationStatusColor,
  statusColor,
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

// 待办事项合并（名单建议 + 预警），只展示前 5 条；剩余跳转名单管理
const TODO_LIMIT = 5;
const todoItems = computed(() => {
  const items = [
    ...pendingRecommendations.value.map((item) => ({
      key: `rec_${item.recommendation_id}`,
      kind: "recommendation",
      label: item.subject_label,
      sub: [
        labels.list[item.current_list] || item.current_list,
        "→",
        labels.list[item.target_list] || item.target_list,
        item.health_change,
      ].join(" "),
      badgeText: labels.recommendationStatus[item.status] || item.status,
      badgeTone: recommendationStatusColor(item.status),
      barTone: "bg-danger",
      amountText: "",
    })),
    ...openAlerts.value.map((alert) => ({
      key: `alert_${alert.alert_id}`,
      kind: "alert",
      label: alert.subject_label,
      sub: [
        labels.severity[alert.severity] || alert.severity,
        labels.alertType[alert.alert_type] || alert.alert_type,
        alert.message,
      ].join(" · "),
      badgeText: "",
      badgeTone: "",
      barTone: alert.severity === "CRITICAL" || alert.severity === "HIGH" ? "bg-danger" : "bg-warning",
      amountText: formatMoney(alert.risk_amount),
    })),
  ];
  return { visible: items.slice(0, TODO_LIMIT), total: items.length };
});

const metrics = computed(() => [
  { label: "待事前评估", value: warning.value.pre_assessment_pending ?? "—", tone: "brand", icon: ClipboardCheck },
  { label: "待处理案件", value: pendingCases.value, tone: "warning", icon: ListTodo },
  { label: "健康度下降", value: warning.value.health_drop_count ?? "—", tone: "orange", icon: FileWarning },
  { label: "待审批名单", value: warning.value.pending_list_recommendations ?? "—", tone: "danger", icon: ListChecks },
  { label: "未处理舆情", value: warning.value.open_sentiments ?? "—", tone: "warning", icon: Newspaper },
  { label: "健康度高危", value: warning.value.high_risk_count ?? "—", tone: "danger", icon: ShieldAlert },
]);

// 待处理案件 = 案件队列中未关闭的案件数（待调查 + 待复核 + 处理中）
const pendingCases = computed(() => {
  const o = workspace.overview;
  if (!o) return "—";
  const total = o.total_cases ?? 0;
  const closed = o.closed_cases ?? 0;
  return total - closed;
});
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
  labels: ["健康", "关注", "预警", "高危"],
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
    <div class="grid grid-cols-2 gap-4 xl:grid-cols-3">
      <section v-for="m in metrics" :key="m.label" class="card min-h-[132px] p-5">
        <span class="mb-3 grid h-10 w-10 place-items-center rounded-lg" :class="toneIcon[m.tone]">
          <component :is="m.icon" :size="20" />
        </span>
        <span class="block text-sm font-medium text-muted">{{ m.label }}</span>
        <strong class="mt-1 block leading-tight text-ink" :class="m.compact ? 'text-[1.1875rem]' : 'text-[1.5625rem]'">{{ m.value }}</strong>
      </section>
    </div>

    <div class="grid grid-cols-1 gap-4 xl:grid-cols-3">
      <!-- 待办列表：待审批名单 + 未处理预警（限 5 条） -->
      <section class="card xl:col-span-2">
        <div class="panel-head">
          <h3>待办事项</h3>
          <button type="button" @click="go('/lists')" class="inline-flex items-center gap-1 text-sm font-semibold text-brand hover:text-brand-dark">
            名单管理 <ArrowRight :size="15" />
          </button>
        </div>
        <div class="px-2.5 py-2">
          <div v-for="item in todoItems.visible" :key="item.key" class="grid grid-cols-[3px_minmax(0,1fr)_auto] items-center gap-3 rounded-md px-2 py-2.5">
            <span class="h-full w-[3px] rounded" :class="item.barTone"></span>
            <span class="min-w-0">
              <strong class="block truncate text-[0.8125rem] text-ink">{{ item.label }}</strong>
              <span class="mt-1 flex flex-wrap items-center gap-1.5 text-[0.75rem] text-muted">
                <Badge v-if="item.kind === 'recommendation'" :tone="listColor(item.current_list)">{{ labels.list[item.current_list] }}</Badge>
                <span class="truncate">{{ item.sub }}</span>
              </span>
            </span>
            <Badge v-if="item.badgeText" :tone="item.badgeTone">{{ item.badgeText }}</Badge>
            <span v-else class="text-right text-[0.8125rem] text-ink">{{ item.amountText }}</span>
          </div>

          <div v-if="!loading && !todoItems.total" class="empty-state">暂无待办事项</div>
        </div>
      </section>

      <!-- 健康度分布 -->
      <section class="card pb-4 xl:col-span-1">
        <div class="panel-head"><h3>健康度分布</h3></div>
        <div ref="donutHostRef" class="px-5 pt-3">
          <VueApexCharts ref="donutChartRef" type="donut" height="210" :options="donutOptions" :series="gradeSeries" />
        </div>
        <div class="space-y-3 px-5 pt-1">
          <div v-for="(tone, grade) in { HEALTHY: 'bg-success', WATCH: 'bg-warning', WARNING: 'bg-[#f97316]', HIGH_RISK: 'bg-danger' }" :key="grade" class="flex items-center justify-between text-[0.8125rem]">
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
            <strong class="block text-[0.8125rem] text-ink">{{ item.entity_label }}</strong>
            <span class="mt-1 flex max-w-[650px] items-center gap-1.5 overflow-hidden">
              <Badge :tone="priorityColor(item.priority)">{{ labels.priority[item.priority] }}风险</Badge>
              <Badge tone="neutral">{{ labels.caseType[item.case_type] }}</Badge>
              <span class="min-w-0 truncate text-[0.75rem] text-muted">{{ item.risk_overview }}</span>
            </span>
          </span>
          <span class="text-right">
            <strong class="block text-[0.8125rem] text-ink">{{ formatMoney(item.exposure_amount) }}</strong>
            <Badge class="mt-1" :tone="statusColor(item.status)">{{ labels.status[item.status] }}</Badge>
          </span>
        </button>
        <div v-if="!loading && !priorityCases.length" class="empty-state">尚无风险案件</div>
      </div>
    </section>
  </div>
</template>
