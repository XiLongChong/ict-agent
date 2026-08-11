<script setup>
import { computed, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { AlertCircle, ArrowLeft, CalendarDays, CodeXml, LoaderCircle, Radar, Sparkles, UserCheck, Wallet, X } from "lucide-vue-next";
import BrandMark from "./BrandMark.vue";
import Badge from "./ui/Badge.vue";
import Button from "./ui/Button.vue";
import SelectInput from "./ui/SelectInput.vue";
import Tabs from "./ui/Tabs.vue";
import TextArea from "./ui/TextArea.vue";
import TextInput from "./ui/TextInput.vue";
import InvestigationThread from "./InvestigationThread.vue";
import { api, formatMoney, labels, priorityColor, statusColor } from "../lib";
import { loadRiskData, workspace } from "../store";

const route = useRoute();
const router = useRouter();
const caseItem = ref(null);
const loading = ref(true);
const error = ref("");
const tab = ref("investigation");
const submitting = ref(false);
const form = reactive({ decision: "", reviewer: "", reason: "" });
const decisionOptions = [
  { title: "请选择复核结论", value: "" },
  { title: "风险成立，转入处理中", value: "CONFIRMED_RISK" },
  { title: "证据不足，需要重新调查", value: "NEEDS_MORE_EVIDENCE" },
  { title: "确认无风险，关闭案件", value: "NO_RISK" },
];
const canReview = computed(() => caseItem.value?.status === "PENDING_HUMAN_REVIEW");
const canSubmit = computed(() => canReview.value && form.decision && form.reviewer.trim() && form.reason.trim().length >= 2);
const sourcePath = computed(() => {
  const value = typeof route.query.from === "string" ? route.query.from : "";
  return value.startsWith("/risk") || value.startsWith("/cases") ? value : "/cases";
});
const tabs = [
  { value: "investigation", label: "Agent 调查", icon: Sparkles },
  { value: "signals", label: "规则信号", icon: Radar },
  { value: "review", label: "人工复核", icon: UserCheck },
];
const facts = computed(() =>
  caseItem.value
    ? [
        { icon: Wallet, tone: "text-brand-deep bg-brand-wash", label: "风险敞口", value: formatMoney(caseItem.value.exposure_amount) },
        { icon: CalendarDays, tone: "text-muted bg-gray-100", label: "观察日期", value: caseItem.value.observation_date },
        { icon: CodeXml, tone: "text-muted bg-gray-100", label: "规则版本", value: caseItem.value.rule_set_version },
        { icon: Radar, tone: "text-warning-deep bg-warning-wash", label: "规则命中", value: `${caseItem.value.rule_hits.length} 条` },
      ]
    : []
);

async function loadCase(id) {
  loading.value = true;
  error.value = "";
  try {
    caseItem.value = await api(`/api/v1/cases/${encodeURIComponent(id)}`);
    document.title = `${caseItem.value.entity_label} · 案件处理`;
  } catch (exception) {
    error.value = exception.message;
    caseItem.value = null;
  } finally {
    loading.value = false;
  }
}

watch(
  () => route.params.caseId,
  (id) => {
    tab.value = "investigation";
    Object.assign(form, { decision: "", reviewer: "", reason: "" });
    if (id) loadCase(id);
  },
  { immediate: true }
);

async function refresh() {
  if (!route.params.caseId) return;
  await Promise.all([loadCase(route.params.caseId), loadRiskData()]);
}

async function submitReview() {
  if (!caseItem.value || !canSubmit.value) return;
  submitting.value = true;
  try {
    await api(`/api/v1/cases/${encodeURIComponent(caseItem.value.case_id)}/reviews`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        decision: form.decision,
        reviewer: form.reviewer.trim(),
        reason: form.reason.trim(),
      }),
    });
    Object.assign(form, { decision: "", reason: "" });
    await refresh();
  } catch (exception) {
    workspace.status = { text: exception.message, error: true };
  } finally {
    submitting.value = false;
  }
}

function reviewLabel(decision) {
  return ({ CONFIRMED_RISK: "风险成立", NEEDS_MORE_EVIDENCE: "需补充调查", NO_RISK: "确认无风险" })[decision] || decision;
}

function returnToSource() {
  if (window.opener && !window.opener.closed) {
    window.opener.focus();
  }
  window.close();
  window.setTimeout(() => {
    if (!window.closed) router.replace(sourcePath.value);
  }, 100);
}
</script>

