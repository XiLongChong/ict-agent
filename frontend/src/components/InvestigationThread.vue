<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { AlertCircle, CheckCircle2, Database, Download, PlayCircle, Search, ShieldCheck, Sparkles } from "lucide-vue-next";
import Badge from "./ui/Badge.vue";
import HighlightText from "./HighlightText.vue";
import { api, labels, priorityColor, queryArguments, streamNdjson } from "../lib";

const props = defineProps({ caseItem: Object });
const emit = defineEmits(["completed"]);
const running = ref(false);
const events = ref([]);
const record = ref(props.caseItem?.latest_investigation || null);
const error = ref("");
const canInvestigate = computed(() => props.caseItem?.status === "PENDING_AGENT_REVIEW");
const protocol = ref(null);
const protocolLoading = ref(false);
const protocolError = ref("");
const protocolRequestJson = computed(() =>
  protocol.value?.request ? JSON.stringify(protocol.value.request, null, 2) : ""
);
const protocolResponseJson = computed(() =>
  protocol.value?.response_summary ? JSON.stringify(protocol.value.response_summary, null, 2) : ""
);
const protocolDownloadUrl = computed(() =>
  record.value?.investigation_id
    ? `/api/v1/investigations/${encodeURIComponent(record.value.investigation_id)}/protocol/download`
    : ""
);
const protocolDownloadFilename = computed(() => {
  const caseId = String(props.caseItem?.case_id || record.value?.case_id || "case").replace(/[^a-zA-Z0-9_-]/g, "-");
  const requestIndex = protocol.value?.request_index || 1;
  return `${caseId}-request-${requestIndex}-deepseek-chat-completions.json`;
});
const missingEvidence = computed(() => [
  ...new Set((record.value?.report?.possibility_assessments || []).flatMap((item) => item.missing_evidence || [])),
]);
const report = computed(() => record.value?.report || null);
const conclusionText = computed(() => {
  const summary = String(report.value?.executive_summary || "").trim();
  const statement = String(report.value?.risk_assessment?.statement || "").trim();
  if (!statement || statement === summary) return summary;
  return [summary, statement].join("\n\n");
});

function toolName(toolName) {
  return labels.tool[toolName] || toolName || "";
}
function periodLabel(item) {
  return item?.period || "";
}
function evidenceSignature(item) {
  return [toolName(item?.tool_name), periodLabel(item)].filter(Boolean).join(" · ");
}
function evidenceRefs(ids) {
  const index = new Map((record.value?.evidence || []).map((item) => [item.evidence_id, item]));
  return (ids || []).map((id) => index.get(id) || id);
}

function likelihoodLabel(item) {
  const lower = item?.likelihood?.lower_percent;
  const upper = item?.likelihood?.upper_percent;
  if (lower == null || upper == null) return "可能性未评估";
  return `${lower}%–${upper}%`;
}
function likelihoodTone(lower, upper) {
  const midpoint = (lower + upper) / 2;
  if (midpoint >= 70) return "danger";
  if (midpoint <= 30) return "success";
  return "warning";
}
function likelihoodDescription(lower, upper) {
  if (lower >= 60) return "可能性较高";
  if (upper <= 40) return "可能性较低";
  if (lower >= 40) return "可能性偏高，但不确定性大";
  if (upper <= 60) return "可能性偏低，但不确定性大";
  return "方向不明确";
}
function ownerLabel(value) {
  return ({ customer_manager: "客户经理", risk_reviewer: "风险复核人", credit_manager: "授信经理", collection_specialist: "催收专员", legal_counsel: "法务", inventory_manager: "库存管理员", sales_manager: "销售经理", case_reviewer: "案件复核人", reviewer: "复核人" }[value]) || value;
}
function urgencyLabel(value) {
  return ({ IMMEDIATE: "立即", SHORT_TERM: "短期", MONITOR: "持续监测" })[value] || value;
}

watch(
  () => props.caseItem,
  (value) => {
    record.value = value?.latest_investigation || null;
    events.value = [];
    error.value = "";
    protocol.value = null;
    protocolLoading.value = false;
    protocolError.value = "";
  },
  { deep: false }
);

let scrollFrame = 0;
let scrollScheduled = false;
onBeforeUnmount(() => {
  if (scrollFrame) cancelAnimationFrame(scrollFrame);
  scrollScheduled = false;
});

async function scrollToBottom() {
  if (scrollScheduled) return;
  scrollScheduled = true;
  await nextTick();
  scrollFrame = requestAnimationFrame(() => {
    window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "auto" });
    scrollFrame = 0;
    scrollScheduled = false;
  });
}

