<script setup>
import { computed } from "vue";
import { useRouter } from "vue-router";
import VueApexCharts from "vue3-apexcharts";
import { AlertTriangle, ArrowRight, ClipboardList, Clock, DatabaseBackup, Radar, Wallet } from "lucide-vue-next";
import Badge from "./ui/Badge.vue";
import { formatMoney, labels, statusColor } from "../lib";
import { workspace } from "../store";

const router = useRouter();
const overview = computed(() => workspace.overview);
const loading = computed(() => workspace.loading);
const priorityCases = computed(() => workspace.cases.slice(0, 5));

const ar = computed(() => workspace.overview?.cases_by_type?.ACCOUNTS_RECEIVABLE || 0);
const inventory = computed(() => workspace.overview?.cases_by_type?.INVENTORY || 0);
const total = computed(() => ar.value + inventory.value);
const arShare = computed(() => (total.value ? Math.round((ar.value / total.value) * 100) : 0));
const invShare = computed(() => (total.value ? Math.round((inventory.value / total.value) * 100) : 0));

const metrics = computed(() => [
  { label: "关键级案件", value: workspace.overview?.critical_cases ?? "—", note: "当前规则侧重早期预警", tone: "danger", icon: AlertTriangle },
  { label: "等待调查", value: workspace.overview?.open_cases ?? "—", note: "规则已经命中", tone: "brand", icon: ClipboardList },
  { label: "等待审核", value: workspace.overview?.pending_review_cases ?? "—", note: "Agent 已完成取证", tone: "warning", icon: Clock },
  { label: "风险敞口", value: workspace.overview ? formatMoney(workspace.overview.exposure_amount) : "—", note: "未关闭案件合计", tone: "success", icon: Wallet, compact: true },
]);
const toneIcon = {
  danger: "bg-danger-wash text-danger",
  brand: "bg-brand-wash text-brand-deep",
  warning: "bg-warning-wash text-warning-deep",
  success: "bg-success-wash text-success-deep",
};

const donutOptions = computed(() => ({
  chart: { type: "donut" },
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
    <section class="card flex items-center justify-between gap-6 border-brand/20 bg-brand-wash/50 px-6 py-5">
      <div>
        <span class="eyebrow inline-flex items-center gap-1.5"><Radar :size="13" /> 规则发现 · Agent 调查 · 人工审核</span>
        <h2 class="mt-2 text-[22px] font-bold text-ink">风险调查态势</h2>
        <p v-if="overview?.latest_run" class="mt-1 text-xs text-muted">
          规则集 {{ overview.latest_run.rule_set_version }} · 观察期 {{ overview.latest_run.observation_date }} · 命中 {{ overview.latest_run.rule_hits }} 条规则
        </p>
        <p v-else class="mt-1 text-xs text-muted">尚未执行规则扫描，请点击右上角"重新扫描"。</p>
      </div>
      <div class="min-w-[130px] border-l border-brand/20 pl-5">
        <strong class="block text-4xl leading-none text-brand">{{ overview?.total_cases ?? "—" }}</strong>
        <span class="mt-1 block text-[11px] text-muted">当前风险案件</span>
      </div>
    </section>

    <div class="grid grid-cols-2 gap-4 xl:grid-cols-4">
      <section v-for="m in metrics" :key="m.label" class="card min-h-[148px] p-5">
        <span class="mb-4 grid h-10 w-10 place-items-center rounded-lg" :class="toneIcon[m.tone]">
          <component :is="m.icon" :size="20" />
        </span>
        <span class="block text-xs text-muted">{{ m.label }}</span>
        <strong class="mt-1 block leading-tight text-ink" :class="m.compact ? 'text-[19px]' : 'text-[25px]'">{{ m.value }}</strong>
        <small class="mt-1 block text-[11px] text-faint">{{ m.note }}</small>
      </section>
    </div>

    <div class="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.45fr)_minmax(340px,0.75fr)]">
      <section class="card">
        <div class="panel-head">
          <div class="flex items-center gap-2"><span class="section-index">A</span><h3>优先调查</h3></div>
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
              <small class="mt-0.5 block max-w-[650px] truncate text-xs text-muted">{{ item.summary }}</small>
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
        <div class="panel-head"><div class="flex items-center gap-2"><span class="section-index">B</span><h3>案件构成</h3></div></div>
        <div class="px-5 pt-3">
          <VueApexCharts type="donut" height="210" :options="donutOptions" :series="donutSeries" />
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
        <div class="m-4 mt-5 rounded-lg bg-canvas p-3.5">
          <div class="flex gap-2.5">
            <DatabaseBackup :size="18" class="mt-0.5 flex-none text-muted" />
            <div>
              <strong class="text-xs text-ink">数据边界</strong>
              <p class="mt-0.5 text-[11px] leading-5 text-muted">库存为公司仓库库存；当前数据不包含促销、下游库存、银行未核销和项目验收记录。</p>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
