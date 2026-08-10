<script setup>
import { computed } from "vue";
import { formatMoney, formatPercent, metricMap } from "../lib";
const props = defineProps({ data: Object, loading: Boolean });
const cards = computed(() => {
  if (!props.data) return [];
  const overview = metricMap(props.data.overview); const ar = metricMap(props.data.latest_ar);
  return [
    ["累计销售额", formatMoney(overview["销售额"]), "含退货负值", "brand", "mdi-chart-line"],
    ["累计回款额", formatMoney(overview["回款额"]), "全数据窗口", "success", "mdi-cash-check"],
    ["最新应收余额", formatMoney(ar["应收余额"]), props.data.latest_ar.period, "warning", "mdi-wallet-outline"],
    ["最新超期率", formatPercent(ar["超期率"]), `超期 ${formatMoney(ar["超期应收"])}`, "danger", "mdi-alert-circle-outline"],
  ];
});
const trend = computed(() => (props.data?.ar_trend?.rows || []).slice(-8).reverse());
</script>

<template>
  <div class="view-stack">
    <div class="section-intro"><div><span class="eyebrow">BUSINESS FACTS</span><h2>经营分析</h2></div><p>{{ data?.overview?.period || '正在读取' }} · 确定性指标，不消耗模型额度。</p></div>
    <div class="metric-grid"><v-card v-for="card in cards" :key="card[0]" class="metric-card" :class="`metric-${card[3]}`" :loading="loading"><div class="metric-icon"><v-icon :icon="card[4]" /></div><span>{{ card[0] }}</span><strong class="compact">{{ card[1] }}</strong><small>{{ card[2] }}</small></v-card></div>
    <v-card class="flat-panel"><div class="panel-title"><div><span class="section-index">C</span><h3>最近应收趋势</h3></div><span class="subtle-copy">每个月末独立聚合</span></div>
      <div class="table-scroll"><v-table density="comfortable"><thead><tr><th>期间</th><th>应收余额</th><th>超期应收</th><th>超期率</th></tr></thead><tbody><tr v-for="row in trend" :key="row[0]"><td>{{ row[0] }}</td><td>{{ formatMoney(row[1]) }}</td><td>{{ formatMoney(row[2]) }}</td><td>{{ formatPercent(row[3]) }}</td></tr></tbody></v-table></div>
    </v-card>
  </div>
</template>
