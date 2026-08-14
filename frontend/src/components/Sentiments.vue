<script setup>
import { computed, ref, watch } from "vue";
import { ChevronLeft, ChevronRight } from "lucide-vue-next";
import Badge from "./ui/Badge.vue";
import Button from "./ui/Button.vue";
import Modal from "./ui/Modal.vue";
import SelectInput from "./ui/SelectInput.vue";
import TextArea from "./ui/TextArea.vue";
import TextInput from "./ui/TextInput.vue";
import { formatDate, labels, severityColor, verifyStatusColor } from "../lib";
import { verifySentiment, workspace } from "../store";

const filter = ref("");
const pageSize = ref("10");
const currentPage = ref(1);
const pageJump = ref("1");
const verifying = ref(null);
const verifier = ref("");

const statusOptions = [
  { title: "全部状态", value: "" },
  { title: "待核验", value: "PENDING" },
  { title: "已确认", value: "CONFIRMED" },
  { title: "已排除", value: "EXCLUDED" },
];
const pageSizeOptions = [10, 20, 50].map((value) => ({ title: `${value} 行/页`, value: String(value) }));

const filtered = computed(() => {
  const items = workspace.sentiments || [];
  if (!filter.value) return items;
  return items.filter((item) => item.verify_status === filter.value);
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
        <SelectInput v-model="filter" :options="statusOptions" class="w-[160px]" />
        <span class="ml-auto text-sm text-muted">共 {{ filtered.length }} 条</span>
      </div>

      <div class="overflow-x-auto">
        <table class="table-base table-fixed min-w-[1180px]">
          <colgroup>
            <col class="w-[22%]" />
            <col class="w-[9%]" />
            <col class="w-[13%]" />
            <col class="w-[9%]" />
            <col class="w-[8%]" />
            <col class="w-[9%]" />
            <col class="w-[8%]" />
            <col class="w-[10%]" />
            <col class="w-[12%]" />
          </colgroup>
          <thead>
            <tr>
              <th>标题 / 来源</th>
              <th>发布时间</th>
              <th>涉及主体</th>
              <th>事件类型</th>
              <th>严重程度</th>
              <th>影响金额</th>
              <th>真实性状态</th>
              <th>关联项目</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in paginated"
              :key="item.sentiment_id"
              class="hover:bg-canvas/60"
              :class="item.verify_status === 'EXCLUDED' ? 'opacity-50' : ''"
            >
              <td>
                <strong class="block truncate text-[0.8125rem] text-ink" :title="item.title">{{ item.title }}</strong>
                <span class="text-[0.75rem] text-muted">{{ item.source }} · {{ item.sentiment_id }}</span>
              </td>
              <td><span class="text-sm text-muted">{{ formatDate(item.published_at) }}</span></td>
              <td>
                <strong class="block truncate text-[0.8125rem] text-ink" :title="`${item.subject_type} ${item.subject}`">{{ item.subject_type }} {{ item.subject }}</strong>
              </td>
              <td><span class="text-sm text-muted">{{ item.event_type }}</span></td>
              <td>
                <Badge :tone="severityTone[item.severity]">{{ labels.severity[item.severity] || item.severity }}</Badge>
              </td>
              <td class="money-cell">{{ item.impact_amount_wan ? `${item.impact_amount_wan} 万元` : "—" }}</td>
              <td>
                <Badge :tone="verifyStatusColor(item.verify_status)">{{ labels.verifyStatus[item.verify_status] || item.verify_status }}</Badge>
              </td>
              <td><span class="block truncate text-sm text-muted" :title="item.related_project">{{ item.related_project || "—" }}</span></td>
              <td>
                <button
                  v-if="item.verify_status === 'PENDING'"
                  type="button"
                  class="inline-flex h-9 items-center rounded-lg bg-brand px-3 text-[0.8125rem] font-semibold text-white transition-colors hover:bg-brand-dark"
                  @click="openVerify(item)"
                >
                  核验
                </button>
                <span v-else class="text-[0.75rem] text-muted">{{ item.verify_label }}</span>
              </td>
            </tr>
            <tr v-if="!workspace.loading && !filtered.length"><td colspan="9" class="empty-state">暂无舆情数据</td></tr>
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
