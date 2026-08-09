<script setup>
import { nextTick, ref, watch } from "vue";
import { hypothesisColor, labels, priorityColor, queryArguments, stageColor, streamNdjson } from "../lib";

const props = defineProps({ caseItem: Object });
const emit = defineEmits(["completed"]);
const running = ref(false);
const events = ref([]);
const record = ref(props.caseItem?.latest_investigation || null);
const error = ref("");
const thread = ref(null);

watch(() => props.caseItem, (value) => {
  record.value = value?.latest_investigation || null;
  events.value = [];
  error.value = "";
}, { deep: false });

async function scrollToBottom() {
  await nextTick();
  if (thread.value) thread.value.scrollTo({ top: thread.value.scrollHeight, behavior: "smooth" });
}

async function investigate() {
  if (!props.caseItem || running.value) return;
  running.value = true; events.value = []; record.value = null; error.value = "";
  let finalRecord = null; let terminalError = "";
  try {
    await streamNdjson(`/api/v1/cases/${encodeURIComponent(props.caseItem.case_id)}/investigations`, { method: "POST" }, (event) => {
      events.value.push(event);
      if (event.event_type === "REPORT_COMPLETED" && event.record) {
        finalRecord = event.record; record.value = event.record;
      }
      if (event.event_type === "ERROR") terminalError = event.message;
      void scrollToBottom();
    });
    if (!finalRecord) throw new Error(terminalError || "调查事件流结束，但没有生成可保存的报告。");
    emit("completed");
  } catch (exception) {
    error.value = exception.message;
  } finally {
    running.value = false;
    void scrollToBottom();
  }
}

function eventIcon(type) {
  return ({ RUN_STARTED: "mdi-play-circle-outline", TOOL_STARTED: "mdi-magnify", TOOL_COMPLETED: "mdi-database-check-outline", VALIDATION_STARTED: "mdi-shield-check-outline", REPORT_COMPLETED: "mdi-check-circle-outline", ERROR: "mdi-alert-circle-outline" })[type] || "mdi-circle-small";
}
function eventColor(type) { return type === "ERROR" ? "error" : type === "REPORT_COMPLETED" ? "success" : type === "TOOL_COMPLETED" ? "primary" : "grey"; }
function evidenceById(id) { return record.value?.evidence?.find((item) => item.evidence_id === id); }
function completeness(value) { return ({ LOW: 33, MEDIUM: 66, HIGH: 100 })[value] || 0; }
</script>

