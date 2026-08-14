<script setup>
import { computed } from "vue";
const props = defineProps({ tone: { type: String, default: "brand" } });
const cls = computed(() => ({
  brand: "bg-brand-wash text-brand-deep",
  danger: "bg-danger-wash text-danger",
  warning: "bg-warning-wash text-warning-deep",
  yellow: "bg-[#fefce8] text-[#a16207]",
  orange: "bg-[#fff7ed] text-[#c2410c]",
  success: "bg-success-wash text-success-deep",
  info: "bg-gray-100 text-ink",
  neutral: "bg-gray-100 text-muted",
}[props.tone] || "bg-gray-100 text-muted"));
</script>
<template>
  <span class="inline-flex items-center gap-1 whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-semibold" :class="cls"><slot /></span>
</template>
