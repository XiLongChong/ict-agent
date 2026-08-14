<script setup>
import { computed, ref, watch } from "vue";
import { ChevronLeft, ChevronRight } from "lucide-vue-next";
import Badge from "./ui/Badge.vue";
import Button from "./ui/Button.vue";
import Modal from "./ui/Modal.vue";
import SelectInput from "./ui/SelectInput.vue";
import { formatAmountTier, formatMoneyWan } from "../lib";
import { runPreAssessment, workspace } from "../store";

const assessing = ref(null);
const assessment = ref(null);
const listFilter = ref("");
const tierFilter = ref("");
const pageSize = ref("10");
const currentPage = ref(1);
const pageJump = ref("1");

const listOptions = [
  { title: "全部名单", value: "" },
  { title: "白名单", value: "白名单" },
  { title: "一般", value: "一般" },
  { title: "黑名单", value: "黑名单" },
];
const tierOptions = [
  { title: "全部档位", value: "" },
  { title: "<300", value: "<300" },
  { title: "300~500", value: "300~500" },
  { title: "500~700", value: "500~700" },
  { title: ">=700", value: ">=700" },
];
const pageSizeOptions = [10, 20, 50].map((value) => ({ title: `${value} 行/页`, value: String(value) }));

const projects = computed(() =>
  (workspace.projects || []).filter((item) => String(item.project_id || "").startsWith("P2026-"))
);
const filtered = computed(() =>
  projects.value.filter(
    (item) =>
      (!listFilter.value || item.customer_list === listFilter.value) &&
      (!tierFilter.value || item.amount_tier === tierFilter.value)
  )
);
const pageSizeValue = computed(() => Number(pageSize.value));
const totalPages = computed(() => Math.max(1, Math.ceil(filtered.value.length / pageSizeValue.value)));
const paginated = computed(() => {
  const start = (currentPage.value - 1) * pageSizeValue.value;
  return filtered.value.slice(start, start + pageSizeValue.value);
});
const pageNumbers = computed(() => {
  const visibleCount = Math.min(5, totalPages.value);
  let start = Math.max(1, currentPage.value - 2);
  start = Math.min(start, totalPages.value - visibleCount + 1);
  return Array.from({ length: visibleCount }, (_, index) => start + index);
});
const rangeStart = computed(() => (filtered.value.length ? (currentPage.value - 1) * pageSizeValue.value + 1 : 0));
const rangeEnd = computed(() => Math.min(currentPage.value * pageSizeValue.value, filtered.value.length));

watch([listFilter, tierFilter, pageSize], () => goToPage(1));
watch(totalPages, (total) => {
  if (currentPage.value > total) goToPage(total);
});

async function assess(item) {
  assessing.value = item.project_id;
  try {
    assessment.value = await runPreAssessment(item.project_id);
  } catch {
    assessment.value = null;
  } finally {
    assessing.value = null;
  }
}

function goToPage(page) {
  const normalized = Math.min(totalPages.value, Math.max(1, Math.trunc(Number(page) || 1)));
  currentPage.value = normalized;
  pageJump.value = String(normalized);
}

function jumpToPage() {
  goToPage(pageJump.value);
}

const conclusionTone = (conclusion) =>
  ({ "正常通过": "success", "有条件通过": "warning", "需要人工复核": "orange", "暂缓项目": "warning", "不建议通过": "danger" }[conclusion] || "neutral");

const tierTone = (tier) =>
  ({ "<300": "success", "300~500": "neutral", "500~700": "warning", ">=700": "danger" }[tier] || "neutral");
</script>

