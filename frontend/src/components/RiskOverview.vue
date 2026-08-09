<script setup>
import { computed } from "vue";
import { formatMoney, labels, priorityColor, statusColor } from "../lib";

const props = defineProps({ overview: Object, cases: Array, loading: Boolean });
defineEmits(["open-case", "show-cases"]);
const priorityCases = computed(() => (props.cases || []).slice(0, 5));
const ar = computed(() => props.overview?.cases_by_type?.ACCOUNTS_RECEIVABLE || 0);
const inventory = computed(() => props.overview?.cases_by_type?.INVENTORY || 0);
const total = computed(() => Math.max(ar.value + inventory.value, 1));
const metrics = computed(() => [
  { label: "关键级案件", value: props.overview?.critical_cases ?? "—", note: "当前规则侧重早期预警", color: "#ea4335" },
  { label: "等待调查", value: props.overview?.open_cases ?? "—", note: "规则已经命中", color: "#4285f4" },
  { label: "等待审核", value: props.overview?.pending_review_cases ?? "—", note: "Agent 已完成取证", color: "#fbbc05" },
  { label: "风险敞口", value: props.overview ? formatMoney(props.overview.exposure_amount) : "—", note: "未关闭案件合计", color: "#34a853", compact: true },
]);
</script>

<template>
  <div class="view-stack">
    <section class="overview-hero">
      <div><span class="eyebrow">规则发现 · Agent 调查 · 人工审核</span><h2>从风险信号出发，沿证据链完成可复核调查。</h2>
        <p v-if="overview?.latest_run">规则集 {{ overview.latest_run.rule_set_version }} · 观察期 {{ overview.latest_run.observation_date }} · 命中 {{ overview.latest_run.rule_hits }} 条规则</p>
        <p v-else>尚未执行规则扫描，请点击右上角“重新扫描”。</p>
      </div>
      <div class="hero-count"><strong>{{ overview?.total_cases ?? '—' }}</strong><span>当前风险案件</span></div>
    </section>

    <div class="metric-grid">
      <v-card v-for="metric in metrics" :key="metric.label" class="metric-card" :loading="loading">
        <span class="metric-accent" :style="{ background: metric.color }"></span>
        <span>{{ metric.label }}</span><strong :class="{ compact: metric.compact }">{{ metric.value }}</strong><small>{{ metric.note }}</small>
      </v-card>
    </div>

    <div class="overview-grid">
      <v-card class="flat-panel">
        <div class="panel-title"><div><span class="section-index">A</span><h3>优先调查</h3></div><v-btn variant="text" color="primary" @click="$emit('show-cases')">查看全部</v-btn></div>
        <div class="priority-list">
          <button v-for="item in priorityCases" :key="item.case_id" class="priority-case" @click="$emit('open-case', item.case_id)">
            <span class="priority-bar" :class="item.priority.toLowerCase()"></span>
            <div><strong>{{ item.entity_label }}</strong><small>{{ item.summary }}</small></div>
            <div class="case-meta"><strong>{{ formatMoney(item.exposure_amount) }}</strong><v-chip size="x-small" :color="statusColor(item.status)" variant="tonal">{{ labels.status[item.status] }}</v-chip></div>
          </button>
          <div v-if="!loading && !priorityCases.length" class="empty-state">尚无风险案件</div>
        </div>
      </v-card>

      <v-card class="flat-panel composition-panel">
        <div class="panel-title"><div><span class="section-index">B</span><h3>案件构成</h3></div></div>
        <div class="composition-row"><div><span>客户应收调查</span><strong>{{ ar }} 件</strong></div><v-progress-linear :model-value="ar / total * 100" color="primary" height="8" rounded /></div>
        <div class="composition-row"><div><span>库存积压调查</span><strong>{{ inventory }} 件</strong></div><v-progress-linear :model-value="inventory / total * 100" color="success" height="8" rounded /></div>
        <div class="boundary-note"><v-icon icon="mdi-database-lock-outline" /><div><strong>数据边界</strong><p>库存为公司仓库库存；当前数据不包含促销、下游库存、银行未核销和项目验收记录。</p></div></div>
      </v-card>
    </div>
  </div>
</template>
