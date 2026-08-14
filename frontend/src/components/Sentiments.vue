<script setup>
import { computed, ref } from "vue";
import Badge from "./ui/Badge.vue";
import Button from "./ui/Button.vue";
import Modal from "./ui/Modal.vue";
import TextArea from "./ui/TextArea.vue";
import TextInput from "./ui/TextInput.vue";
import { formatDate, labels, severityColor, verifyStatusColor } from "../lib";
import { verifySentiment, workspace } from "../store";

const filter = ref("");
const verifying = ref(null);
const verifier = ref("");

const sentiments = computed(() => {
  const items = workspace.sentiments || [];
  if (!filter.value) return items;
  return items.filter((item) => item.verify_status === filter.value);
});

const statusOptions = [
  { value: "", label: "全部状态" },
  { value: "PENDING", label: "待核验" },
  { value: "CONFIRMED", label: "已确认" },
  { value: "EXCLUDED", label: "已排除" },
];

const severityTone = {
  LOW: "neutral",
  MEDIUM: "warning",
  HIGH: "danger",
  CRITICAL: "danger",
};

function openVerify(item) {
  verifying.value = item;
  verifier.value = "";
}

async function submitVerify(decision) {
  if (!verifier.value.trim()) {
    workspace.status = { text: "请填写核验人", error: true };
    return;
  }
  try {
    await verifySentiment(verifying.value.sentiment_id, { decision, verifier: verifier.value.trim() });
    verifying.value = null;
  } catch {
    // 状态由 store 统一提示
  }
}
</script>

<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-center gap-3">
      <h2 class="text-[17px] font-bold text-ink">舆情监控</h2>
      <Badge tone="warning">模拟数据</Badge>
      <div class="flex-1"></div>
      <select
        v-model="filter"
        class="h-10 rounded-lg border border-border bg-surface px-3 text-sm text-ink outline-none focus:border-brand"
      >
        <option v-for="option in statusOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
      </select>
    </div>

    <section class="card overflow-hidden">
      <div class="overflow-x-auto">
        <table class="w-full min-w-[980px] text-left text-sm">
          <thead>
            <tr class="border-b border-border text-[12px] text-muted">
              <th class="px-4 py-3 font-semibold">标题 / 来源</th>
              <th class="px-4 py-3 font-semibold">发布时间</th>
              <th class="px-4 py-3 font-semibold">涉及主体</th>
              <th class="px-4 py-3 font-semibold">事件类型</th>
              <th class="px-4 py-3 font-semibold">严重程度</th>
              <th class="px-4 py-3 font-semibold">影响金额</th>
              <th class="px-4 py-3 font-semibold">真实性状态</th>
              <th class="px-4 py-3 font-semibold">关联项目</th>
              <th class="px-4 py-3 font-semibold">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in sentiments"
              :key="item.sentiment_id"
              class="border-b border-border/60 last:border-0 hover:bg-canvas/60"
              :class="item.verify_status === 'EXCLUDED' ? 'opacity-50' : ''"
            >
              <td class="px-4 py-3">
                <strong class="block max-w-[280px] text-ink">{{ item.title }}</strong>
                <span class="text-[12px] text-muted">{{ item.source }} · {{ item.sentiment_id }}</span>
              </td>
              <td class="px-4 py-3 text-muted">{{ formatDate(item.published_at) }}</td>
              <td class="px-4 py-3">
                <span class="block text-ink">{{ item.subject }}</span>
                <span class="text-[12px] text-muted">{{ item.subject_type }}</span>
              </td>
              <td class="px-4 py-3 text-muted">{{ item.event_type }}</td>
              <td class="px-4 py-3">
                <Badge :tone="severityColor(item.severity)">{{ labels.severity[item.severity] || item.severity }}</Badge>
              </td>
              <td class="px-4 py-3 text-muted tabular-nums">{{ item.impact_amount_wan ? `${item.impact_amount_wan} 万元` : "—" }}</td>
              <td class="px-4 py-3">
                <Badge :tone="verifyStatusColor(item.verify_status)">{{ labels.verifyStatus[item.verify_status] || item.verify_status }}</Badge>
              </td>
              <td class="px-4 py-3 text-muted">{{ item.related_project || "—" }}</td>
              <td class="px-4 py-3">
                <button
                  v-if="item.verify_status === 'PENDING'"
                  type="button"
                  class="inline-flex h-8 items-center rounded-lg bg-brand px-3 text-[12px] font-semibold text-white transition-colors hover:bg-brand-dark"
                  @click="openVerify(item)"
                >
                  核验
                </button>
                <span v-else class="text-[12px] text-muted">{{ item.verify_label }}</span>
              </td>
            </tr>
            <tr v-if="!sentiments.length">
              <td colspan="9" class="empty-state px-4 py-10 text-center">暂无舆情数据</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <Modal :open="Boolean(verifying)" title="舆情核验" @close="verifying = null">
      <div v-if="verifying" class="space-y-4">
        <p class="text-sm text-muted">{{ verifying.title }}</p>
        <TextInput v-model="verifier" placeholder="核验人姓名" />
        <div class="flex justify-end gap-2">
          <Button tone="neutral" @click="verifying = null">取消</Button>
          <Button tone="danger" @click="submitVerify('EXCLUDED')">排除（误报）</Button>
          <Button tone="success" @click="submitVerify('CONFIRMED')">确认属实</Button>
        </div>
      </div>
    </Modal>
  </div>
</template>