async function loadProtocol(event) {
  if (!event.currentTarget.open || protocol.value || protocolLoading.value || !record.value?.investigation_id) return;
  const investigationId = record.value.investigation_id;
  protocolLoading.value = true;
  protocolError.value = "";
  try {
    const value = await api(`/api/v1/investigations/${encodeURIComponent(investigationId)}/protocol`);
    if (value.schema_version !== "4.0" || value.api_format !== "openai_chat_completions") {
      throw new Error("该调查协议不是当前 Chat Completions 格式。");
    }
    if (record.value?.investigation_id === investigationId) protocol.value = value;
  } catch (exception) {
    if (record.value?.investigation_id === investigationId) protocolError.value = exception.message;
  } finally {
    if (record.value?.investigation_id === investigationId) protocolLoading.value = false;
  }
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
</script>

<template>
  <section class="w-full space-y-4 pb-10" aria-live="polite">
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
        <h3 class="text-[0.9375rem] font-bold text-ink">审查进度</h3>
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
      <h3 class="mt-3 text-[0.9375rem] font-bold text-ink">等待开始AI审查</h3>
    </section>

    <template v-if="record">
      <article class="card overflow-hidden">
        <header class="flex flex-wrap items-center gap-3 border-b border-border px-5 py-4">
          <CheckCircle2 :size="20" class="text-success" />
          <h3 class="text-lg font-bold text-ink">AI审查报告</h3>
        </header>

        <div class="space-y-6 p-5">
          <section>
            <h4 class="text-[0.9375rem] font-bold text-ink">审查结论</h4>
            <p class="mt-2 whitespace-pre-line text-[0.9375rem] leading-7 text-ink">
              <HighlightText :text="conclusionText" />
            </p>
          </section>

          <section class="border-t border-border pt-5">
            <div class="flex items-center gap-2">
              <h4 class="text-[0.9375rem] font-bold text-ink">处置建议</h4>
              <Badge :tone="priorityColor(record.report.recommended_priority)">
                建议{{ labels.priority[record.report.recommended_priority] }}优先级
              </Badge>
            </div>
            <div class="mt-3 rounded-lg bg-brand-wash px-4 py-3 text-sm leading-6 text-brand-deep">
              <b>总体处置：</b>{{ record.report.risk_assessment.management_posture }}
            </div>
            <ol class="mt-3 space-y-3">
              <li
                v-for="(item, index) in record.report.recommended_actions"
                :key="item.action + item.owner"
                class="grid grid-cols-[24px_minmax(0,1fr)] gap-2 text-sm leading-6 text-muted"
              >
                <span class="grid h-6 w-6 place-items-center rounded-full bg-brand-wash text-sm font-semibold text-brand-deep">
                  {{ index + 1 }}
                </span>
                <span>
                  <b>{{ ownerLabel(item.owner) }} · {{ urgencyLabel(item.urgency) }}：</b>{{ item.action }}
                  <span class="block text-muted">{{ item.rationale }}</span>
                  <span class="block text-muted"><b>完成依据：</b>{{ item.completion_evidence }}</span>
                </span>
              </li>
            </ol>
            <ul class="mt-3 space-y-1 text-sm leading-6 text-muted">
              <li v-for="item in record.report.risk_assessment.watch_items" :key="item">
                <b>持续监测：</b>{{ item }}
              </li>
              <li v-if="!record.report.risk_assessment.watch_items?.length">
                <b>持续监测：</b>暂无
              </li>
            </ul>
          </section>

          <section v-if="report?.possibility_assessments?.length" class="border-t border-border pt-5">
            <h4 class="text-[0.9375rem] font-bold text-ink">可能性分析</h4>
            <p class="mt-1 text-xs leading-5 text-muted">AI基于本案证据的推断，可能性为未校准模型估计，不是历史统计违约率。</p>
            <div class="mt-2 divide-y divide-border">
              <article v-for="item in report.possibility_assessments" :key="item.assessment_id" class="py-3">
                <div class="flex flex-wrap items-start gap-2">
                  <Badge :tone="likelihoodTone(item.likelihood?.lower_percent ?? 50, item.likelihood?.upper_percent ?? 50)">
                    {{ likelihoodDescription(item.likelihood?.lower_percent ?? 50, item.likelihood?.upper_percent ?? 50) }} · {{ likelihoodLabel(item) }}
                  </Badge>
                  <strong class="text-sm leading-6 text-ink">{{ item.possibility }}</strong>
                </div>
                <p class="mt-1.5 text-sm leading-6 text-muted">{{ item.rationale }}</p>
                <div class="mt-1.5 space-y-1 text-sm leading-6">
                  <p
                    v-for="entry in evidenceRefs(item.supporting_evidence_ids)"
                    :key="'support-' + (entry?.evidence_id || entry)"
                    class="flex flex-wrap items-center gap-1.5"
                  >
                    <Badge tone="danger">支持</Badge>
                    <span class="text-muted">{{ evidenceSignature(entry) }}</span>
                    <span v-if="entry?.summary" class="text-muted">· {{ entry.summary }}</span>
                  </p>
                  <p
                    v-for="entry in evidenceRefs(item.contradicting_evidence_ids)"
                    :key="'contra-' + (entry?.evidence_id || entry)"
                    class="flex flex-wrap items-center gap-1.5"
                  >
                    <Badge tone="success">反驳</Badge>
                    <span class="text-muted">{{ evidenceSignature(entry) }}</span>
                    <span v-if="entry?.summary" class="text-muted">· {{ entry.summary }}</span>
                  </p>
                </div>
                <p class="mt-1.5 text-sm leading-6 text-ink">
                  <b>业务影响：</b>{{ item.business_implication }}
                </p>
                <p v-if="item.missing_evidence?.length" class="mt-1.5 text-sm leading-6 text-warning-deep">
                  <b>仍需补证：</b>{{ item.missing_evidence.join("；") }}
                </p>
              </article>
            </div>
          </section>

          <section class="border-t border-border pt-5">
            <h4 class="text-[0.9375rem] font-bold text-ink">证据分析</h4>
            <div class="mt-3 grid grid-cols-1 gap-5 md:grid-cols-2">
              <div>
                <strong class="text-sm text-danger">支持本案存在风险</strong>
                <ul class="mt-1 space-y-1 text-sm leading-6 text-muted">
                  <li v-for="item in record.report.risk_assessment.drivers" :key="item">· {{ item }}</li>
                  <li v-if="!record.report.risk_assessment.drivers?.length">· 暂无</li>
                </ul>
              </div>
              <div>
                <strong class="text-sm text-success">支持本案不存在或风险较轻</strong>
                <ul class="mt-1 space-y-1 text-sm leading-6 text-muted">
                  <li v-for="item in record.report.risk_assessment.counter_signals" :key="item">· {{ item }}</li>
                  <li v-if="!record.report.risk_assessment.counter_signals?.length">· 暂无</li>
                </ul>
              </div>
            </div>
          </section>

          <section class="border-t border-border pt-5">
            <h4 class="text-[0.9375rem] font-bold text-ink">关键事实</h4>
            <div class="mt-3 divide-y divide-border border-y border-border">
              <article v-for="fact in record.report.facts" :key="fact.statement" class="py-3">
                <p class="text-sm leading-6 text-ink">{{ fact.statement }}</p>
                <div class="mt-2 flex flex-wrap gap-1.5">
                  <Badge v-for="id in fact.evidence_ids" :key="id" tone="brand">
                    {{ evidenceSignature(evidenceById(id)) || "证据" }}
                  </Badge>
                </div>
              </article>
            </div>
          </section>

          <section v-if="record.report.data_conflicts?.length" class="border-t border-border pt-5">
            <h4 class="text-[0.9375rem] font-bold text-ink">关键数据冲突</h4>
            <div class="mt-2 divide-y divide-border">
              <article v-for="item in record.report.data_conflicts" :key="item.statement" class="py-3">
                <strong class="text-sm leading-6 text-ink">{{ item.statement }}</strong>
                <p class="mt-1 text-sm leading-6 text-warning-deep"><b>决策影响：</b>{{ item.decision_impact }}</p>
              </article>
            </div>
          </section>

          <section class="border-t border-border pt-5">
            <h4 class="text-[0.9375rem] font-bold text-ink">待补证据与限制</h4>
            <div class="mt-3 grid grid-cols-1 gap-5 md:grid-cols-2">
              <div>
                <strong class="text-sm text-ink">待补证据</strong>
                <ul class="mt-1 space-y-1 text-sm leading-6 text-muted">
                  <li v-for="item in missingEvidence" :key="item">· {{ item }}</li>
                  <li v-if="!missingEvidence.length">· 暂无</li>
                </ul>
              </div>
              <div>
                <strong class="text-sm text-ink">数据限制</strong>
                <ul class="mt-1 space-y-1 text-sm leading-6 text-muted">
                  <li v-for="item in record.report.limitations" :key="item">· {{ item }}</li>
                  <li v-if="!record.report.limitations.length">· 未报告额外限制</li>
                </ul>
              </div>
            </div>
          </section>

          <p class="rounded-lg bg-warning-wash px-4 py-3 text-sm leading-6 text-warning-deep">
            AI结论用于辅助判断，请结合下方可复核依据完成人工复核。
          </p>
        </div>
      </article>

      <details class="card">
        <summary class="cursor-pointer select-none px-5 py-4 text-sm font-semibold text-ink">
          查看审查过程与工具证据
        </summary>
        <div class="border-t border-border px-5 py-5">
          <section v-if="record.report.trace?.length">
            <h4 class="text-[0.9375rem] font-bold text-ink">执行路径</h4>
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
            <h4 class="text-[0.9375rem] font-bold text-ink">工具证据</h4>
            <div class="mt-2 divide-y divide-border">
              <article v-for="item in record.evidence" :key="item.evidence_id" class="py-3">
                <div class="flex flex-wrap items-center gap-2">
                  <strong class="text-sm text-ink">{{ labels.tool[item.tool_name] }}</strong>
                  <span class="text-sm text-muted">{{ item.period }}</span>
                </div>
                <p class="mt-1 text-sm leading-6 text-muted">{{ item.summary }}</p>
                <p class="mt-1 font-mono text-sm text-muted">{{ queryArguments(item) }}</p>
                <p class="mt-1 text-sm text-muted">
                  共 {{ item.total_rows ?? item.rows?.length ?? 0 }} 行，实际返回
                  {{ item.returned_rows ?? item.rows?.length ?? 0 }} 行<span v-if="item.is_truncated">，结果已截断</span><span v-else>，结果完整</span>
                </p>
                <code class="mt-1 block font-mono text-sm text-muted">证据编号：{{ item.evidence_id }}</code>
              </article>
            </div>
          </section>
        </div>
      </details>

      <details v-if="record.protocol_available" class="card" @toggle="loadProtocol">
        <summary class="cursor-pointer select-none px-5 py-4 text-sm font-semibold text-ink">
          查看 DeepSeek Chat Completions HTTP JSON
        </summary>
        <div class="border-t border-border px-5 py-5">
          <p v-if="protocolLoading" class="text-sm leading-6 text-muted">正在加载本轮 HTTP 请求摘要…</p>
          <p v-else-if="protocolError" class="text-sm leading-6 text-danger">{{ protocolError }}</p>
          <template v-else-if="protocol">
            <div class="mb-3 flex flex-wrap items-center gap-2">
              <div class="flex min-w-0 flex-1 flex-wrap items-center gap-2 text-sm text-muted">
                <span>最后一次真实 HTTP 请求与响应</span>
                <Badge tone="neutral">Chat Completions API</Badge>
                <Badge tone="neutral">第 {{ protocol.request_index }} 次请求</Badge>
                <Badge tone="neutral">{{ protocol.request.body.model }}</Badge>
                <Badge v-if="protocol.capture_source === 'wire'" tone="neutral">HTTP 原始抓取</Badge>
              </div>
              <a
                :href="protocolDownloadUrl"
                :download="protocolDownloadFilename"
                data-testid="download-investigation-protocol-json"
                class="inline-flex h-9 flex-none items-center gap-2 rounded-lg border border-border px-3 text-sm font-semibold text-muted transition-colors hover:bg-canvas hover:text-ink"
              >
                <Download :size="16" />
                下载 JSON
              </a>
            </div>
            <p class="mb-4 break-all font-mono text-xs text-muted">
              {{ protocol.request.method }} {{ protocol.request.url }}
            </p>
            <div class="space-y-5">
              <section>
                <h4 class="mb-2 text-sm font-semibold text-ink">HTTP 请求</h4>
                <pre
                  data-testid="investigation-protocol-request-json"
                  class="max-h-[65vh] overflow-auto rounded-lg bg-[#101828] p-4 font-mono text-xs leading-5 text-[#d0d5dd]"
                ><code>{{ protocolRequestJson }}</code></pre>
              </section>
              <section>
                <h4 class="mb-2 text-sm font-semibold text-ink">HTTP 响应摘要</h4>
                <pre
                  data-testid="investigation-protocol-response-json"
                  class="max-h-[65vh] overflow-auto rounded-lg bg-[#101828] p-4 font-mono text-xs leading-5 text-[#d0d5dd]"
                ><code>{{ protocolResponseJson || "本轮没有取得 DeepSeek Chat Completions HTTP 响应。" }}</code></pre>
                <p class="mt-2 text-xs leading-5 text-muted">完整原始响应仅在点击“下载 JSON”时由后端读取，不进入页面渲染。</p>
              </section>
            </div>
          </template>
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
