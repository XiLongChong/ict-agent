<script setup>
import { computed, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  AlertCircle,
  ArrowLeft,
  CalendarDays,
  CodeXml,
  FileText,
  LoaderCircle,
  Radar,
  ShieldAlert,
  Sparkles,
  UserCheck,
  Wallet,
  X,
} from "lucide-vue-next";
import Badge from "./ui/Badge.vue";
import BrandMark from "./BrandMark.vue";
import Button from "./ui/Button.vue";
import SelectInput from "./ui/SelectInput.vue";
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
const section = ref("overview");
const submitting = ref(false);
const form = reactive({ decision: "", reviewer: "", reason: "" });
const decisionOptions = [
  { title: "请选择复核结论", value: "" },
  { title: "风险成立，转入处理中", value: "CONFIRMED_RISK" },
  { title: "证据不足，需要重新审查", value: "NEEDS_MORE_EVIDENCE" },
  { title: "确认无风险，关闭案件", value: "NO_RISK" },
];
const canReview = computed(() => caseItem.value?.status === "PENDING_HUMAN_REVIEW");
const canSubmit = computed(
  () =>
    canReview.value &&
    form.decision &&
    form.reviewer.trim() &&
    form.reason.trim().length >= 2
);
const sourcePath = computed(() => {
  const value = typeof route.query.from === "string" ? route.query.from : "";
  return value.startsWith("/risk") || value.startsWith("/cases") ? value : "/cases";
});
const sections = [
  { value: "overview", label: "案件概况", icon: FileText },
  { value: "investigation", label: "AI审查", icon: Sparkles },
  { value: "review", label: "人工复核", icon: UserCheck },
];
const facts = computed(() =>
  caseItem.value
    ? [
        {
          icon: ShieldAlert,
          tone: "text-danger bg-danger-wash",
          label: "风险等级",
          value: labels.priority[caseItem.value.priority],
        },
        {
          icon: Wallet,
          tone: "text-brand-deep bg-brand-wash",
          label: "风险敞口",
          value: formatMoney(caseItem.value.exposure_amount),
        },
        {
          icon: CalendarDays,
          tone: "text-muted bg-gray-100",
          label: "观察日期",
          value: caseItem.value.observation_date,
        },
        {
          icon: CodeXml,
          tone: "text-muted bg-gray-100",
          label: "规则版本",
          value: caseItem.value.rule_set_version,
        },
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
    section.value = "overview";
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
  return (
    {
      CONFIRMED_RISK: "风险成立",
      NEEDS_MORE_EVIDENCE: "需补充审查",
      NO_RISK: "确认无风险",
    }[decision] || decision
  );
}

function returnToSource() {
  if (window.opener && !window.opener.closed) window.opener.focus();
  window.close();
  window.setTimeout(() => {
    if (!window.closed) router.replace(sourcePath.value);
  }, 100);
}
</script>

<template>
  <div class="min-h-screen bg-canvas md:pl-[200px]">
    <aside
      class="fixed inset-y-0 left-0 z-40 hidden w-[200px] flex-col overflow-hidden border-r border-border bg-surface md:flex"
    >
      <div class="flex h-[72px] items-center px-3">
        <span class="grid h-9 w-10 flex-none place-items-center">
          <BrandMark />
        </span>
        <strong class="ml-2 whitespace-nowrap text-[15px] text-ink">佳华智审</strong>
      </div>

      <nav class="flex-1 space-y-1 overflow-y-auto px-3 py-4" aria-label="案件处理导航">
        <button
          v-for="item in sections"
          :key="item.value"
          type="button"
          class="relative flex h-11 w-full items-center rounded-lg text-[13px] font-semibold transition-colors"
          :class="
            section === item.value
              ? 'bg-brand-wash text-brand-deep'
              : 'text-muted hover:bg-canvas hover:text-brand'
          "
          @click="section = item.value"
        >
          <span
            v-if="section === item.value"
            class="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r bg-brand"
          ></span>
          <span class="grid h-11 w-10 flex-none place-items-center">
            <component :is="item.icon" :size="18" />
          </span>
          <span class="ml-2 whitespace-nowrap">{{ item.label }}</span>
        </button>
      </nav>
    </aside>

    <header
      class="sticky top-0 z-30 flex h-[72px] items-center gap-3 border-b border-border bg-surface/95 px-4 backdrop-blur md:px-6"
    >
      <h1 class="text-[15px] font-bold text-ink">案件处理</h1>
      <div class="flex-1"></div>
      <template v-if="caseItem">
        <Badge :tone="statusColor(caseItem.status)">{{ labels.status[caseItem.status] }}</Badge>
        <Badge tone="neutral">规则命中 {{ caseItem.rule_hits.length }} 条</Badge>
      </template>
      <button
        type="button"
        class="inline-flex h-10 items-center gap-2 rounded-lg border border-border px-4 text-sm font-semibold text-muted transition-colors hover:bg-canvas hover:text-ink"
        @click="returnToSource"
      >
        <X :size="17" />
        <span class="hidden sm:inline">返回并关闭</span>
      </button>
    </header>

    <nav
      class="flex gap-1 overflow-x-auto border-b border-border bg-surface px-3 py-2 md:hidden"
      aria-label="案件处理导航"
    >
      <button
        v-for="item in sections"
        :key="item.value"
        type="button"
        class="flex h-10 flex-none items-center gap-2 rounded-lg px-3 text-sm font-semibold transition-colors"
        :class="
          section === item.value
            ? 'bg-brand-wash text-brand-deep'
            : 'text-muted hover:bg-canvas hover:text-brand'
        "
        @click="section = item.value"
      >
        <component :is="item.icon" :size="17" />
        {{ item.label }}
      </button>
    </nav>

    <div class="mx-auto w-full max-w-[1536px] px-4 py-7 md:px-8">
      <section
        v-if="loading"
        class="card grid min-h-[420px] place-content-center justify-items-center gap-3 text-muted"
      >
        <LoaderCircle :size="28" class="animate-spin text-brand" />
        <span class="text-sm">正在装载案件、证据和复核记录</span>
      </section>

      <section
        v-else-if="error"
        class="card grid min-h-[420px] place-content-center justify-items-center gap-3 text-center"
      >
        <AlertCircle :size="42" class="text-danger" />
        <h2 class="text-lg font-bold text-ink">案件加载失败</h2>
        <p class="text-sm text-muted">{{ error }}</p>
        <Button @click="returnToSource"><ArrowLeft :size="16" /> 返回来源页面</Button>
      </section>

      <main v-else-if="caseItem" class="min-w-0">
        <div v-if="section === 'overview'" class="space-y-4">
          <section class="card overflow-hidden">
            <header class="flex flex-wrap items-center gap-3 px-5 py-5">
              <div class="leading-tight">
                <h2 class="text-xl font-bold text-ink">{{ caseItem.entity_label }}</h2>
                <span class="block text-sm text-muted">
                  {{ labels.caseType[caseItem.case_type] }} · {{ caseItem.case_id }}
                </span>
              </div>
            </header>

            <div class="grid grid-cols-2 gap-3 border-t border-border bg-canvas p-4 xl:grid-cols-4">
              <div
                v-for="fact in facts"
                :key="fact.label"
                class="flex min-w-0 items-center gap-3 rounded-lg border border-border bg-surface px-4 py-3"
              >
                <span
                  class="grid h-9 w-9 flex-none place-items-center rounded-lg"
                  :class="fact.tone"
                >
                  <component :is="fact.icon" :size="16" />
                </span>
                <div class="min-w-0">
                  <span class="block text-sm text-muted">{{ fact.label }}</span>
                  <strong class="block truncate text-base text-ink">{{ fact.value }}</strong>
                </div>
              </div>
            </div>

            <div class="border-t border-border px-5 py-4">
              <h3 class="text-[15px] font-bold text-ink">风险概况</h3>
              <p class="mt-1 text-sm leading-6 text-muted">{{ caseItem.risk_overview }}</p>
            </div>
          </section>

          <section>
            <div class="mb-3 flex items-center gap-2">
              <Radar :size="18" class="text-brand" />
              <h2 class="text-lg font-bold text-ink">规则信号</h2>
            </div>
            <div class="grid grid-cols-1 gap-3 xl:grid-cols-2">
              <article v-for="hit in caseItem.rule_hits" :key="hit.rule_hit_id" class="card p-4">
                <div class="flex items-center justify-between gap-3">
                  <span class="font-mono text-sm font-semibold text-brand-deep">
                    {{ hit.rule_id }}
                  </span>
                  <Badge :tone="priorityColor(hit.severity)">
                    {{ labels.priority[hit.severity] }}
                  </Badge>
                </div>
                <h3 class="mt-2 text-[15px] font-semibold text-ink">{{ hit.rule_name }}</h3>
                <p class="mt-1 text-[13px] leading-6 text-muted">{{ hit.reason }}</p>
                <span class="mt-2 block text-sm text-muted">
                  {{ hit.sources.join(" / ") }} · {{ hit.period }}
                </span>
              </article>
            </div>
          </section>
        </div>

        <section v-else-if="section === 'investigation'" class="card overflow-hidden bg-surface">
          <InvestigationThread :case-item="caseItem" @completed="refresh" />
        </section>

        <div v-else class="grid grid-cols-1 gap-4 xl:grid-cols-[380px_minmax(0,1fr)]">
          <section class="card h-fit p-5">
            <h2 class="text-lg font-bold text-ink">提交人工复核</h2>
            <p
              v-if="!canReview"
              class="mt-3 rounded-lg bg-canvas px-3 py-2 text-sm leading-6 text-muted"
            >
              当前状态为“{{ labels.status[caseItem.status] }}”，无需提交人工复核。
            </p>
            <div v-else class="mt-4 space-y-4">
              <SelectInput v-model="form.decision" :options="decisionOptions" />
              <TextInput v-model="form.reviewer" maxlength="100" placeholder="审核人" />
              <TextArea
                v-model="form.reason"
                rows="3"
                maxlength="1000"
                placeholder="说明判断依据"
              />
              <Button
                block
                :disabled="!canSubmit"
                :loading="submitting"
                @click="submitReview"
              >
                提交复核结论
              </Button>
            </div>
          </section>

          <section>
            <h2 class="mb-3 text-lg font-bold text-ink">复核历史</h2>
            <div class="space-y-3">
              <article v-for="review in caseItem.reviews" :key="review.review_id" class="card p-4">
                <div class="flex items-center gap-2">
                  <strong class="text-sm text-ink">{{ review.reviewer }}</strong>
                  <Badge tone="neutral">{{ reviewLabel(review.decision) }}</Badge>
                  <span class="ml-auto text-sm text-muted">{{ review.created_at }}</span>
                </div>
                <p class="mt-2 text-sm leading-6 text-muted">{{ review.reason }}</p>
              </article>
              <div v-if="!caseItem.reviews.length" class="card empty-state">
                还没有人工复核记录
              </div>
            </div>
          </section>
        </div>
      </main>
    </div>
  </div>
</template>
