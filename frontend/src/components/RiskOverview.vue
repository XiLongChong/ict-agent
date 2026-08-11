<script setup>
import { computed } from "vue";
import { useRouter } from "vue-router";
import VueApexCharts from "vue3-apexcharts";
import { ArrowRight, CircleDollarSign, ClipboardCheck, Search, ShieldAlert } from "lucide-vue-next";
import Badge from "./ui/Badge.vue";
import { formatMoney, labels, statusColor } from "../lib";
import { workspace } from "../store";
import { useResponsiveChart } from "../composables/useResponsiveChart";

const router = useRouter();
const { chartRef: donutChartRef, chartHostRef: donutHostRef } = useResponsiveChart();
const loading = computed(() => workspace.loading);
const priorityCases = computed(() => workspace.cases.slice(0, 5));

const ar = computed(() => workspace.overview?.cases_by_type?.ACCOUNTS_RECEIVABLE || 0);
const inventory = computed(() => workspace.overview?.cases_by_type?.INVENTORY || 0);
const total = computed(() => ar.value + inventory.value);
const arShare = computed(() => (total.value ? Math.round((ar.value / total.value) * 100) : 0));
const invShare = computed(() => (total.value ? Math.round((inventory.value / total.value) * 100) : 0));

const metrics = computed(() => [
  { label: "案件总数", value: workspace.overview?.total_cases ?? "—", tone: "brand", icon: ShieldAlert },
  { label: "待 Agent 调查", value: workspace.overview?.pending_agent_cases ?? "—", tone: "warning", icon: Search },
  { label: "待人工复核", value: workspace.overview?.pending_human_review_cases ?? "—", tone: "success", icon: ClipboardCheck },
  { label: "风险敞口", value: workspace.overview ? formatMoney(workspace.overview.exposure_amount) : "—", tone: "danger", icon: CircleDollarSign, compact: true },
]);
const toneIcon = {
  danger: "bg-danger-wash text-danger",
  brand: "bg-brand-wash text-brand-deep",
  warning: "bg-warning-wash text-warning-deep",
  success: "bg-success-wash text-success-deep",
};

const donutOptions = computed(() => ({
  chart: { type: "donut", animations: { enabled: false }, redrawOnParentResize: false, redrawOnWindowResize: false },
  labels: ["客户应收", "库存积压"],
  colors: ["#465fff", "#039855"],
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
          total: { show: true, label: "案件总数", fontSize: "12px", fontWeight: 500, color: "#98a2b3" },
        },
      },
    },
  },
}));
const donutSeries = computed(() => [ar.value, inventory.value]);

const barTone = {
  CRITICAL: "bg-danger",
  HIGH: "bg-warning",
  MEDIUM: "bg-brand",
  LOW: "bg-gray-200",
};

function openCase(caseId) {
  router.push(`/cases/${encodeURIComponent(caseId)}`);
}
function showCases() {
  router.push("/cases");
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
      <section class="card">
        <div class="panel-head">
          <h3>优先调查</h3>
          <button type="button" @click="showCases" class="inline-flex items-center gap-1 text-sm font-semibold text-brand hover:text-brand-dark">
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
              <span class="mt-0.5 block max-w-[650px] truncate text-[13px] text-muted">{{ item.summary }}</span>
            </span>
            <span class="text-right">
              <strong class="block text-[13px] text-ink">{{ formatMoney(item.exposure_amount) }}</strong>
              <Badge class="mt-1" :tone="statusColor(item.status)">{{ labels.status[item.status] }}</Badge>
            </span>
          </button>
          <div v-if="!loading && !priorityCases.length" class="empty-state">尚无风险案件</div>
        </div>
      </section>

      <section class="card pb-4">
        <div class="panel-head"><h3>案件构成</h3></div>
        <div ref="donutHostRef" class="px-5 pt-3">
          <VueApexCharts ref="donutChartRef" type="donut" height="210" :options="donutOptions" :series="donutSeries" />
        </div>
        <div class="space-y-4 px-5 pt-1">
          <div>
            <div class="mb-2 flex justify-between text-[13px]">
              <span class="flex items-center gap-2 text-muted"><i class="h-2.5 w-2.5 rounded-sm bg-brand"></i>客户应收调查</span>
              <strong class="text-ink">{{ ar }} 件</strong>
            </div>
            <div class="h-2 rounded-full bg-gray-100"><div class="h-2 rounded-full bg-brand" :style="{ width: arShare + '%' }"></div></div>
          </div>
          <div>
            <div class="mb-2 flex justify-between text-[13px]">
              <span class="flex items-center gap-2 text-muted"><i class="h-2.5 w-2.5 rounded-sm bg-success"></i>库存积压调查</span>
              <strong class="text-ink">{{ inventory }} 件</strong>
            </div>
            <div class="h-2 rounded-full bg-gray-100"><div class="h-2 rounded-full bg-success" :style="{ width: invShare + '%' }"></div></div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
