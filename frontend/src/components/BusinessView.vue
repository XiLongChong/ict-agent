<script setup>
import { computed } from "vue";
import VueApexCharts from "vue3-apexcharts";
import { AlertCircle, ChartLine, HandCoins, Wallet } from "lucide-vue-next";
import { formatMoney, formatPercent, metricMap } from "../lib";
import { workspace } from "../store";
import { useResponsiveChart } from "../composables/useResponsiveChart";

const { chartRef: trendChartRef, chartHostRef: trendHostRef } = useResponsiveChart();

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
const trend = computed(() => (workspace.business?.ar_trend?.rows || []).slice(-8).reverse());
const trendCategories = computed(() => trend.value.map((r) => r[0]));
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
    fontFamily: "DM Sans, 'Microsoft YaHei', sans-serif",
    redrawOnParentResize: false,
    redrawOnWindowResize: false,
  },
  colors: ["#465fff", "#d92d20"],
  stroke: { curve: "smooth", width: 2 },
  fill: { type: "gradient", gradient: { opacityFrom: 0.15, opacityTo: 0 } },
  dataLabels: { enabled: false },
  xaxis: { categories: trendCategories.value, axisBorder: { show: false }, axisTicks: { show: false }, labels: { style: { colors: "#98a2b3", fontSize: "12px" } } },
  yaxis: { labels: { formatter: axisMoney, style: { colors: "#98a2b3", fontSize: "12px" } } },
  grid: { borderColor: "#e4e7ec", strokeDashArray: 3 },
  legend: { position: "top", horizontalAlign: "right", fontSize: "12px", labels: { colors: "#667085" }, markers: { size: 4 } },
  tooltip: { y: { formatter: (value) => formatMoney(value) } },
}));
</script>

<template>
  <div class="space-y-5">
    <h2 class="text-[27px] font-bold text-ink">经营分析</h2>

    <div class="grid grid-cols-2 gap-4 xl:grid-cols-4">
      <section v-for="card in cards" :key="card.label" class="card min-h-[132px] p-5">
        <span class="mb-3 grid h-10 w-10 place-items-center rounded-lg" :class="toneIcon[card.tone]"><component :is="card.icon" :size="20" /></span>
        <span class="block text-sm font-medium text-muted">{{ card.label }}</span>
        <strong class="mt-1 block text-[19px] leading-tight text-ink">{{ card.value }}</strong>
      </section>
    </div>

    <section class="card">
      <div class="panel-head">
        <h3>最近应收趋势</h3>
      </div>
      <div ref="trendHostRef" class="px-5 pt-4">
        <VueApexCharts ref="trendChartRef" type="area" height="260" :options="trendOptions" :series="trendSeries" />
      </div>
      <div class="overflow-x-auto border-t border-border">
        <table class="table-base">
          <thead><tr><th>期间</th><th>应收余额</th><th>超期应收</th><th>超期率</th></tr></thead>
          <tbody>
            <tr v-for="row in trend" :key="row[0]">
              <td><span class="font-mono text-sm text-muted">{{ row[0] }}</span></td>
              <td class="money-cell">{{ formatMoney(row[1]) }}</td>
              <td class="money-cell">{{ formatMoney(row[2]) }}</td>
              <td>
                <span class="inline-flex rounded-md px-2 py-0.5 text-sm font-semibold" :class="Number(row[3]) > 0.3 ? 'bg-danger-wash text-danger' : 'bg-success-wash text-success-deep'">{{ formatPercent(row[3]) }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>
