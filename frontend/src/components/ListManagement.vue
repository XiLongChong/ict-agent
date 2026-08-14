<script setup>
import { computed, ref } from "vue";
import Badge from "./ui/Badge.vue";
import Button from "./ui/Button.vue";
import Modal from "./ui/Modal.vue";
import TextArea from "./ui/TextArea.vue";
import TextInput from "./ui/TextInput.vue";
import { formatDateTime, formatMoney, listColor, labels, localizeRecommendationText, recommendationStatusColor } from "../lib";
import { reviewRecommendation, workspace } from "../store";

const filter = ref("");
const reviewing = ref(null);
const reviewer = ref("");
const reason = ref("");

const recommendations = computed(() => {
  const items = workspace.recommendations || [];
  if (!filter.value) return items;
  return items.filter((item) => item.status === filter.value);
});

const statusOptions = [
  { value: "", label: "全部状态" },
  { value: "PENDING", label: "待审批" },
  { value: "APPROVED", label: "已采纳" },
  { value: "REJECTED", label: "已驳回" },
];

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

const evidenceText = (item) => (item.evidence || []).map((e) => localizeRecommendationText(e.summary)).join("；") || "—";
</script>

<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-center gap-3">
      <h2 class="text-[17px] font-bold text-ink">名单管理</h2>
      <div class="flex-1"></div>
      <select
        v-model="filter"
        class="h-10 rounded-lg border border-border bg-surface px-3 text-sm text-ink outline-none focus:border-brand"
      >
        <option v-for="option in statusOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
      </select>
    </div>

    <section class="space-y-3">
      <div v-for="item in recommendations" :key="item.recommendation_id" class="card p-5">
        <!-- 头部行：主体 + 状态徽标，右侧固定审批按钮位（无按钮时由 flex-1 撑开，保持对齐） -->
        <div class="flex items-center gap-2">
          <strong class="text-[15px] text-ink">{{ item.subject_label }}</strong>
          <span class="text-[12px] text-muted">{{ item.subject_id }}</span>
          <Badge :tone="recommendationStatusColor(item.status)">{{ labels.recommendationStatus[item.status] || item.status }}</Badge>
          <div class="flex-1"></div>
          <button
            v-if="item.status === 'PENDING'"
            type="button"
            class="inline-flex h-9 items-center rounded-lg bg-brand px-3 text-[13px] font-semibold text-white transition-colors hover:bg-brand-dark"
            @click="openReview(item)"
          >
            审批
          </button>
        </div>

        <!-- 名单变化信息行 -->
        <div class="mt-2 flex flex-wrap items-center gap-2 text-[13px]">
          <Badge :tone="listColor(item.current_list)">{{ labels.list[item.current_list] || item.current_list }}</Badge>
          <span class="text-muted">→</span>
          <Badge :tone="listColor(item.target_list)">{{ labels.list[item.target_list] || item.target_list }}</Badge>
          <span class="ml-2 text-muted">风险金额 {{ formatMoney(item.risk_amount) }}</span>
          <span class="text-muted">· 复查 {{ item.review_due_date }}</span>
        </div>

        <!-- 详情区：理由 / 证据 / 健康度变化 / 审核人 -->
        <div class="mt-3 border-t border-border/60 pt-3 text-[13px] text-muted">
          <p class="text-ink">{{ localizeRecommendationText(item.reason) }}</p>
          <p class="mt-1 text-[12px]">证据：{{ evidenceText(item) }}</p>
          <p class="mt-1 text-[12px]">
            健康度变化：{{ item.health_change }} · 触发规则：{{ labels.recommendationTrigger[item.trigger_rule] || "其他规则" }}
          </p>
          <p v-if="item.status !== 'PENDING'" class="mt-1 text-[12px]">
            审核人：{{ item.reviewer }} · {{ item.review_reason }} · {{ formatDateTime(item.review_at) }}
          </p>
        </div>
      </div>
      <div v-if="!recommendations.length" class="empty-state card py-12 text-center text-muted">暂无名单建议</div>
    </section>

    <Modal :open="Boolean(reviewing)" title="名单建议审批" @close="reviewing = null">
      <div v-if="reviewing" class="space-y-4">
        <div class="text-sm text-muted">
          {{ reviewing.subject_label }}：{{ labels.list[reviewing.current_list] }} → {{ labels.list[reviewing.target_list] }}
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