<template>
  <div class="space-y-4">
    <header class="flex min-h-[64px] flex-wrap items-center gap-3 rounded-xl border border-border bg-surface px-4 py-3 shadow-sm md:px-5">
      <BrandMark />
      <div class="leading-tight">
        <strong class="block text-[15px] text-ink">佳华智审</strong>
        <span class="text-sm text-muted">案件处理</span>
      </div>
      <div class="flex-1"></div>
      <button
        type="button"
        class="inline-flex h-10 items-center gap-2 rounded-lg border border-border px-4 text-sm font-semibold text-muted transition-colors hover:bg-canvas hover:text-ink"
        @click="returnToSource"
      >
        <X :size="17" />
        返回并关闭
      </button>
    </header>

    <section class="card overflow-hidden">
    <header class="flex flex-wrap items-center gap-3 px-4 py-4 md:px-5">
      <div class="leading-tight">
        <h2 class="text-xl font-bold text-ink">{{ caseItem ? caseItem.entity_label : "案件详情" }}</h2>
        <span v-if="caseItem" class="block text-sm text-muted">{{ labels.caseType[caseItem.case_type] }} · {{ caseItem.case_id }}</span>
      </div>
      <div class="flex-1"></div>
      <template v-if="caseItem">
        <Badge :tone="priorityColor(caseItem.priority)">{{ labels.priority[caseItem.priority] }}优先级</Badge>
        <Badge :tone="statusColor(caseItem.status)">{{ labels.status[caseItem.status] }}</Badge>
      </template>
    </header>

    <div v-if="loading" class="grid min-h-[360px] place-content-center justify-items-center gap-3 border-t border-border text-muted">
      <LoaderCircle :size="28" class="animate-spin text-brand" />
      <span class="text-sm">正在装载案件、证据和审核记录</span>
    </div>

    <div v-else-if="error" class="grid min-h-[360px] place-content-center justify-items-center gap-3 border-t border-border text-center">
      <AlertCircle :size="42" class="text-danger" />
      <h3 class="text-lg font-bold text-ink">案件加载失败</h3>
      <p class="text-sm text-muted">{{ error }}</p>
      <Button @click="returnToSource"><ArrowLeft :size="16" /> 返回来源页面</Button>
    </div>

    <template v-else-if="caseItem">
      <div class="grid grid-cols-2 gap-3 border-t border-border bg-canvas p-4 md:grid-cols-4">
        <div v-for="f in facts" :key="f.label" class="flex min-w-0 items-center gap-3 rounded-lg border border-border bg-surface px-4 py-3">
          <span class="grid h-9 w-9 flex-none place-items-center rounded-lg" :class="f.tone"><component :is="f.icon" :size="16" /></span>
          <div class="min-w-0">
            <span class="block text-sm text-muted">{{ f.label }}</span>
            <strong class="block truncate text-base text-ink">{{ f.value }}</strong>
          </div>
        </div>
      </div>
    </template>
    </section>

    <template v-if="!loading && !error && caseItem">
      <section class="card overflow-hidden">
      <Tabs v-model="tab" :tabs="tabs" />

      <div v-if="tab === 'investigation'" class="bg-surface">
        <InvestigationThread :case-item="caseItem" @completed="refresh" />
      </div>

      <div v-else-if="tab === 'signals'" class="mx-auto w-full max-w-[1200px] px-5 py-6">
        <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
          <section v-for="hit in caseItem.rule_hits" :key="hit.rule_hit_id" class="card p-4">
            <div class="flex items-center justify-between">
              <span class="font-mono text-sm font-semibold text-brand-deep">{{ hit.rule_id }}</span>
              <Badge :tone="priorityColor(hit.severity)">{{ labels.priority[hit.severity] }}</Badge>
            </div>
            <h3 class="mt-2 text-[15px] font-semibold text-ink">{{ hit.rule_name }}</h3>
            <p class="mt-1 text-[13px] leading-6 text-muted">{{ hit.reason }}</p>
            <span class="mt-2 block text-sm text-muted">{{ hit.sources.join(" / ") }} · {{ hit.period }}</span>
          </section>
        </div>
      </div>

      <div v-else class="mx-auto grid w-full max-w-[1100px] grid-cols-1 gap-6 px-5 py-6 md:grid-cols-[380px_1fr]">
        <section class="card h-fit p-5">
          <h2 class="text-lg font-bold text-ink">提交人工复核</h2>
          <p v-if="!canReview" class="mt-3 rounded-lg bg-canvas px-3 py-2 text-sm leading-6 text-muted">
            当前状态为“{{ labels.status[caseItem.status] }}”，无需提交人工复核。
          </p>
          <div v-else class="mt-4 space-y-4">
            <SelectInput v-model="form.decision" :options="decisionOptions" />
            <TextInput v-model="form.reviewer" maxlength="100" placeholder="审核人" />
            <TextArea v-model="form.reason" rows="3" maxlength="1000" placeholder="说明判断依据" />
            <Button block :disabled="!canSubmit" :loading="submitting" @click="submitReview">提交复核结论</Button>
          </div>
        </section>

        <section>
          <h2 class="mb-4 text-lg font-bold text-ink">复核历史</h2>
          <div class="space-y-3">
            <article v-for="review in caseItem.reviews" :key="review.review_id" class="card p-4">
              <div class="flex items-center gap-2">
                <strong class="text-sm text-ink">{{ review.reviewer }}</strong>
                <Badge tone="neutral">{{ reviewLabel(review.decision) }}</Badge>
                <span class="ml-auto text-sm text-muted">{{ review.created_at }}</span>
              </div>
              <p class="mt-2 text-sm leading-6 text-muted">{{ review.reason }}</p>
            </article>
            <div v-if="!caseItem.reviews.length" class="card empty-state">还没有人工复核记录</div>
          </div>
        </section>
      </div>
      </section>
    </template>
  </div>
</template>
