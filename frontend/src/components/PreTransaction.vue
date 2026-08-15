<script setup>
import { reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { ArrowRight, Dices, ShieldCheck } from "lucide-vue-next";
import Button from "./ui/Button.vue";
import SelectInput from "./ui/SelectInput.vue";
import TextInput from "./ui/TextInput.vue";
import { api, formatMoney, formatPercent, labels } from "../lib";
import { workspace } from "../store";

const router = useRouter();
const loading = ref(false);
const result = ref(null);
const form = reactive({
  customer_id: "",
  business_type: "",
  scenario: "RANDOM",
  seed: "",
});
const scenarios = [
  { title: "按 80/15/5 随机", value: "RANDOM" },
  { title: "符合历史习惯", value: "NORMAL" },
  { title: "临界偏高", value: "BORDERLINE" },
  { title: "明显异常", value: "ANOMALY" },
];
const businesses = [
  { title: "随机业务类型", value: "" },
  { title: "分销", value: "DISTRIBUTION" },
  { title: "项目", value: "PROJECT" },
  { title: "服务云", value: "SERVICE_CLOUD" },
];

async function simulate() {
  loading.value = true;
  try {
    result.value = await api("/api/v1/pre-transaction/simulations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        customer_id: form.customer_id || undefined,
        business_type: form.business_type || undefined,
        scenario: form.scenario,
        seed: form.seed === "" ? undefined : Number(form.seed),
      }),
    });
  } catch (error) {
    workspace.status = { text: error.message, error: true };
  } finally {
    loading.value = false;
  }
}

function openCase() {
  if (!result.value?.case_id) return;
  router.push({
    name: "case",
    params: { caseId: result.value.case_id },
    query: { from: "/pre-transaction" },
  });
}
</script>

<template>
  <div class="space-y-5">
    <header>
      <h1 class="text-2xl font-bold text-ink">事前交易模拟</h1>
      <p class="mt-1 max-w-3xl text-sm leading-6 text-muted">
        按客户同业务类型的历史订单分布生成一笔新交易，经统一准入与案件组装后进入 Agent 调查和人工复核链路。
        当前为演示模式，所有有效模拟订单一律立案；模拟订单不会写入真实销售、合同、应收或授信数据。
      </p>
    </header>

    <section class="card p-5">
      <div class="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <TextInput v-model="form.customer_id" placeholder="可选：指定客户编号" />
        <SelectInput v-model="form.business_type" :options="businesses" />
        <SelectInput v-model="form.scenario" :options="scenarios" />
        <TextInput v-model="form.seed" type="number" placeholder="可选：复现实验 seed" />
      </div>
      <div class="mt-4 flex flex-wrap items-center gap-3">
        <Button :loading="loading" @click="simulate">
          <Dices :size="16" />
          生成新交易并创建案件
        </Button>
        <span class="text-xs text-muted">未指定客户时，只会从存在正向历史订单的“客户 × 业务类型”中选择。</span>
      </div>
    </section>

    <section v-if="result" class="card overflow-hidden">
      <header class="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4">
        <div>
          <span class="text-xs font-semibold text-brand">已创建成交前调查案件</span>
          <h2 class="mt-1 text-lg font-bold text-ink">
            {{ result.customer_name }} · {{ labels.businessType[result.business_type] }}
          </h2>
        </div>
        <Button @click="openCase">
          进入案件调查
          <ArrowRight :size="16" />
        </Button>
      </header>

      <div class="grid gap-5 p-5 lg:grid-cols-[minmax(0,1fr)_minmax(280px,0.7fr)]">
        <dl class="grid grid-cols-2 gap-4 text-sm md:grid-cols-3">
          <div>
            <dt class="text-muted">拟交易金额</dt>
            <dd class="mt-1 font-semibold text-ink">{{ formatMoney(result.amount_yuan) }}</dd>
          </div>
          <div>
            <dt class="text-muted">拟账期</dt>
            <dd class="mt-1 font-semibold text-ink">{{ result.proposed_term_days }} 天</dd>
          </div>
          <div>
            <dt class="text-muted">预期毛利率</dt>
            <dd class="mt-1 font-semibold text-ink">{{ formatPercent(result.expected_margin_rate) }}</dd>
          </div>
          <div>
            <dt class="text-muted">历史正向订单</dt>
            <dd class="mt-1 font-semibold text-ink">{{ result.historical_order_count }} 笔</dd>
          </div>
          <div>
            <dt class="text-muted">历史金额中位数</dt>
            <dd class="mt-1 font-semibold text-ink">{{ formatMoney(result.distribution_summary.median_yuan) }}</dd>
          </div>
          <div>
            <dt class="text-muted">历史金额 P90</dt>
            <dd class="mt-1 font-semibold text-ink">{{ formatMoney(result.distribution_summary.p90_yuan) }}</dd>
          </div>
        </dl>

        <aside class="rounded-lg bg-canvas p-4 text-sm leading-6">
          <div class="flex items-center gap-2 font-semibold text-ink">
            <ShieldCheck :size="17" class="text-brand" />
            入口说明
          </div>
          <p class="mt-2 text-muted">
            本次场景为 {{ result.scenario }}，数据质量为 {{ result.data_quality_status }}。
            场景只决定模拟订单相对历史分布的位置，不直接判定客户风险。
          </p>
          <p v-if="result.data_quality_warnings?.length" class="mt-2 text-warning-deep">
            {{ result.data_quality_warnings.join("；") }}
          </p>
        </aside>
      </div>
    </section>
  </div>
</template>
