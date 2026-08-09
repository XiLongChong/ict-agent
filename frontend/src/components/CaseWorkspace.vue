<script setup>
import { computed, reactive, ref, watch } from "vue";
import InvestigationThread from "./InvestigationThread.vue";
import { api, formatMoney, labels, priorityColor, statusColor } from "../lib";

const props = defineProps({ modelValue: Boolean, caseItem: Object });
const emit = defineEmits(["update:modelValue", "refresh"]);
const tab = ref("investigation");
const submitting = ref(false);
const form = reactive({ decision: "", reviewer: "", reason: "", action: "", next_review_at: "" });
const decisionOptions = [
  { title: "暂时接受，持续观察", value: "MONITOR" }, { title: "风险成立，需要处置", value: "ACTION_REQUIRED" },
  { title: "确认误报或数据问题", value: "FALSE_POSITIVE" }, { title: "风险已经解决", value: "RESOLVED" },
];
const open = computed({ get: () => props.modelValue, set: (value) => emit("update:modelValue", value) });
watch(() => props.caseItem?.case_id, () => { tab.value = "investigation"; Object.assign(form, { decision: "", reviewer: "", reason: "", action: "", next_review_at: "" }); });
const canSubmit = computed(() => form.decision && form.reviewer.trim() && form.reason.trim().length >= 2 && (form.decision !== "MONITOR" || form.next_review_at));

async function submitReview() {
  if (!props.caseItem || !canSubmit.value) return;
  submitting.value = true;
  try {
    await api(`/api/v1/cases/${encodeURIComponent(props.caseItem.case_id)}/reviews`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision: form.decision, reviewer: form.reviewer.trim(), reason: form.reason.trim(), action: form.action.trim() || null, next_review_at: form.decision === "MONITOR" ? form.next_review_at : null }),
    });
    emit("refresh");
    Object.assign(form, { decision: "", reason: "", action: "", next_review_at: "" });
  } finally { submitting.value = false; }
}

function reviewLabel(decision) {
  return ({ MONITOR: "持续观察", ACTION_REQUIRED: "需要处置", FALSE_POSITIVE: "确认误报", RESOLVED: "已经解决" })[decision] || decision;
}
</script>

<template>
  <v-dialog v-model="open" fullscreen transition="dialog-bottom-transition" class="case-dialog">
    <v-card class="case-workspace">
      <header class="case-workspace-header">
        <v-btn icon="mdi-close" variant="text" aria-label="关闭案件" @click="open = false" />
        <div v-if="caseItem" class="case-title"><span>{{ labels.caseType[caseItem.case_type] }} · {{ caseItem.case_id }}</span><h2>{{ caseItem.entity_label }}</h2></div>
        <v-spacer />
        <template v-if="caseItem"><v-chip :color="priorityColor(caseItem.priority)" variant="tonal">{{ labels.priority[caseItem.priority] }}优先级</v-chip><v-chip :color="statusColor(caseItem.status)" variant="tonal">{{ labels.status[caseItem.status] }}</v-chip></template>
      </header>

      <div v-if="!caseItem" class="case-loading"><v-progress-circular indeterminate color="primary" /><span>正在装载案件、证据和审核记录</span></div>
      <template v-else>
        <div class="case-facts-strip"><div><span>风险敞口</span><strong>{{ formatMoney(caseItem.exposure_amount) }}</strong></div><div><span>观察日期</span><strong>{{ caseItem.observation_date }}</strong></div><div><span>规则版本</span><strong>{{ caseItem.rule_set_version }}</strong></div><div><span>规则命中</span><strong>{{ caseItem.rule_hits.length }} 条</strong></div></div>
        <v-tabs v-model="tab" class="case-tabs" color="primary" density="compact"><v-tab value="investigation" prepend-icon="mdi-creation">Agent 调查</v-tab><v-tab value="signals" prepend-icon="mdi-radar">规则信号</v-tab><v-tab value="review" prepend-icon="mdi-account-check-outline">人工审核</v-tab></v-tabs>
        <v-window v-model="tab" class="case-window">
          <v-window-item value="investigation" class="case-window-item"><InvestigationThread :case-item="caseItem" @completed="$emit('refresh')" /></v-window-item>
          <v-window-item value="signals" class="case-window-item"><div class="content-column"><div class="section-intro compact"><div><span class="eyebrow">RULE SIGNALS</span><h2>规则触发</h2></div><p>{{ caseItem.summary }}</p></div><div class="rule-grid"><v-card v-for="hit in caseItem.rule_hits" :key="hit.rule_hit_id" class="rule-card"><div><span>{{ hit.rule_id }}</span><v-chip size="x-small" :color="priorityColor(hit.severity)" variant="tonal">{{ labels.priority[hit.severity] }}</v-chip></div><h3>{{ hit.rule_name }}</h3><p>{{ hit.reason }}</p><small>{{ hit.sources.join(' / ') }} · {{ hit.period }}</small></v-card></div></div></v-window-item>
          <v-window-item value="review" class="case-window-item"><div class="review-layout"><v-card class="review-form-card"><span class="eyebrow">HUMAN REVIEW</span><h2>提交审核决定</h2><v-select v-model="form.decision" :items="decisionOptions" label="审核决定" /><v-text-field v-model="form.reviewer" label="审核人" maxlength="100" /><v-textarea v-model="form.reason" label="审核原因" rows="3" maxlength="1000" /><v-text-field v-model="form.action" label="后续动作（可选）" maxlength="1000" /><v-text-field v-if="form.decision === 'MONITOR'" v-model="form.next_review_at" label="复查日期" type="date" /><v-btn block color="primary" :disabled="!canSubmit" :loading="submitting" @click="submitReview">提交人工审核</v-btn></v-card><section class="review-history"><div class="section-intro compact"><div><span class="eyebrow">AUDIT TRAIL</span><h2>审核历史</h2></div></div><article v-for="review in caseItem.reviews" :key="review.review_id"><div><strong>{{ review.reviewer }}</strong><v-chip size="x-small" variant="tonal">{{ reviewLabel(review.decision) }}</v-chip><span>{{ review.created_at }}</span></div><p>{{ review.reason }}</p><small v-if="review.action">后续动作：{{ review.action }}</small></article><div v-if="!caseItem.reviews.length" class="empty-state">还没有人工审核记录</div></section></div></v-window-item>
        </v-window>
      </template>
    </v-card>
  </v-dialog>
</template>
