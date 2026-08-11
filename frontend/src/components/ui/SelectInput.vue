<script setup>
import { ChevronDown } from "lucide-vue-next";
defineProps({ modelValue: String, options: { type: Array, required: true } });
defineEmits(["update:modelValue"]);
</script>
<template>
  <div class="relative">
    <select
      :value="modelValue"
      @change="$emit('update:modelValue', $event.target.value)"
      class="h-10 w-full appearance-none rounded-lg border border-border bg-white pl-3 pr-9 text-sm text-ink outline-none transition-colors hover:border-gray-300 focus:border-brand focus:ring-2 focus:ring-brand-wash"
    >
      <option v-for="o in options" :key="o.value" :value="o.value">{{ o.title }}</option>
    </select>
    <ChevronDown :size="16" class="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-faint" />
  </div>
</template>
