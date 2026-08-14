<script setup>
import { computed, ref, watch } from "vue";
import { ChevronLeft, ChevronRight } from "lucide-vue-next";
import Badge from "./ui/Badge.vue";
import Button from "./ui/Button.vue";
import Modal from "./ui/Modal.vue";
import SelectInput from "./ui/SelectInput.vue";
import TextArea from "./ui/TextArea.vue";
import TextInput from "./ui/TextInput.vue";
import { formatDateTime, formatMoney, listColor, labels, recommendationStatusColor } from "../lib";
import { reviewRecommendation, workspace } from "../store";

const filter = ref("");
const pageSize = ref("10");
const currentPage = ref(1);
const pageJump = ref("1");
const reviewing = ref(null);
const reviewer = ref("");
const reason = ref("");

const statusOptions = [
  { title: "全部状态", value: "" },
  { title: "待审批", value: "PENDING" },
  { title: "已采纳", value: "APPROVED" },
  { title: "已驳回", value: "REJECTED" },
];
const pageSizeOptions = [10, 20, 50].map((value) => ({ title: `${value} 行/页`, value: String(value) }));

const filtered = computed(() => {
  const items = workspace.recommendations || [];
  if (!filter.value) return items;
  return items.filter((item) => item.status === filter.value);
});
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

watch([filter, pageSize], () => goToPage(1));
watch(totalPages, (total) => {
  if (currentPage.value > total) goToPage(total);
});

function openReview(item) {
  reviewing.value = item;
  reviewer.value = "";
  reason.value = "";
}

async function submitReview(decision) {
  if (!reviewer.value.trim() || reason.value.trim().length < 2) {
    workspace.status = { text: "请填写审批人与原因（至少 2 字）", error: true };
    return;
  }
  try {
    await reviewRecommendation(reviewing.value.recommendation_id, {
      decision,
      reviewer: reviewer.value.trim(),
      reason: reason.value.trim(),
    });
    reviewing.value = null;
  } catch {
    // 状态由 store 统一提示
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

const evidenceText = (item) => (item.evidence || []).map((e) => e.summary).join("；") || "—";
</script>

<template>
  <div class="space-y-5">
    <section class="card overflow-hidden">
      <div class="flex flex-wrap items-center gap-3 border-b border-border px-5 py-4">
        <SelectInput v-model="filter" :options="statusOptions" class="w-[160px]" />
        <span class="ml-auto text-sm text-muted">共 {{ filtered.length }} 条</span>
      </div>

      <div class="overflow-x-auto">
        <table class="table-base table-fixed min-w-[1200px]">
          <colgroup>
            <col class="w-[15%]" />
            <col class="w-[14%]" />
            <col class="w-[28%]" />
            <col class="w-[10%]" />
            <col class="w-[9%]" />
            <col class="w-[9%]" />
            <col class="w-[8%]" />
            <col class="w-[7%]" />
          </colgroup>
          <thead>
            <tr>
              <th>主体</th>
              <th>名单变更</th>
              <th>建议原因</th>
              <th>健康度变化</th>
              <th>风险金额</th>
              <th>复查日期</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in paginated" :key="item.recommendation_id" class="hover:bg-canvas/60">
              <td>
                <strong class="block truncate text-[0.8125rem] text-ink" :title="item.subject_label">{{ item.subject_label }}</strong>
                <span class="text-[0.75rem] text-muted">{{ item.subject_id }}</span>
              </td>
              <td>
                <div class="flex items-center gap-1.5">
                  <Badge :tone="listColor(item.current_list)">{{ labels.list[item.current_list] || item.current_list }}</Badge>
                  <span class="text-[0.75rem] text-muted">→</span>
                  <Badge :tone="listColor(item.target_list)">{{ labels.list[item.target_list] || item.target_list }}</Badge>
                </div>
              </td>
              <td>
                <p class="block truncate text-sm text-ink" :title="item.reason">{{ item.reason }}</p>
                <span class="block truncate text-[0.75rem] text-muted" :title="evidenceText(item)">证据：{{ evidenceText(item) }} · 触发规则：{{ item.trigger_rule }}</span>
              </td>
              <td><span class="text-sm text-muted">{{ item.health_change }}</span></td>
              <td class="money-cell">{{ formatMoney(item.risk_amount) }}</td>
              <td><span class="text-sm text-muted">{{ item.review_due_date }}</span></td>
              <td>
                <Badge :tone="recommendationStatusColor(item.status)">{{ labels.recommendationStatus[item.status] || item.status }}</Badge>
                <span v-if="item.status !== 'PENDING'" class="mt-1 block truncate text-[0.75rem] text-muted" :title="`${item.reviewer} · ${item.review_reason}`">
                  {{ item.reviewer }} · {{ formatDateTime(item.review_at) }}
                </span>
              </td>
              <td>
                <button
                  v-if="item.status === 'PENDING'"
                  type="button"
                  class="inline-flex h-9 items-center rounded-lg bg-brand px-3 text-[0.8125rem] font-semibold text-white transition-colors hover:bg-brand-dark"
                  @click="openReview(item)"
                >
                  审批
                </button>
              </td>
            </tr>
            <tr v-if="!workspace.loading && !filtered.length"><td colspan="8" class="empty-state">暂无名单建议</td></tr>
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

    <Modal :open="Boolean(reviewing)" title="名单建议审批" @close="reviewing = null">
      <div v-if="reviewing" class="space-y-4">
        <div class="text-sm text-muted">
          {{ reviewing.subject_label }}：{{ labels.list[reviewing.current_list] || reviewing.current_list }} → {{ labels.list[reviewing.target_list] || reviewing.target_list }}
        </div>
        <TextInput v-model="reviewer" placeholder="审批人姓名" />
        <TextArea v-model="reason" placeholder="审批意见（至少 2 字）" />
        <div class="flex justify-end gap-2">
          <Button tone="neutral" @click="reviewing = null">取消</Button>
          <Button tone="danger" @click="submitReview('REJECTED')">驳回</Button>
          <Button tone="success" @click="submitReview('APPROVED')">采纳</Button>
        </div>
      </div>
    </Modal>
  </div>
</template>
