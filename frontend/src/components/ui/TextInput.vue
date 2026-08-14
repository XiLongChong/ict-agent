<script setup>
import { Search, X } from "lucide-vue-next";
defineProps({
  modelValue: String,
  type: { type: String, default: "text" },
  placeholder: String,
  search: Boolean,
  clearable: Boolean,
  maxlength: Number,
  ariaLabel: String,
  clearAriaLabel: { type: String, default: "清空输入内容" },
});
defineEmits(["update:modelValue", "clear"]);
</script>
<template>
  <div class="relative">
    <Search v-if="search" :size="16" class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
    <input
      :type="type"
      :value="modelValue"
      :placeholder="placeholder"
      :maxlength="maxlength"
      :aria-label="ariaLabel"
      @input="$emit('update:modelValue', $event.target.value)"
      class="h-10 w-full rounded-lg border border-border bg-white text-sm text-ink outline-none transition-colors placeholder:text-faint hover:border-gray-300 focus:border-brand focus:ring-2 focus:ring-brand-wash"
      :class="search ? 'pl-9 pr-9' : 'px-3'"
    />
    <button
      v-if="clearable && modelValue"
      type="button"
      :aria-label="clearAriaLabel"
      :title="clearAriaLabel"
      @click="$emit('clear')"
      class="absolute right-2 top-1/2 grid h-6 w-6 -translate-y-1/2 place-items-center rounded text-faint hover:bg-gray-100 hover:text-muted"
    >
      <X :size="14" />
    </button>
  </div>
</template>
