<script setup>
import { computed, ref, watch } from "vue";
import { ChevronLeft, ChevronRight } from "lucide-vue-next";
import Badge from "./ui/Badge.vue";
import SelectInput from "./ui/SelectInput.vue";
import TextInput from "./ui/TextInput.vue";
import TrendSpark from "./TrendSpark.vue";
import { formatDateTime, gradeColor, labels } from "../lib";
import { recalcHealth, workspace } from "../store";

const businessType = ref("");
const grade = ref("");
const query = ref("");
const pageSize = ref("10");
const currentPage = ref(1);
const pageJump = ref("1");
const recalculating = ref(false);

const businessOptions = [
  { title: "全部类型", value: "" },
  { title: "分销", value: "DISTRIBUTION" },
  { title: "项目", value: "PROJECT" },
  { title: "服务云", value: "SERVICE_CLOUD" },
];
const gradeOptions = [
  { title: "全部等级", value: "" },
  { title: "健康", value: "HEALTHY" },
  { title: "关注", value: "WATCH" },
  { title: "预警", value: "WARNING" },
  { title: "高危", value: "HIGH_RISK" },
];
const pageSizeOptions = [10, 20, 50].map((value) => ({ title: `${value} 行/页`, value: String(value) }));

const filtered = computed(() => {
  const items = workspace.healthScores || [];
  const keyword = String(query.value ?? "").trim().toLocaleLowerCase();
  return items.filter((item) => {
    const matchesFilters =
      (!businessType.value || item.business_type === businessType.value) &&
      (!grade.value || item.grade === grade.value);
    if (!matchesFilters || !keyword) return matchesFilters;
    const searchable = [
      item.subject_id,
      item.subject_label,
      labels.businessType[item.business_type],
      labels.grade[item.grade],
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

watch([businessType, grade, query, pageSize], () => goToPage(1));
watch(totalPages, (total) => {
  if (currentPage.value > total) goToPage(total);
});

const gradeTone = {
  HEALTHY: "bg-success",
  WATCH: "bg-warning",
  WARNING: "bg-[#f97316]",
  HIGH_RISK: "bg-danger",
};

const businessTypeTone = {
  DISTRIBUTION: "neutral",
  PROJECT: "brand",
  SERVICE_CLOUD: "info",
};

const topDrivers = (item) => {
  const down = item.drivers?.down || [];
  return down.slice(0, 2).join("；") || "—";
};

async function recalculate() {
  recalculating.value = true;
  try {
    await recalcHealth();
    goToPage(1);
  } finally {
    recalculating.value = false;
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
</script>

<template>
  <div class="space-y-5">
    <section class="card overflow-hidden">
      <div class="flex flex-wrap items-center gap-3 border-b border-border px-5 py-4">
        <SelectInput v-model="businessType" :options="businessOptions" class="w-[180px]" />
        <SelectInput v-model="grade" :options="gradeOptions" class="w-[180px]" />
        <TextInput v-model="query" search clearable class="w-[320px] max-w-full" placeholder="搜索公司或等级" aria-label="搜索公司或等级" @clear="query = ''" />
        <span class="ml-auto text-sm text-muted">共 {{ filtered.length }} 条业务健康记录</span>
        <button
          type="button"
          class="inline-flex h-10 items-center gap-2 rounded-lg bg-brand px-4 text-sm font-semibold text-white transition-colors hover:bg-brand-dark disabled:opacity-50"
          :disabled="recalculating"
          @click="recalculate"
        >
          <span :class="recalculating ? 'animate-spin' : ''">⟳</span>
          {{ recalculating ? "计算中…" : "重算健康度" }}
        </button>
      </div>

      <div class="overflow-x-auto">
        <table class="table-base table-fixed min-w-[960px]">
          <colgroup>
            <col class="w-[21%]" />
            <col class="w-[12%]" />
            <col class="w-[16%]" />
            <col class="w-[9%]" />
            <col class="w-[14%]" />
            <col class="w-[22%]" />
            <col class="w-[8%]" />
          </colgroup>
          <thead>
            <tr>
              <th>主体</th>
              <th>业务类型</th>
              <th>分数</th>
              <th>等级</th>
              <th>近 12 期趋势</th>
              <th>主要拉低因素</th>
              <th>计算时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in paginated" :key="item.id" class="hover:bg-canvas/60">
              <td>
                <strong class="block truncate text-[0.8125rem] text-ink" :title="`${item.subject_id} ${item.subject_label}`">{{ item.subject_id }} {{ item.subject_label }}</strong>
              </td>
              <td>
                <Badge
                  v-if="item.business_type"
                  :tone="businessTypeTone[item.business_type] || 'neutral'"
                >
                  {{ labels.businessType[item.business_type] || item.business_type }}
                </Badge>
                <Badge v-else tone="neutral">—</Badge>
              </td>
              <td>
                <div class="flex items-center gap-2">
                  <span class="h-2 w-14 overflow-hidden rounded-full bg-gray-100">
                    <span class="block h-full rounded-full" :class="gradeTone[item.grade] || 'bg-gray-300'" :style="{ width: item.score + '%' }"></span>
                  </span>
                  <strong class="text-ink tabular-nums">{{ item.score }}</strong>
                </div>
              </td>
              <td><Badge :tone="gradeColor(item.grade)">{{ labels.grade[item.grade] || item.grade }}</Badge></td>
              <td><TrendSpark :data="item.trend" /></td>
              <td><span class="block truncate text-sm text-muted" :title="topDrivers(item)">{{ topDrivers(item) }}</span></td>
              <td><span class="text-[0.75rem] text-muted">{{ formatDateTime(item.computed_at) }}</span></td>
            </tr>
            <tr v-if="!workspace.loading && !filtered.length"><td colspan="7" class="empty-state">当前筛选条件下没有健康度数据，请点击“重算健康度”生成</td></tr>
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
