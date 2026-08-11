<script setup>
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { ChevronRight } from "lucide-vue-next";
import Badge from "./ui/Badge.vue";
import SelectInput from "./ui/SelectInput.vue";
import TextInput from "./ui/TextInput.vue";
import { formatMoney, labels, priorityColor, statusColor } from "../lib";
import { workspace } from "../store";

const router = useRouter();
const type = ref("");
const status = ref("");
const query = ref("");
const typeOptions = [
  { title: "全部类型", value: "" },
  { title: "客户应收", value: "ACCOUNTS_RECEIVABLE" },
  { title: "库存积压", value: "INVENTORY" },
];
const statusOptions = [{ title: "全部状态", value: "" }, ...Object.entries(labels.status).map(([value, title]) => ({ title, value }))];
const filtered = computed(() => {
  const keyword = String(query.value ?? "").trim().toLocaleLowerCase();
  return workspace.cases.filter((item) => {
    const matchesFilters = (!type.value || item.case_type === type.value) && (!status.value || item.status === status.value);
    if (!matchesFilters || !keyword) return matchesFilters;
    const searchable = [item.case_id, item.entity_label, item.summary, labels.caseType[item.case_type], labels.status[item.status]]
      .filter(Boolean)
      .join(" ")
      .toLocaleLowerCase();
    return searchable.includes(keyword);
  });
});

function openCase(caseId) {
  router.push(`/cases/${encodeURIComponent(caseId)}`);
}
</script>

<template>
  <div class="space-y-5">
    <div class="section-intro flex items-end justify-between gap-6">
      <div><span class="eyebrow">CASE QUEUE</span><h2>风险案件队列</h2></div>
      <p>规则命中是调查入口；优先级用于排队，不代表自动业务定性。</p>
    </div>

    <section class="card overflow-hidden">
      <div class="flex flex-wrap items-center gap-3 border-b border-border px-5 py-4">
        <SelectInput v-model="type" :options="typeOptions" class="w-[180px]" />
        <SelectInput v-model="status" :options="statusOptions" class="w-[180px]" />
        <TextInput v-model="query" search clearable class="w-[320px] max-w-full" placeholder="搜索案件、客户或物料" aria-label="搜索案件、客户或物料" @clear="query = ''" />
        <span class="ml-auto text-xs text-muted">共 {{ filtered.length }} 个案件</span>
      </div>

      <div class="overflow-x-auto">
        <table class="table-base min-w-[1050px]">
          <thead>
            <tr><th>优先级</th><th>案件主体</th><th>触发摘要</th><th>风险敞口</th><th>状态</th><th>观察期</th><th></th></tr>
          </thead>
          <tbody>
            <tr
              v-for="item in filtered"
              :key="item.case_id"
              tabindex="0"
              @click="openCase(item.case_id)"
              @keydown.enter="openCase(item.case_id)"
            >
              <td><Badge :tone="priorityColor(item.priority)">{{ labels.priority[item.priority] }}</Badge></td>
              <td>
                <strong class="block text-[13px] text-ink">{{ item.entity_label }}</strong>
                <small class="block text-xs text-muted">{{ labels.caseType[item.case_type] }}</small>
              </td>
              <td class="text-xs leading-[1.55] text-muted">{{ item.summary }}</td>
              <td class="money-cell">{{ formatMoney(item.exposure_amount) }}</td>
              <td><Badge :tone="statusColor(item.status)">{{ labels.status[item.status] }}</Badge></td>
              <td class="text-muted">{{ item.observation_date }}</td>
              <td><ChevronRight :size="16" class="text-faint" /></td>
            </tr>
            <tr v-if="!workspace.loading && !filtered.length"><td colspan="7" class="empty-state">当前筛选条件下没有案件</td></tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>
