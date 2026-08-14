<script setup>
import { computed, ref } from "vue";
import Badge from "./ui/Badge.vue";
import SelectInput from "./ui/SelectInput.vue";
import TrendSpark from "./TrendSpark.vue";
import { formatDateTime, gradeColor, labels } from "../lib";
import { recalcHealth, workspace } from "../store";

const subjectType = ref("");
const grade = ref("");
const recalculating = ref(false);

const filtered = computed(() => {
  const items = workspace.healthScores || [];
  return items.filter((item) => {
    if (subjectType.value && item.subject_type !== subjectType.value) return false;
    if (grade.value && item.grade !== grade.value) return false;
    return true;
  });
});

const subjectOptions = [
  { value: "", label: "全部类型" },
  { value: "CUSTOMER", label: "客户" },
  { value: "CONTRACT", label: "项目合同" },
];
const gradeOptions = [
  { value: "", label: "全部等级" },
  { value: "HEALTHY", label: "健康" },
  { value: "WATCH", label: "关注" },
  { value: "WARNING", label: "预警" },
  { value: "HIGH_RISK", label: "高风险" },
];

const gradeTone = {
  HEALTHY: "bg-success",
  WATCH: "bg-warning",
  WARNING: "bg-[#f97316]",
  HIGH_RISK: "bg-danger",
};

const topDrivers = (item) => {
  const down = item.drivers?.down || [];
  return down.slice(0, 2).join("；") || "—";
};

async function recalculate() {
  recalculating.value = true;
  try {
    await recalcHealth();
  } finally {
    recalculating.value = false;
  }
}
</script>

<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-center gap-3">
      <h2 class="text-[17px] font-bold text-ink">健康度总览</h2>
      <div class="flex-1"></div>
      <SelectInput :model-value="subjectType" :options="subjectOptions" @update:model-value="subjectType = $event" />
      <SelectInput :model-value="grade" :options="gradeOptions" @update:model-value="grade = $event" />
      <button
        type="button"
        class="inline-flex h-10 items-center gap-2 rounded-lg bg-brand px-4 text-sm font-semibold text-white transition-colors hover:bg-brand-dark disabled:opacity-50"
        :disabled="recalculating"
        @click="recalculate"
      >
        <span :class="recalculating ? 'animate-spin' : ''">⟳</span>
        重算健康度
      </button>
    </div>

    <section class="card overflow-hidden">
      <div class="overflow-x-auto">
        <table class="w-full min-w-[880px] text-left text-sm">
          <thead>
            <tr class="border-b border-border text-[12px] text-muted">
              <th class="px-4 py-3 font-semibold">主体</th>
              <th class="px-4 py-3 font-semibold">类型</th>
              <th class="px-4 py-3 font-semibold">分数</th>
              <th class="px-4 py-3 font-semibold">等级</th>
              <th class="px-4 py-3 font-semibold">近 12 期趋势</th>
              <th class="px-4 py-3 font-semibold">主要拉低因素</th>
              <th class="px-4 py-3 font-semibold">计算时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in filtered" :key="item.id" class="border-b border-border/60 last:border-0 hover:bg-canvas/60">
              <td class="px-4 py-3">
                <strong class="block text-ink">{{ item.subject_label }}</strong>
                <span class="text-[12px] text-muted">{{ item.subject_id }}</span>
              </td>
              <td class="px-4 py-3">
                <Badge tone="neutral">{{ item.subject_type === "CUSTOMER" ? "客户" : "项目合同" }}</Badge>
              </td>
              <td class="px-4 py-3">
                <div class="flex items-center gap-2">
                  <span class="h-2 w-14 overflow-hidden rounded-full bg-gray-100">
                    <span class="block h-full rounded-full" :class="gradeTone[item.grade] || 'bg-gray-300'" :style="{ width: item.score + '%' }"></span>
                  </span>
                  <strong class="text-ink tabular-nums">{{ item.score }}</strong>
                </div>
              </td>
              <td class="px-4 py-3">
                <Badge :tone="gradeColor(item.grade)">{{ labels.grade[item.grade] || item.grade }}</Badge>
              </td>
              <td class="px-4 py-3"><TrendSpark :data="item.trend" /></td>
              <td class="max-w-[260px] truncate px-4 py-3 text-muted" :title="topDrivers(item)">{{ topDrivers(item) }}</td>
              <td class="px-4 py-3 text-[12px] text-muted">{{ formatDateTime(item.computed_at) }}</td>
            </tr>
            <tr v-if="!filtered.length">
              <td colspan="7" class="empty-state px-4 py-10 text-center">暂无健康度数据，请点击“重算健康度”生成</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <p class="text-[12px] text-muted">健康度由确定性经营指标计算（回款 / 项目进度 / 应收 / 合同授信 / 担保人 / 舆情六维加权），不消耗模型额度。</p>
  </div>
</template>