<template>
  <div class="space-y-5">
    <section class="card overflow-hidden">
      <div class="flex flex-wrap items-center gap-3 border-b border-border px-5 py-4">
        <SelectInput v-model="listFilter" :options="listOptions" class="w-[160px]" />
        <SelectInput v-model="tierFilter" :options="tierOptions" class="w-[160px]" />
        <span class="ml-auto text-sm text-muted">共 {{ filtered.length }} 个项目</span>
      </div>

      <div class="overflow-x-auto">
        <table class="table-base table-fixed min-w-[1080px]">
          <colgroup>
            <col class="w-[24%]" />
            <col class="w-[8%]" />
            <col class="w-[10%]" />
            <col class="w-[10%]" />
            <col class="w-[9%]" />
            <col class="w-[17%]" />
            <col class="w-[11%]" />
            <col class="w-[11%]" />
          </colgroup>
          <thead>
            <tr>
              <th>项目</th>
              <th>客户名单</th>
              <th>金额档位</th>
              <th>项目金额</th>
              <th>授信</th>
              <th>担保人</th>
              <th>计划回款</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in paginated" :key="item.project_id" class="hover:bg-canvas/60">
              <td>
                <strong class="block truncate text-[0.8125rem] text-ink" :title="item.name">{{ item.name }}</strong>
                <span class="text-[0.75rem] text-muted">{{ item.project_id }} · {{ item.customer }}</span>
              </td>
              <td><Badge tone="neutral">{{ item.customer_list }}</Badge></td>
              <td><Badge :tone="tierTone(item.amount_tier)">{{ formatAmountTier(item.amount_tier) }}</Badge></td>
              <td class="money-cell">{{ formatMoneyWan(item.amount_wan) }}</td>
              <td class="money-cell">{{ item.credit_amount_wan != null ? formatMoneyWan(item.credit_amount_wan) : "—" }}</td>
              <td><span class="block truncate text-sm text-muted" :title="item.guarantor">{{ item.guarantor || "—" }}</span></td>
              <td><span class="text-sm text-muted">{{ item.planned_payment_date || "—" }}</span></td>
              <td>
                <Button
                  tone="brand"
                  size="sm"
                  :disabled="assessing === item.project_id"
                  @click="assess(item)"
                >
                  {{ assessing === item.project_id ? "评估中…" : "事前评估" }}
                </Button>
              </td>
            </tr>
            <tr v-if="!workspace.loading && !filtered.length"><td colspan="8" class="empty-state">暂无新项目</td></tr>
          </tbody>
        </table>
      </div>

      <div class="flex flex-wrap items-center gap-3 border-t border-border px-5 py-4">
        <span class="text-sm text-muted">第 {{ rangeStart }}–{{ rangeEnd }} 条，共 {{ filtered.length }} 条</span>
        <SelectInput v-model="pageSize" :options="pageSizeOptions" class="w-[150px]" />

        <div class="ml-auto flex flex-wrap items-center gap-2">
          <button
            type="button"
            class="grid h-9 w-9 place-items-center rounded-lg border border-border text-muted transition-colors hover:bg-canvas disabled:cursor-not-allowed disabled:opacity-40"
            :disabled="currentPage === 1"
            aria-label="上一页"
            @click="goToPage(currentPage - 1)"
          >
            <ChevronLeft :size="16" />
          </button>
          <button
            v-for="page in pageNumbers"
            :key="page"
            type="button"
            class="grid h-9 min-w-9 place-items-center rounded-lg border px-2 text-sm font-semibold transition-colors"
            :class="page === currentPage ? 'border-brand bg-brand text-white' : 'border-border text-muted hover:bg-canvas'"
            :aria-current="page === currentPage ? 'page' : undefined"
            @click="goToPage(page)"
          >
            {{ page }}
          </button>
          <button
            type="button"
            class="grid h-9 w-9 place-items-center rounded-lg border border-border text-muted transition-colors hover:bg-canvas disabled:cursor-not-allowed disabled:opacity-40"
            :disabled="currentPage === totalPages"
            aria-label="下一页"
            @click="goToPage(currentPage + 1)"
          >
            <ChevronRight :size="16" />
          </button>
          <span class="ml-1 text-sm text-muted">跳至</span>
          <input
            v-model="pageJump"
            type="number"
            min="1"
            :max="totalPages"
            step="1"
            class="h-9 w-16 rounded-lg border border-border bg-white px-2 text-center text-sm text-ink outline-none focus:border-brand focus:ring-2 focus:ring-brand-wash"
            aria-label="跳转页码"
            @keydown.enter="jumpToPage"
          />
          <button
            type="button"
            class="h-9 rounded-lg border border-border px-3 text-sm font-semibold text-muted transition-colors hover:bg-canvas hover:text-ink"
            @click="jumpToPage"
          >
            跳转
          </button>
        </div>
      </div>
    </section>

    <Modal :open="Boolean(assessment)" title="事前评估结论" @close="assessment = null">
      <div v-if="assessment" class="space-y-4">
        <div class="flex items-center gap-3">
          <strong class="text-[0.9375rem] text-ink">{{ assessment.name }}</strong>
          <Badge :tone="conclusionTone(assessment.conclusion)">{{ assessment.conclusion }}</Badge>
          <Badge v-if="assessment.force_review" tone="danger">强制人工审批</Badge>
        </div>
        <p class="text-[0.8125rem] text-muted">{{ assessment.project_id }} · 金额 {{ formatMoneyWan(assessment.amount_wan) }} · 档位 {{ formatAmountTier(assessment.amount_tier) }}</p>
        <ul class="space-y-1.5">
          <li v-for="(reason, index) in assessment.reasons" :key="index" class="flex items-start gap-2 text-[0.8125rem] text-ink">
            <span class="mt-1.5 h-1.5 w-1.5 flex-none rounded-full bg-brand"></span>
            {{ reason }}
          </li>
        </ul>
        <p class="text-[0.75rem] text-muted">评估时间：{{ assessment.evaluated_at }}</p>
      </div>
    </Modal>
  </div>
</template>
