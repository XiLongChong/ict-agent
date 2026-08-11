<script setup>
defineProps({ tabs: { type: Array, required: true }, modelValue: { type: String, required: true } });
defineEmits(["update:modelValue"]);
</script>
<template>
  <div class="flex items-center gap-1 border-b border-border bg-surface px-5">
    <button
      v-for="t in tabs"
      :key="t.value"
      type="button"
      @click="$emit('update:modelValue', t.value)"
      class="-mb-px inline-flex items-center gap-2 border-b-2 px-4 py-3 text-sm font-semibold transition-colors"
      :class="modelValue === t.value ? 'border-brand text-brand' : 'border-transparent text-muted hover:text-ink'"
    >
      <component :is="t.icon" :size="16" />
      {{ t.label }}
    </button>
  </div>
</template>