<template>
  <section class="investigation-shell">
    <header class="investigation-header">
      <div><span class="eyebrow">AGENT INVESTIGATION</span><h3>调查对话</h3><p>按真实执行顺序展示工具与证据，不展示私有思维链。</p></div>
      <v-btn color="primary" :loading="running" prepend-icon="mdi-sparkles" @click="investigate">{{ record ? '重新调查' : '开始调查' }}</v-btn>
    </header>

    <div ref="thread" class="investigation-thread" aria-live="polite">
      <div class="thread-boundary"><v-icon icon="mdi-lock-outline" /><span>只读业务工具 · 无任意 SQL · 结论输出前校验证据引用</span></div>

      <div v-if="!events.length && !record" class="thread-empty">
        <div class="agent-avatar"><v-icon icon="mdi-creation" /></div><div><h4>准备从证据开始调查</h4><p>Agent 会先发现当前案件可用数据，再逐步查询、核验证据，最终报告将出现在这条时间线底部。</p></div>
      </div>

      <div v-for="event in events" :key="event.sequence" class="thread-message">
        <div class="event-rail"><span :class="`event-dot ${eventColor(event.event_type)}`"><v-icon :icon="eventIcon(event.event_type)" size="17" /></span></div>
        <div class="event-card" :class="event.event_type.toLowerCase()">
          <div class="event-heading"><strong>{{ event.tool_name ? labels.tool[event.tool_name] : labels.event[event.event_type] }}</strong><span>#{{ String(event.sequence).padStart(2, '0') }}</span></div>
          <p>{{ event.message }}</p>
          <small v-if="event.evidence?.arguments || event.arguments">{{ queryArguments(event.evidence || event) }}</small>
          <div v-if="event.evidence" class="evidence-preview"><v-icon icon="mdi-database-outline" /><div><strong>{{ event.evidence.summary }}</strong><small>{{ event.evidence.period }} · 证据 {{ event.evidence.evidence_id.slice(0, 8) }}</small></div></div>
        </div>
      </div>

      <div v-if="running" class="agent-typing"><span></span><span></span><span></span><small>Agent 正在继续调查</small></div>

      <div v-if="record" class="final-report">
        <div class="report-arrival"><span><v-icon icon="mdi-check-decagram" /></span><div><small>VERIFIED OUTCOME</small><h3>调查报告</h3></div></div>
        <v-card class="report-summary-card">
          <div class="report-title"><v-chip :color="priorityColor(record.report.recommended_priority)" variant="tonal">建议{{ labels.priority[record.report.recommended_priority] }}优先级</v-chip><span>证据完整度 {{ record.report.evidence_completeness }}</span></div>
          <p>{{ record.report.investigation_summary }}</p><v-progress-linear :model-value="completeness(record.report.evidence_completeness)" color="primary" height="6" rounded />
        </v-card>

        <v-card class="report-section risk-assessment"><div class="report-section-title"><h4>风险信号判断</h4><v-chip size="small" :color="stageColor(record.report.risk_assessment.stage)" variant="tonal">{{ labels.riskStage[record.report.risk_assessment.stage] }}</v-chip></div>
          <p>{{ record.report.risk_assessment.statement }}</p>
          <div class="assessment-grid"><div><strong>主要驱动</strong><ul><li v-for="item in record.report.risk_assessment.drivers" :key="item">{{ item }}</li></ul></div><div><strong>反向信号</strong><ul><li v-for="item in record.report.risk_assessment.counter_signals" :key="item">{{ item }}</li><li v-if="!record.report.risk_assessment.counter_signals.length">暂无</li></ul></div><div><strong>后续监测</strong><ul><li v-for="item in record.report.risk_assessment.watch_items" :key="item">{{ item }}</li></ul></div></div>
        </v-card>

        <section class="report-section"><div class="report-section-title"><h4>确定事实</h4><span>{{ record.report.facts.length }} 项</span></div>
          <div class="fact-grid"><article v-for="fact in record.report.facts" :key="fact.statement"><p>{{ fact.statement }}</p><div class="citation-row"><v-chip v-for="id in fact.evidence_ids" :key="id" size="x-small" variant="outlined" color="primary">{{ labels.tool[evidenceById(id)?.tool_name] || '证据' }} · {{ evidenceById(id)?.period }}</v-chip></div></article></div>
        </section>

        <section class="report-section"><div class="report-section-title"><h4>证据支持的判断</h4><span>{{ record.report.hypotheses.length }} 项</span></div>
          <article v-for="item in record.report.hypotheses" :key="item.hypothesis_id" class="hypothesis-card"><div><v-chip size="small" :color="hypothesisColor(item.status)" variant="tonal">{{ labels.hypothesis[item.status] }}</v-chip><strong>{{ item.statement }}</strong></div><p v-if="item.missing_evidence.length"><b>仍需补证：</b>{{ item.missing_evidence.join('；') }}</p></article>
        </section>

        <div class="report-two-column"><v-card class="report-section"><h4>建议动作</h4><ul><li v-for="item in record.report.recommended_actions" :key="item">{{ item }}</li></ul></v-card><v-card class="report-section"><h4>数据限制</h4><ul><li v-for="item in record.report.limitations" :key="item">{{ item }}</li><li v-if="!record.report.limitations.length">未报告额外限制</li></ul></v-card></div>

        <v-expansion-panels class="evidence-panels" variant="accordion">
          <v-expansion-panel :title="`查看本轮 ${record.evidence.length} 项工具证据`"><v-expansion-panel-text><article v-for="(item, index) in record.evidence" :key="item.evidence_id" class="evidence-record"><span>{{ String(index + 1).padStart(2, '0') }}</span><div><strong>{{ labels.tool[item.tool_name] }}</strong><small>{{ item.period }} · {{ item.sources.join(' / ') }}</small><p>{{ item.summary }}</p><code>{{ item.evidence_id }}</code></div></article></v-expansion-panel-text></v-expansion-panel>
          <v-expansion-panel v-if="record.report.trace?.length" title="回放调查轨迹"><v-expansion-panel-text><article v-for="item in record.report.trace" :key="item.created_at + item.title" class="trace-record"><strong>{{ item.title }}</strong><small>{{ item.created_at }}</small><p>{{ item.detail }}</p></article></v-expansion-panel-text></v-expansion-panel>
        </v-expansion-panels>
        <div class="human-boundary"><v-icon icon="mdi-account-check-outline" />Agent 提供调查证据，最终业务处置仍由人工审核决定。</div>
      </div>

      <v-alert v-if="error" type="error" variant="tonal" title="本次调查未生成报告">{{ error }}</v-alert>
    </div>
  </section>
</template>
