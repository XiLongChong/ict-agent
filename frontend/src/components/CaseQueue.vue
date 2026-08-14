<script setup>
import { computed, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { ChevronLeft, ChevronRight, Radar } from "lucide-vue-next";
import Badge from "./ui/Badge.vue";
import SelectInput from "./ui/SelectInput.vue";
import TextInput from "./ui/TextInput.vue";
import { formatMoney, labels, openCaseWorkspace, priorityColor, statusColor } from "../lib";
import { runScan, workspace } from "../store";

const route = useRoute();
const type = ref("");
const status = ref("");
const priority = ref("");
const query = ref("");
const pageSize = ref("10");
const currentPage = ref(1);
const pageJump = ref("1");
const typeOptions = [
  { title: "全部类型", value: "" },
  { title: "应收", value: "ACCOUNTS_RECEIVABLE" },
  { title: "库存", value: "INVENTORY" },
];
const statusOptions = [{ title: "全部状态", value: "" }, ...Object.entries(labels.status).map(([value, title]) => ({ title, value }))];
const priorityOptions = [
  { title: "全部风险等级", value: "" },
  { title: labels.priority.HIGH, value: "HIGH" },
  { title: labels.priority.MEDIUM, value: "MEDIUM" },
  { title: labels.priority.LOW, value: "LOW" },
];
const pageSizeOptions = [10, 20, 50].map((value) => ({ title: `${value} 行/页`, value: String(value) }));
const filtered = computed(() => {
  const keyword = String(query.value ?? "").trim().toLocaleLowerCase();
  return workspace.cases.filter((item) => {
    const matchesFilters =
      (!type.value || item.case_type === type.value) &&
      (!status.value || item.status === status.value) &&
      (!priority.value || item.priority === priority.value);
    if (!matchesFilters || !keyword) return matchesFilters;
    const searchable = [
      item.case_id,
      item.entity_label,
      item.risk_overview,
      labels.caseType[item.case_type],
      labels.status[item.status],
      labels.priority[item.priority],
    ]
      .filter(Boolean)
      .join(" ")
      .toLocaleLowerCase();
    return searchable.includes(keyword);
  });
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

watch([type, status, priority, query, pageSize], () => goToPage(1));
watch(totalPages, (total) => {
  if (currentPage.value > total) goToPage(total);
});

function openCase(caseId) {
  try {
    openCaseWorkspace(caseId, route.fullPath);
  } catch (exception) {
    workspace.status = { text: exception.message, error: true };
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

async function scan() {
  await runScan();
  goToPage(1);
}
</script>

<template>
  <div class="space-y-5">
    <section class="card overflow-hidden">
      <div class="flex flex-wrap items-center gap-3 border-b border-border px-5 py-4">
        <SelectInput v-model="type" :options="typeOptions" class="w-[180px]" />
        <SelectInput v-model="status" :options="statusOptions" class="w-[180px]" />
        <SelectInput v-model="priority" :options="priorityOptions" class="w-[180px]" />
        <TextInput v-model="query" search clearable class="w-[320px] max-w-full" placeholder="搜索案件、客户或物料" aria-label="搜索案件、客户或物料" @clear="query = ''" />
        <span class="ml-auto text-sm text-muted">共 {{ filtered.length }} 个案件</span>
        <button
          type="button"
          class="inline-flex h-10 items-center gap-2 rounded-lg bg-brand px-4 text-sm font-semibold text-white transition-colors hover:bg-brand-dark disabled:opacity-50"
          :disabled="workspace.scanning"
          @click="scan"
        >
          <Radar :size="16" :class="workspace.scanning ? 'animate-spin' : ''" />
          {{ workspace.scanning ? "扫描中…" : "重新扫描" }}
        </button>
      </div>

      <div class="overflow-x-auto">
        <table class="table-base table-fixed min-w-[1120px]">
          <colgroup>
            <col class="w-[28%]" />
            <col class="w-[10%]" />
            <col class="w-[27%]" />
            <col class="w-[10%]" />
            <col class="w-[14%]" />
            <col class="w-[11%]" />
          </colgroup>
          <thead>
            <tr>
              <th>主体</th>
              <th>案件类型</th>
              <th>风险概况</th>
              <th>风险等级</th>
              <th>风险敞口</th>
              <th class="sticky right-0 z-10 border-l border-border bg-canvas">处理状态</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in paginated"
              :key="item.case_id"
              class="group"
              tabindex="0"
              @click="openCase(item.case_id)"
              @keydown.enter="openCase(item.case_id)"
            >
              <td>
                <strong class="block truncate text-[0.8125rem] text-ink" :title="item.entity_label">{{ item.entity_label }}</strong>
              </td>
              <td><span class="text-sm text-muted">{{ labels.caseType[item.case_type] }}</span></td>
              <td><span class="block truncate text-sm font-medium text-ink" :title="item.risk_overview">{{ item.risk_overview }}</span></td>
              <td><Badge :tone="priorityColor(item.priority)">{{ labels.priority[item.priority] }}</Badge></td>
              <td class="money-cell">{{ formatMoney(item.exposure_amount) }}</td>
              <td class="sticky right-0 border-l border-border bg-surface group-hover:bg-canvas">
                <Badge :tone="statusColor(item.status)">{{ labels.status[item.status] }}</Badge>
              </td>
            </tr>
            <tr v-if="!workspace.loading && !filtered.length"><td colspan="6" class="empty-state">当前筛选条件下没有案件</td></tr>
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
  </div>
</template>
