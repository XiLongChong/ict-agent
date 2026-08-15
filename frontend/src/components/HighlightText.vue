<script setup>
import { computed } from "vue";

const props = defineProps({ text: { type: String, required: true } });

// 数字高亮规则：客户编号（C046）、金额（36,395.73元 / 1,742万元）、比例（2.46%）、
// 日期与时长（2026-07-31 / 8—9月 / 19天 / 12个月）。按类别统一配色。
const TOKEN =
  /(?<![A-Za-z0-9])C\d{3,}|(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:万元|亿元|元)|(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%|\d{4}-\d{2}(?:-\d{2})?|\d{1,2}(?:[—-]\d{1,2})?月|\d+(?:\.\d+)?(?:天|个月|年)/g;

const segments = computed(() => {
  const text = props.text || "";
  const parts = [];
  let cursor = 0;
  for (const match of text.matchAll(TOKEN)) {
    const index = match.index ?? 0;
    if (index > cursor) parts.push({ cls: "", value: text.slice(cursor, index) });
    parts.push({ cls: classify(match[0]), value: match[0] });
    cursor = index + match[0].length;
  }
  if (cursor < text.length) parts.push({ cls: "", value: text.slice(cursor) });
  return parts;
});

function classify(token) {
  if (/^C\d{3,}$/.test(token)) return "hl-code";
  if (token.endsWith("元") || token.endsWith("%")) return "hl-money";
  return "hl-time";
}
</script>

<template>
  <span>
    <span v-for="(part, index) in segments" :key="index" :class="part.cls || undefined">{{ part.value }}</span>
  </span>
</template>
