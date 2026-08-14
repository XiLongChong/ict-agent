<script setup>
import { computed, ref } from "vue";
import Badge from "./ui/Badge.vue";
import Button from "./ui/Button.vue";
import Modal from "./ui/Modal.vue";
import { formatAmountTier, formatMoneyWan } from "../lib";
import { runPreAssessment, workspace } from "../store";

const assessing = ref(null);
const assessment = ref(null);

const newProjects = computed(() => {
  // 后端 /api/v1/projects 当前只返回模拟新项目（P2026-）
  return (workspace.projects || []).filter((item) =>
    String(item.project_id || "").startsWith("P2026-")
  );
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
      <p class="text-[13px] text-muted">对拟立项的模拟新项目执行事前评估（黑名单拦截 / 金额档位 / 历史超期 / 担保人）</p>
    </div>

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
