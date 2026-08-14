<script setup>
import { computed } from "vue";
const props = defineProps({
  data: { type: Array, default: () => [] },
  width: { type: Number, default: 120 },
  height: { type: Number, default: 30 },
  color: { type: String, default: "#465fff" },
});
const values = computed(() =>
  props.data.map((d) => Number(d?.score ?? d)).filter((v) => Number.isFinite(v))
);
const points = computed(() => {
  const v = values.value;
  if (v.length < 2) return "";
  const min = Math.min(...v);
  const max = Math.max(...v);
  const range = max - min || 1;
  const stepX = props.width / (v.length - 1);
  return v
    .map((val, i) => {
      const x = i * stepX;
      const y = props.height - 3 - ((val - min) / range) * (props.height - 6);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
});
const flat = computed(() => values.value.length > 0 && new Set(values.value).size === 1);
</script>
<template>
  <div class="inline-flex items-center gap-1.5">
    <svg :width="width" :height="height" :viewBox="`0 0 ${width} ${height}`" class="overflow-visible" :aria-label="`近 ${values.length} 期趋势`">
      <polyline
        v-if="points && !flat"
        :points="points"
        fill="none"
        :stroke="color"
        stroke-width="1.6"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
      <line v-if="flat" x1="2" :y1="height / 2" :x2="width - 2" :y2="height / 2" :stroke="color" stroke-width="1.6" stroke-linecap="round" />
      <circle v-for="(p, i) in points.split(' ')" :key="i" :cx="p.split(',')[0]" :cy="p.split(',')[1]" r="1.8" :fill="color" />
    </svg>
    <span class="text-[0.6875rem] tabular-nums text-muted" v-if="values.length">{{ values[values.length - 1] }}</span>
  </div>
</template>
