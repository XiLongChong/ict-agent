<script setup>
import { computed, ref } from "vue";
import VueApexCharts from "vue3-apexcharts";
import { AlertCircle, ChartLine, HandCoins, Wallet } from "lucide-vue-next";
import { formatMoney, formatPercent, metricMap } from "../lib";
import { workspace } from "../store";
import { useResponsiveChart } from "../composables/useResponsiveChart";

const { chartRef: trendChartRef, chartHostRef: trendHostRef } = useResponsiveChart();
const range = ref("12");
const rangeOptions = [
  { label: "近 6 个月", value: "6" },
  { label: "近 12 个月", value: "12" },
  { label: "全部", value: "all" },
];

const cards = computed(() => {
  if (!workspace.business) return [];
  const overview = metricMap(workspace.business.overview);
  const ar = metricMap(workspace.business.latest_ar);
  return [
    { label: "累计销售额", value: formatMoney(overview["销售额"]), tone: "brand", icon: ChartLine },
    { label: "累计回款额", value: formatMoney(overview["回款额"]), tone: "success", icon: HandCoins },
    { label: "最新应收余额", value: formatMoney(ar["应收余额"]), tone: "warning", icon: Wallet },
    { label: "最新超期率", value: formatPercent(ar["超期率"]), tone: "danger", icon: AlertCircle },
  ];
});
const toneIcon = {
  brand: "bg-brand-wash text-brand-deep",
  success: "bg-success-wash text-success-deep",
  warning: "bg-warning-wash text-warning-deep",
  danger: "bg-danger-wash text-danger",
};
const allTrend = computed(() => workspace.business?.ar_trend?.rows || []);
const trend = computed(() => (range.value === "all" ? allTrend.value : allTrend.value.slice(-Number(range.value))));
const trendCategories = computed(() => trend.value.map((r) => String(r[0]).slice(0, 7)));
const trendSeries = computed(() => [
  { name: "应收余额", data: trend.value.map((r) => Number(r[1])) },
  { name: "超期应收", data: trend.value.map((r) => Number(r[2])) },
]);
function axisMoney(value) {
  const n = Number(value);
  if (Math.abs(n) >= 100000000) return `${(n / 100000000).toFixed(1)}亿`;
  if (Math.abs(n) >= 10000) return `${(n / 10000).toFixed(0)}万`;
  return String(n);
}
const trendOptions = computed(() => ({
  chart: {
    type: "area",
    animations: { enabled: false },
    toolbar: { show: false },
    fontFamily: "DM Sans, 'Noto Sans SC', 'Microsoft YaHei', sans-serif",
    redrawOnParentResize: false,
    redrawOnWindowResize: false,
  },
  colors: ["#465fff", "#d92d20"],
  stroke: { curve: "smooth", width: 2 },
  fill: { type: "gradient", gradient: { opacityFrom: 0.15, opacityTo: 0 } },
  dataLabels: { enabled: false },
  xaxis: {
    categories: trendCategories.value,
    axisBorder: { show: false },
    axisTicks: { show: false },
    labels: { rotate: 0, hideOverlappingLabels: true, style: { colors: "#98a2b3", fontSize: "12px" } },
  },
  yaxis: { labels: { formatter: axisMoney, style: { colors: "#98a2b3", fontSize: "12px" } } },
  grid: { borderColor: "#e4e7ec", strokeDashArray: 3 },
  legend: { position: "top", horizontalAlign: "right", fontSize: "12px", labels: { colors: "#667085" }, markers: { size: 4 } },
  tooltip: { y: { formatter: (value) => formatMoney(value) } },
}));
</script>

<template>
  <div class="space-y-5">
    <div class="grid grid-cols-2 gap-4 xl:grid-cols-4">
      <section v-for="card in cards" :key="card.label" class="card min-h-[132px] p-5">
        <span class="mb-3 grid h-10 w-10 place-items-center rounded-lg" :class="toneIcon[card.tone]"><component :is="card.icon" :size="20" /></span>
        <span class="block text-sm font-medium text-muted">{{ card.label }}</span>
        <strong class="mt-1 block text-[19px] leading-tight text-ink">{{ card.value }}</strong>
      </section>
    </div>

    <section class="card">
      <div class="panel-head">
        <h3>应收趋势</h3>
        <div class="inline-flex rounded-lg border border-border bg-canvas p-1" aria-label="选择趋势展示范围">
          <button
            v-for="option in rangeOptions"
            :key="option.value"
            type="button"
            :aria-pressed="range === option.value"
            class="h-8 rounded-md px-3 text-sm font-semibold transition-colors"
            :class="range === option.value ? 'bg-white text-brand shadow-sm' : 'text-muted hover:text-ink'"
            @click="range = option.value"
          >
            {{ option.label }}
          </button>
        </div>
      </div>
      <div ref="trendHostRef" class="min-h-[348px] px-5 pb-3 pt-4">
        <VueApexCharts :key="range" ref="trendChartRef" type="area" height="320" :options="trendOptions" :series="trendSeries" />
      </div>
    </section>
  </div>
</template>
