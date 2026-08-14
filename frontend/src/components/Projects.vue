<script setup>
import { computed, ref } from "vue";
import Badge from "./ui/Badge.vue";
import Button from "./ui/Button.vue";
import Modal from "./ui/Modal.vue";
import { formatAmountTier, formatMoneyWan, labels } from "../lib";
import { runPreAssessment, workspace } from "../store";

const tab = ref("new");
const assessing = ref(null);
const assessment = ref(null);

const projects = computed(() => workspace.projects || []);
const newProjects = computed(() => {
  // 后端 /api/v1/projects 返回存量合同视图；模拟新项目在 /warning/overview 之外，
  // 这里从 projects 中识别 P2026- 前缀作为模拟新项目，其余为存量项目。
  return projects.value.filter((item) => String(item.project_id || "").startsWith("P2026-"));
});
const existingProjects = computed(() => projects.value.filter((item) => !String(item.project_id || "").startsWith("P2026-")));

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

const conclusionTone = (conclusion) =>
  ({ "正常通过": "success", "有条件通过": "warning", "需要人工复核": "orange", "暂缓项目": "warning", "不建议通过": "danger" }[conclusion] || "neutral");

const tierTone = (tier) =>
  ({ "<300": "success", "300~500": "neutral", "500~700": "warning", ">=700": "danger" }[tier] || "neutral");
</script>

<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-center gap-3">
      <h2 class="text-[17px] font-bold text-ink">项目评估</h2>
      <Badge tone="warning">模拟数据</Badge>
      <div class="flex-1"></div>
      <div class="inline-flex rounded-lg border border-border p-1">
        <button
          type="button"
          class="rounded-md px-3 py-1.5 text-[13px] font-semibold transition-colors"
          :class="tab === 'new' ? 'bg-brand text-white' : 'text-muted hover:text-brand'"
          @click="tab = 'new'"
        >
          模拟新项目（事前评估）
        </button>
        <button
          type="button"
          class="rounded-md px-3 py-1.5 text-[13px] font-semibold transition-colors"
          :class="tab === 'existing' ? 'bg-brand text-white' : 'text-muted hover:text-brand'"
          @click="tab = 'existing'"
        >
          存量项目（合同视图）
        </button>
      </div>
    </div>

    <!-- 模拟新项目 / 事前评估 -->
    <template v-if="tab === 'new'">
      <section class="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <div v-for="item in newProjects" :key="item.project_id" class="card p-5">
          <div class="flex items-start justify-between gap-3">
            <div>
              <strong class="block text-[15px] text-ink">{{ item.name }}</strong>
              <span class="text-[12px] text-muted">{{ item.project_id }} · {{ item.customer }}</span>
            </div>
            <Badge :tone="tierTone(item.amount_tier)">{{ formatAmountTier(item.amount_tier) }}</Badge>
          </div>
          <div class="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[13px] text-muted">
            <span>金额 {{ formatMoneyWan(item.amount_wan) }}</span>
            <span>授信 {{ item.credit_amount_wan != null ? formatMoneyWan(item.credit_amount_wan) : "—" }}</span>
            <span>担保人 {{ item.guarantor || "—" }}</span>
            <span>计划回款 {{ item.planned_payment_date || "—" }}</span>
          </div>
          <div class="mt-4 flex items-center justify-between">
            <Badge tone="neutral">{{ item.customer_list }}</Badge>
            <Button
              tone="brand"
              size="sm"
              :disabled="assessing === item.project_id"
              @click="assess(item)"
            >
              {{ assessing === item.project_id ? "评估中…" : "事前评估" }}
            </Button>
          </div>
        </div>
        <div v-if="!newProjects.length" class="empty-state card py-12 text-center text-muted">暂无模拟新项目</div>
      </section>
    </template>

    <!-- 存量项目视图 -->
    <template v-else>
      <section class="card overflow-hidden">
        <div class="overflow-x-auto">
          <table class="w-full min-w-[860px] text-left text-sm">
            <thead>
              <tr class="border-b border-border text-[12px] text-muted">
                <th class="px-4 py-3 font-semibold">项目</th>
                <th class="px-4 py-3 font-semibold">客户</th>
                <th class="px-4 py-3 font-semibold">金额档位</th>
                <th class="px-4 py-3 font-semibold">阶段</th>
                <th class="px-4 py-3 font-semibold">里程碑</th>
                <th class="px-4 py-3 font-semibold">计划回款</th>
                <th class="px-4 py-3 font-semibold">担保人</th>
                <th class="px-4 py-3 font-semibold">风险提示</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in existingProjects" :key="item.project_id" class="border-b border-border/60 last:border-0 hover:bg-canvas/60">
                <td class="px-4 py-3">
                  <strong class="block text-ink">{{ item.name }}</strong>
                  <span class="text-[12px] text-muted">{{ item.project_id }}</span>
                </td>
                <td class="px-4 py-3 text-muted">{{ item.customer }}</td>
                <td class="px-4 py-3"><Badge :tone="tierTone(item.amount_tier)">{{ formatAmountTier(item.amount_tier) }}</Badge></td>
                <td class="px-4 py-3 text-muted">{{ item.stage || "—" }}</td>
                <td class="px-4 py-3">
                  <div class="flex items-center gap-2">
                    <span class="h-1.5 w-12 overflow-hidden rounded-full bg-gray-100">
                      <span class="block h-full rounded-full bg-brand" :style="{ width: (item.milestone_progress || 0) + '%' }"></span>
                    </span>
                    <span class="text-[12px] text-muted tabular-nums">{{ item.milestone_progress || 0 }}%</span>
                  </div>
                </td>
                <td class="px-4 py-3 text-muted">{{ item.planned_payment_date || "—" }}</td>
                <td class="px-4 py-3 text-muted">{{ item.guarantor || "—" }}</td>
                <td class="max-w-[220px] truncate px-4 py-3 text-[12px]" :class="item.risk_note ? 'text-warning-deep' : 'text-muted'">{{ item.risk_note || "—" }}</td>
              </tr>
              <tr v-if="!existingProjects.length">
                <td colspan="8" class="empty-state px-4 py-10 text-center">暂无存量项目数据</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>

    <Modal :open="Boolean(assessment)" title="事前评估结论" @close="assessment = null">
      <div v-if="assessment" class="space-y-4">
        <div class="flex items-center gap-3">
          <strong class="text-[15px] text-ink">{{ assessment.name }}</strong>
          <Badge :tone="conclusionTone(assessment.conclusion)">{{ assessment.conclusion }}</Badge>
          <Badge v-if="assessment.force_review" tone="danger">强制人工审批</Badge>
        </div>
        <p class="text-[13px] text-muted">{{ assessment.project_id }} · 金额 {{ formatAmountTier(assessment.amount_wan) }} · 档位 {{ formatAmountTier(assessment.amount_tier) }}</p>
        <ul class="space-y-1.5">
          <li v-for="(reason, index) in assessment.reasons" :key="index" class="flex items-start gap-2 text-[13px] text-ink">
            <span class="mt-1.5 h-1.5 w-1.5 flex-none rounded-full bg-brand"></span>
            {{ reason }}
          </li>
        </ul>
        <p class="text-[12px] text-muted">评估时间：{{ assessment.evaluated_at }} · 模拟数据</p>
      </div>
    </Modal>
  </div>
</template>
