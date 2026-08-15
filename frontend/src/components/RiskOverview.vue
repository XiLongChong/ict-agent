<script setup>
import { computed } from "vue";
import { useRouter } from "vue-router";
import { ArrowRight } from "lucide-vue-next";
import Badge from "./ui/Badge.vue";
import { formatMoney, labels, openCaseWorkspace, statusColor } from "../lib";
import { workspace } from "../store";
const router = useRouter();
const pendingCases = computed(() => (workspace.overview ? workspace.overview.total_cases - workspace.overview.closed_cases : "—"));
const recentCases = computed(() => (workspace.cases || []).slice(0, 6));
function openCase(id) { openCaseWorkspace(id, "/risk"); }
</script>
<template>
  <div class="space-y-5">
    <p class="text-sm text-muted">统一风险信号、案件调查与人工复核</p>

    <div class="grid grid-cols-2 gap-4 xl:grid-cols-4">
      <section
        v-for="item in [
          { label: '待处理案件', value: pendingCases },
          { label: '待调查', value: workspace.overview?.pending_agent_cases ?? '—' },
          { label: '待人工复核', value: workspace.overview?.pending_human_review_cases ?? '—' },
          { label: '风险敞口', value: formatMoney(workspace.overview?.exposure_amount) },
        ]"
        :key="item.label"
        class="card p-5"
      >
        <span class="text-sm text-muted">{{ item.label }}</span>
        <strong class="mt-2 block text-2xl text-ink">{{ item.value }}</strong>
      </section>
    </div>

    <section class="card overflow-hidden">
      <div class="panel-head">
        <h3>最近案件</h3>
        <button class="inline-flex items-center gap-1 text-sm font-semibold text-brand" @click="router.push('/cases')">
          查看全部
          <ArrowRight :size="15" />
        </button>
      </div>

      <div v-if="recentCases.length" class="divide-y divide-border">
        <button
          v-for="item in recentCases"
          :key="item.case_id"
          class="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-4 px-5 py-4 text-left transition-colors hover:bg-canvas md:grid-cols-[minmax(0,1fr)_88px]"
          @click="openCase(item.case_id)"
        >
          <span class="min-w-0">
            <strong class="block truncate text-sm font-semibold text-ink">{{ item.subject_label }}</strong>
            <span class="mt-1 block truncate text-xs text-muted">
              {{ labels.investigationProfile[item.investigation_profile] }} · {{ labels.caseSource[item.source] || item.source }}<template v-if="item.business_type"> · {{ labels.businessType[item.business_type] }}</template>
            </span>
          </span>
          <span class="justify-self-end">
            <Badge :tone="statusColor(item.status)">{{ labels.status[item.status] }}</Badge>
          </span>
        </button>
      </div>
      <div v-else class="empty-state">暂无案件</div>
    </section>
  </div>
</template>
