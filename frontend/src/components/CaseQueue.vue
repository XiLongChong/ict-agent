<script setup>
import { computed, ref } from "vue";
import { formatMoney, labels, priorityColor, statusColor } from "../lib";

const props = defineProps({ cases: Array, loading: Boolean });
defineEmits(["open-case"]);
const type = ref("");
const status = ref("");
const query = ref("");
const typeOptions = [{ title: "全部类型", value: "" }, { title: "客户应收", value: "ACCOUNTS_RECEIVABLE" }, { title: "库存积压", value: "INVENTORY" }];
const statusOptions = [{ title: "全部状态", value: "" }, ...Object.entries(labels.status).map(([value, title]) => ({ title, value }))];
const filtered = computed(() => {
  const keyword = String(query.value ?? "").trim().toLocaleLowerCase();
  return (props.cases || []).filter((item) => {
    const matchesFilters = (!type.value || item.case_type === type.value) && (!status.value || item.status === status.value);
    if (!matchesFilters || !keyword) return matchesFilters;
    const searchable = [item.case_id, item.entity_label, item.summary, labels.caseType[item.case_type], labels.status[item.status]]
      .filter(Boolean)
      .join(" ")
      .toLocaleLowerCase();
    return searchable.includes(keyword);
  });
});
</script>

<template>
  <div class="view-stack">
    <div class="section-intro"><div><span class="eyebrow">CASE QUEUE</span><h2>风险案件队列</h2></div><p>规则命中是调查入口；优先级用于排队，不代表自动业务定性。</p></div>
    <v-card class="flat-panel queue-panel">
      <div class="filter-bar">
        <v-select v-model="type" :items="typeOptions" label="案件类型" />
        <v-select v-model="status" :items="statusOptions" label="案件状态" />
        <v-text-field
          v-model="query"
          class="queue-search"
          type="search"
          placeholder="搜索案件、客户或物料"
          aria-label="搜索案件、客户或物料"
          prepend-inner-icon="mdi-magnify"
          density="compact"
          variant="outlined"
          hide-details
          clearable
          @click:clear="query = ''"
        />
        <span>共 {{ filtered.length }} 个案件</span>
      </div>
      <div class="table-scroll">
        <v-table hover density="comfortable" class="case-table">
          <thead><tr><th>优先级</th><th>案件主体</th><th>触发摘要</th><th>风险敞口</th><th>状态</th><th>观察期</th><th></th></tr></thead>
          <tbody>
            <tr v-for="item in filtered" :key="item.case_id" tabindex="0" @click="$emit('open-case', item.case_id)" @keydown.enter="$emit('open-case', item.case_id)">
              <td><v-chip size="small" :color="priorityColor(item.priority)" variant="tonal">{{ labels.priority[item.priority] }}</v-chip></td>
              <td><strong>{{ item.entity_label }}</strong><small>{{ labels.caseType[item.case_type] }}</small></td>
              <td class="summary-cell">{{ item.summary }}</td><td class="money-cell">{{ formatMoney(item.exposure_amount) }}</td>
              <td><v-chip size="small" :color="statusColor(item.status)" variant="tonal">{{ labels.status[item.status] }}</v-chip></td><td>{{ item.observation_date }}</td><td><v-icon icon="mdi-chevron-right" /></td>
            </tr>
            <tr v-if="!loading && !filtered.length"><td colspan="7" class="empty-state">当前筛选条件下没有案件</td></tr>
          </tbody>
        </v-table>
      </div>
    </v-card>
  </div>
</template>
