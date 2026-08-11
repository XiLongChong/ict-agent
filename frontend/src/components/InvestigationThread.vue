<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { AlertCircle, CheckCircle2, Database, PlayCircle, Search, ShieldCheck, Sparkles } from "lucide-vue-next";
import Badge from "./ui/Badge.vue";
import { hypothesisColor, labels, priorityColor, queryArguments, stageColor, streamNdjson } from "../lib";

const props = defineProps({ caseItem: Object });
const emit = defineEmits(["completed"]);
const running = ref(false);
const events = ref([]);
const record = ref(props.caseItem?.latest_investigation || null);
const error = ref("");
const canInvestigate = computed(() => props.caseItem?.status === "PENDING_AGENT_REVIEW");

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
  window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "smooth" });
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
    emit("completed", finalRecord);
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
function completenessLabel(value) {
  return ({ LOW: "证据有限", MEDIUM: "证据基本充分", HIGH: "证据充分" })[value] || "证据待核验";
}
</script>

<template>
  <section class="mx-auto w-full max-w-[1280px] space-y-4 pb-10" aria-live="polite">
    <div v-if="canInvestigate || running" class="card flex items-center gap-4 p-4">
      <div v-if="running" class="flex items-center gap-2 text-sm text-muted">
        <span class="flex items-end gap-1">
          <i class="h-2 w-1 animate-bounce rounded bg-faint"></i>
          <i class="h-3 w-1 animate-bounce rounded bg-faint" style="animation-delay: 0.12s"></i>
          <i class="h-2 w-1 animate-bounce rounded bg-faint" style="animation-delay: 0.24s"></i>
        </span>
        AI正在审查案件
      </div>
      <div class="flex-1"></div>
      <button
        type="button"
        :disabled="running"
        class="inline-flex h-10 items-center gap-2 rounded-lg bg-brand px-4 text-sm font-semibold text-white transition-colors hover:bg-brand-dark disabled:opacity-60"
        @click="investigate"
      >
        <Sparkles :size="16" :class="running ? 'animate-spin' : ''" />
        {{ record ? "重新审查" : "开始审查" }}
      </button>
    </div>

    <section v-if="events.length && !record" class="card overflow-hidden">
      <header class="border-b border-border px-5 py-4">
        <h3 class="text-[15px] font-bold text-ink">审查进度</h3>
      </header>
      <div class="divide-y divide-border px-5">
        <article v-for="event in events" :key="event.sequence" class="py-4">
          <div class="flex items-center gap-2">
            <component
              :is="eventIcon[event.event_type] || PlayCircle"
              :size="16"
              :class="eventTone[event.event_type]"
            />
            <strong class="text-sm text-ink">
              {{ event.tool_name ? labels.tool[event.tool_name] : labels.event[event.event_type] }}
            </strong>
          </div>
          <p class="mt-1 pl-6 text-sm leading-6 text-muted">{{ event.message }}</p>
          <details v-if="event.evidence?.arguments || event.arguments" class="ml-6 mt-2">
            <summary class="cursor-pointer select-none text-sm font-semibold text-brand-deep">
              查看本步查询与证据
            </summary>
            <div class="mt-2 rounded-lg bg-canvas p-3">
              <p class="font-mono text-sm text-muted">{{ queryArguments(event.evidence || event) }}</p>
              <template v-if="event.evidence">
                <p class="mt-2 text-sm leading-6 text-ink">{{ event.evidence.summary }}</p>
                <span class="mt-1 block text-sm text-muted">{{ event.evidence.period }}</span>
              </template>
            </div>
          </details>
        </article>
      </div>
    </section>

    <section v-if="!events.length && !record" class="card px-6 py-12 text-center">
      <Sparkles :size="24" class="mx-auto text-brand" />
      <h3 class="mt-3 text-[15px] font-bold text-ink">等待开始AI审查</h3>
    </section>

    <template v-if="record">
      <article class="card overflow-hidden">
        <header class="flex flex-wrap items-center gap-3 border-b border-border px-5 py-4">
          <CheckCircle2 :size="20" class="text-success" />
          <h3 class="text-lg font-bold text-ink">AI审查结论</h3>
          <div class="flex-1"></div>
          <Badge :tone="stageColor(record.report.risk_assessment.stage)">
            {{ labels.riskStage[record.report.risk_assessment.stage] }}
          </Badge>
          <Badge tone="neutral">{{ completenessLabel(record.report.evidence_completeness) }}</Badge>
        </header>

        <div class="p-5">
          <p class="text-[15px] leading-7 text-ink">
            {{ record.report.risk_assessment.statement }}
          </p>

          <div class="mt-6 border-t border-border pt-5">
            <div class="flex items-center gap-2">
              <h4 class="text-[15px] font-bold text-ink">后续处理建议</h4>
              <Badge :tone="priorityColor(record.report.recommended_priority)">
                {{ labels.priority[record.report.recommended_priority] }}优先级
              </Badge>
            </div>
            <ol class="mt-3 space-y-3">
              <li
                v-for="(item, index) in record.report.recommended_actions"
                :key="item"
                class="grid grid-cols-[24px_minmax(0,1fr)] gap-2 text-sm leading-6 text-muted"
              >
                <span class="grid h-6 w-6 place-items-center rounded-full bg-brand-wash text-sm font-semibold text-brand-deep">
                  {{ index + 1 }}
                </span>
                <span>{{ item }}</span>
              </li>
            </ol>
          </div>

          <p class="mt-5 rounded-lg bg-warning-wash px-4 py-3 text-sm leading-6 text-warning-deep">
            AI结论用于辅助判断，请结合下方可复核依据完成人工复核。
          </p>
        </div>
      </article>

      <details class="card">
        <summary class="cursor-pointer select-none px-5 py-4 text-sm font-semibold text-ink">
          查看完整分析依据
        </summary>
        <div class="space-y-6 border-t border-border px-5 py-5">
          <section>
            <h4 class="text-[15px] font-bold text-ink">审查摘要</h4>
            <p class="mt-2 text-sm leading-6 text-muted">{{ record.report.investigation_summary }}</p>
          </section>

          <section>
            <h4 class="text-[15px] font-bold text-ink">判断依据</h4>
            <div class="mt-3 grid grid-cols-1 gap-4 md:grid-cols-3">
              <div>
                <strong class="text-sm text-ink">主要驱动</strong>
                <ul class="mt-1 space-y-1 text-sm leading-6 text-muted">
                  <li v-for="item in record.report.risk_assessment.drivers" :key="item">· {{ item }}</li>
                </ul>
              </div>
              <div>
                <strong class="text-sm text-ink">反向信号</strong>
                <ul class="mt-1 space-y-1 text-sm leading-6 text-muted">
                  <li v-for="item in record.report.risk_assessment.counter_signals" :key="item">· {{ item }}</li>
                  <li v-if="!record.report.risk_assessment.counter_signals.length">· 暂无</li>
                </ul>
              </div>
              <div>
                <strong class="text-sm text-ink">后续监测</strong>
                <ul class="mt-1 space-y-1 text-sm leading-6 text-muted">
                  <li v-for="item in record.report.risk_assessment.watch_items" :key="item">· {{ item }}</li>
                </ul>
              </div>
            </div>
          </section>

          <section>
            <h4 class="text-[15px] font-bold text-ink">确定事实</h4>
            <div class="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
              <article
                v-for="fact in record.report.facts"
                :key="fact.statement"
                class="rounded-lg border border-border p-3"
              >
                <p class="text-sm leading-6 text-muted">{{ fact.statement }}</p>
                <div class="mt-2 flex flex-wrap gap-1.5">
                  <Badge v-for="id in fact.evidence_ids" :key="id" tone="brand">
                    {{ labels.tool[evidenceById(id)?.tool_name] || "证据" }} · {{ evidenceById(id)?.period }}
                  </Badge>
                </div>
              </article>
            </div>
          </section>

          <section>
            <h4 class="text-[15px] font-bold text-ink">证据支持的判断</h4>
            <div class="mt-3 space-y-3">
              <article
                v-for="item in record.report.hypotheses"
                :key="item.hypothesis_id"
                class="rounded-lg border border-border p-3"
              >
                <div class="flex items-start gap-2">
                  <Badge :tone="hypothesisColor(item.status)">{{ labels.hypothesis[item.status] }}</Badge>
                  <strong class="text-sm leading-6 text-ink">{{ item.statement }}</strong>
                </div>
                <p v-if="item.missing_evidence.length" class="mt-1.5 text-sm leading-6 text-warning-deep">
                  <b>仍需补证：</b>{{ item.missing_evidence.join("；") }}
                </p>
              </article>
            </div>
          </section>

          <section>
            <h4 class="text-[15px] font-bold text-ink">数据限制</h4>
            <ul class="mt-2 space-y-1 text-sm leading-6 text-muted">
              <li v-for="item in record.report.limitations" :key="item">· {{ item }}</li>
              <li v-if="!record.report.limitations.length">· 未报告额外限制</li>
            </ul>
          </section>
        </div>
      </details>

      <details class="card">
        <summary class="cursor-pointer select-none px-5 py-4 text-sm font-semibold text-ink">
          查看审查过程与工具证据
        </summary>
        <div class="border-t border-border px-5 py-5">
          <section v-if="record.report.trace?.length">
            <h4 class="text-[15px] font-bold text-ink">执行路径</h4>
            <div class="mt-2 divide-y divide-border">
              <article v-for="item in record.report.trace" :key="item.created_at + item.title" class="py-3">
                <div class="flex items-center gap-2">
                  <strong class="text-sm text-ink">{{ item.title }}</strong>
                  <span class="ml-auto text-sm text-muted">{{ item.created_at }}</span>
                </div>
                <p class="mt-1 text-sm leading-6 text-muted">{{ item.detail }}</p>
              </article>
            </div>
          </section>

          <section class="mt-5 border-t border-border pt-5">
            <h4 class="text-[15px] font-bold text-ink">工具证据</h4>
            <div class="mt-2 divide-y divide-border">
              <article v-for="item in record.evidence" :key="item.evidence_id" class="py-3">
                <div class="flex flex-wrap items-center gap-2">
                  <strong class="text-sm text-ink">{{ labels.tool[item.tool_name] }}</strong>
                  <span class="text-sm text-muted">{{ item.period }}</span>
                </div>
                <p class="mt-1 text-sm leading-6 text-muted">{{ item.summary }}</p>
                <p class="mt-1 font-mono text-sm text-muted">{{ queryArguments(item) }}</p>
                <code class="mt-1 block font-mono text-sm text-muted">证据编号：{{ item.evidence_id }}</code>
              </article>
            </div>
          </section>
        </div>
      </details>
    </template>

    <div v-if="error" class="flex items-start gap-3 rounded-lg border border-danger/30 bg-danger-wash p-4">
      <AlertCircle :size="18" class="mt-0.5 flex-none text-danger" />
      <div>
        <strong class="block text-sm text-danger">本次审查未生成报告</strong>
        <p class="mt-1 text-sm text-danger-deep">{{ error }}</p>
      </div>
    </div>
  </section>
</template>
