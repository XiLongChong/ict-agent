<script setup>
import { nextTick, ref, watch } from "vue";
import { AlertCircle, CheckCircle2, Database, PlayCircle, Search, ShieldCheck, Sparkles, UserCheck } from "lucide-vue-next";
import Badge from "./ui/Badge.vue";
import { hypothesisColor, labels, priorityColor, queryArguments, stageColor, streamNdjson } from "../lib";

const props = defineProps({ caseItem: Object });
const emit = defineEmits(["completed"]);
const running = ref(false);
const events = ref([]);
const record = ref(props.caseItem?.latest_investigation || null);
const error = ref("");
const thread = ref(null);

watch(
  () => props.caseItem,
  (value) => {
    record.value = value?.latest_investigation || null;
    events.value = [];
    error.value = "";
  },
  { deep: false }
);

async function scrollToBottom() {
  await nextTick();
  if (thread.value) thread.value.scrollTo({ top: thread.value.scrollHeight, behavior: "smooth" });
}

async function investigate() {
  if (!props.caseItem || running.value) return;
  running.value = true;
  events.value = [];
  record.value = null;
  error.value = "";
  let finalRecord = null;
  let terminalError = "";
  try {
    await streamNdjson(`/api/v1/cases/${encodeURIComponent(props.caseItem.case_id)}/investigations`, { method: "POST" }, (event) => {
      events.value.push(event);
      if (event.event_type === "REPORT_COMPLETED" && event.record) {
        finalRecord = event.record;
        record.value = event.record;
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

const eventIcon = {
  RUN_STARTED: PlayCircle,
  TOOL_STARTED: Search,
  TOOL_COMPLETED: Database,
  VALIDATION_STARTED: ShieldCheck,
  REPORT_COMPLETED: CheckCircle2,
  ERROR: AlertCircle,
};
const eventTone = {
  RUN_STARTED: "text-muted",
  TOOL_STARTED: "text-muted",
  TOOL_COMPLETED: "text-brand",
  VALIDATION_STARTED: "text-muted",
  REPORT_COMPLETED: "text-success",
  ERROR: "text-danger",
};
function evidenceById(id) {  return record.value?.evidence?.find((item) => item.evidence_id === id);
}
function completeness(value) {
  return ({ LOW: 33, MEDIUM: 66, HIGH: 100 })[value] || 0;
}
</script>

<template>
  <section class="mx-auto flex h-full w-full max-w-[1100px] flex-col">
    <header class="flex items-center gap-4 px-5 py-4">
      <div class="leading-tight">
        <span class="eyebrow">AGENT INVESTIGATION</span>
        <h3 class="mt-0.5 text-[16px] font-bold text-ink">调查对话</h3>
        <p class="text-xs text-muted">按真实执行顺序展示工具与证据，不展示私有思维链。</p>
      </div>
      <div class="flex-1"></div>
      <button
        type="button"
        :disabled="running"
        class="inline-flex h-10 items-center gap-2 rounded-lg bg-brand px-4 text-sm font-semibold text-white transition-colors hover:bg-brand-dark disabled:opacity-60"
        @click="investigate"
      >
        <Sparkles :size="16" :class="running ? 'animate-spin' : ''" />
        {{ record ? "重新调查" : "开始调查" }}
      </button>
    </header>

    <div ref="thread" class="h-[calc(100vh-280px)] min-h-[360px] overflow-y-auto px-4 pb-10 md:px-6" aria-live="polite">
      <div class="mx-auto flex max-w-[880px] flex-col gap-1 border-l border-border pl-6">
        <div class="relative -ml-[31px] my-2 flex items-center gap-2">
          <span class="grid h-8 w-8 flex-none place-items-center rounded-full border border-border bg-surface"><Database :size="14" class="text-muted" /></span>
          <span class="text-[11px] text-muted">只读业务工具 · 无任意 SQL · 结论输出前校验证据引用</span>
        </div>

        <div v-if="!events.length && !record" class="my-10 flex items-start gap-4">
          <span class="grid h-12 w-12 flex-none place-items-center rounded-xl bg-brand-wash text-brand-deep"><Sparkles :size="22" /></span>
          <div>
            <h4 class="text-[15px] font-semibold text-ink">准备从证据开始调查</h4>
            <p class="mt-1 max-w-[520px] text-[13px] leading-6 text-muted">Agent 会先发现当前案件可用数据，再逐步查询、核验证据，最终报告将出现在这条时间线底部。</p>
          </div>
        </div>

        <div v-for="event in events" :key="event.sequence" class="relative -ml-[31px] grid grid-cols-[28px_minmax(0,1fr)] gap-3 py-2">
          <span class="grid h-8 w-8 flex-none place-items-center rounded-full border border-border bg-surface" :class="eventTone[event.event_type]">
            <component :is="eventIcon[event.event_type] || PlayCircle" :size="15" />
          </span>
          <div class="rounded-lg border border-border bg-surface p-3.5">
            <div class="flex items-center gap-2">
              <strong class="text-[13px] text-ink">{{ event.tool_name ? labels.tool[event.tool_name] : labels.event[event.event_type] }}</strong>
              <span class="ml-auto font-mono text-[10px] text-faint">#{{ String(event.sequence).padStart(2, "0") }}</span>
            </div>
            <p class="mt-1 text-[13px] leading-6 text-muted">{{ event.message }}</p>
            <small v-if="event.evidence?.arguments || event.arguments" class="mt-1 block font-mono text-[11px] text-faint">{{ queryArguments(event.evidence || event) }}</small>
            <div v-if="event.evidence" class="mt-2 flex items-start gap-2 rounded-md bg-canvas p-2.5">
              <Database :size="14" class="mt-0.5 flex-none text-muted" />
              <div>
                <strong class="block text-xs text-ink">{{ event.evidence.summary }}</strong>
                <small class="block text-[11px] text-faint">{{ event.evidence.period }} · 证据 {{ event.evidence.evidence_id.slice(0, 8) }}</small>
              </div>
            </div>
          </div>
        </div>

        <div v-if="running" class="relative -ml-[31px] my-3 flex items-center gap-2 text-muted">
          <span class="flex items-end gap-1"><i class="h-2 w-1 animate-bounce rounded bg-faint"></i><i class="h-3 w-1 animate-bounce rounded bg-faint" style="animation-delay: 0.12s"></i><i class="h-2 w-1 animate-bounce rounded bg-faint" style="animation-delay: 0.24s"></i></span>
          <small class="text-xs">Agent 正在继续调查</small>
        </div>

        <div v-if="record" class="relative -ml-[31px] mt-4 space-y-4">
          <div class="flex items-center gap-3">
            <span class="grid h-10 w-10 place-items-center rounded-xl bg-success-wash text-success"><CheckCircle2 :size="20" /></span>
            <div><small class="block font-mono text-[10px] uppercase tracking-wide text-faint">VERIFIED OUTCOME</small><h3 class="text-lg font-bold text-ink">调查报告</h3></div>
          </div>

          <section class="card p-5">
            <div class="flex flex-wrap items-center gap-3">
              <Badge :tone="priorityColor(record.report.recommended_priority)">建议{{ labels.priority[record.report.recommended_priority] }}优先级</Badge>
              <span class="text-xs text-muted">证据完整度 {{ record.report.evidence_completeness }}</span>
            </div>
            <p class="mt-3 text-sm leading-6 text-muted">{{ record.report.investigation_summary }}</p>
            <div class="mt-3 h-1.5 rounded-full bg-gray-100"><div class="h-1.5 rounded-full bg-brand" :style="{ width: completeness(record.report.evidence_completeness) + '%' }"></div></div>
          </section>

          <section class="card p-5">
            <div class="flex items-center gap-2">
              <h4 class="text-[15px] font-bold text-ink">风险信号判断</h4>
              <Badge :tone="stageColor(record.report.risk_assessment.stage)">{{ labels.riskStage[record.report.risk_assessment.stage] }}</Badge>
            </div>
            <p class="mt-2 text-[13px] leading-6 text-muted">{{ record.report.risk_assessment.statement }}</p>
            <div class="mt-4 grid grid-cols-1 gap-4 md:grid-cols-3">
              <div><strong class="text-xs text-ink">主要驱动</strong><ul class="mt-1 space-y-1 text-[13px] text-muted"><li v-for="item in record.report.risk_assessment.drivers" :key="item">· {{ item }}</li></ul></div>
              <div><strong class="text-xs text-ink">反向信号</strong><ul class="mt-1 space-y-1 text-[13px] text-muted"><li v-for="item in record.report.risk_assessment.counter_signals" :key="item">· {{ item }}</li><li v-if="!record.report.risk_assessment.counter_signals.length">· 暂无</li></ul></div>
              <div><strong class="text-xs text-ink">后续监测</strong><ul class="mt-1 space-y-1 text-[13px] text-muted"><li v-for="item in record.report.risk_assessment.watch_items" :key="item">· {{ item }}</li></ul></div>
            </div>
          </section>

          <section class="card p-5">
            <div class="flex items-center justify-between"><h4 class="text-[15px] font-bold text-ink">确定事实</h4><span class="text-xs text-faint">{{ record.report.facts.length }} 项</span></div>
            <div class="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
              <article v-for="fact in record.report.facts" :key="fact.statement" class="rounded-lg border border-border p-3">
                <p class="text-[13px] leading-6 text-muted">{{ fact.statement }}</p>
                <div class="mt-2 flex flex-wrap gap-1.5">
                  <Badge v-for="id in fact.evidence_ids" :key="id" tone="brand">{{ labels.tool[evidenceById(id)?.tool_name] || "证据" }} · {{ evidenceById(id)?.period }}</Badge>
                </div>
              </article>
            </div>
          </section>

          <section class="card p-5">
            <div class="flex items-center justify-between"><h4 class="text-[15px] font-bold text-ink">证据支持的判断</h4><span class="text-xs text-faint">{{ record.report.hypotheses.length }} 项</span></div>
            <div class="mt-3 space-y-3">
              <article v-for="item in record.report.hypotheses" :key="item.hypothesis_id" class="rounded-lg border border-border p-3">
                <div class="flex items-start gap-2"><Badge :tone="hypothesisColor(item.status)">{{ labels.hypothesis[item.status] }}</Badge><strong class="text-[13px] text-ink">{{ item.statement }}</strong></div>
                <p v-if="item.missing_evidence.length" class="mt-1.5 text-[12px] text-warning-deep"><b>仍需补证：</b>{{ item.missing_evidence.join("；") }}</p>
              </article>
            </div>
          </section>

          <div class="grid grid-cols-1 gap-4 md:grid-cols-2">
            <section class="card p-5"><h4 class="text-[15px] font-bold text-ink">建议动作</h4><ul class="mt-2 space-y-1.5 text-[13px] text-muted"><li v-for="item in record.report.recommended_actions" :key="item">· {{ item }}</li></ul></section>
            <section class="card p-5"><h4 class="text-[15px] font-bold text-ink">数据限制</h4><ul class="mt-2 space-y-1.5 text-[13px] text-muted"><li v-for="item in record.report.limitations" :key="item">· {{ item }}</li><li v-if="!record.report.limitations.length">· 未报告额外限制</li></ul></section>
          </div>

          <details class="card">
            <summary class="cursor-pointer select-none px-5 py-4 text-sm font-semibold text-ink">查看本轮 {{ record.evidence.length }} 项工具证据</summary>
            <div class="space-y-3 border-t border-border px-5 py-4">
              <article v-for="(item, index) in record.evidence" :key="item.evidence_id" class="grid grid-cols-[24px_minmax(0,1fr)] gap-3">
                <span class="font-mono text-xs text-faint">{{ String(index + 1).padStart(2, "0") }}</span>
                <div>
                  <div class="flex items-center gap-2"><strong class="text-[13px] text-ink">{{ labels.tool[item.tool_name] }}</strong><small class="text-[11px] text-faint">{{ item.period }} · {{ item.sources.join(" / ") }}</small></div>
                  <p class="mt-1 text-[13px] leading-6 text-muted">{{ item.summary }}</p>
                  <code class="mt-1 block font-mono text-[11px] text-faint">{{ item.evidence_id }}</code>
                </div>
              </article>
            </div>
          </details>

          <details v-if="record.report.trace?.length" class="card">
            <summary class="cursor-pointer select-none px-5 py-4 text-sm font-semibold text-ink">回放调查轨迹</summary>
            <div class="space-y-3 border-t border-border px-5 py-4">
              <article v-for="item in record.report.trace" :key="item.created_at + item.title">
                <div class="flex items-center gap-2"><strong class="text-[13px] text-ink">{{ item.title }}</strong><small class="ml-auto text-[11px] text-faint">{{ item.created_at }}</small></div>
                <p class="mt-1 text-[12px] leading-5 text-muted">{{ item.detail }}</p>
              </article>
            </div>
          </details>

          <div class="flex items-center gap-2 rounded-lg bg-canvas p-3 text-xs text-muted"><UserCheck :size="15" class="flex-none" />Agent 提供调查证据，最终业务处置仍由人工审核决定。</div>
        </div>

        <div v-if="error" class="my-4 flex items-start gap-3 rounded-lg border border-danger/30 bg-danger-wash p-4">
          <AlertCircle :size="18" class="mt-0.5 flex-none text-danger" />
          <div><strong class="block text-sm text-danger">本次调查未生成报告</strong><p class="mt-1 text-[13px] text-danger-deep">{{ error }}</p></div>
        </div>
      </div>
    </div>
  </section>
</template>
