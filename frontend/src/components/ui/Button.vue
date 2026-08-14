<script setup>
import { LoaderCircle } from "lucide-vue-next";
defineProps({
  variant: { type: String, default: "primary" },
  tone: { type: String, default: "" },
  size: { type: String, default: "" },
  loading: Boolean,
  disabled: Boolean,
  block: Boolean,
});
defineEmits(["click"]);
</script>
<template>
  <button
    type="button"
    :disabled="disabled || loading"
    @click="$emit('click')"
    class="inline-flex items-center justify-center gap-2 rounded-lg px-4 font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50"
    :class="[
      size === 'sm' ? 'h-9 text-[0.8125rem]' : 'h-10 text-sm',
      tone === 'neutral' ? 'border border-border bg-white text-ink hover:bg-canvas'
      : tone === 'danger' ? 'bg-danger text-white hover:bg-danger/90'
      : tone === 'success' ? 'bg-success text-white hover:bg-success/90'
      : tone === 'brand' ? 'bg-brand text-white hover:bg-brand-dark'
      : variant === 'primary' ? 'bg-brand text-white hover:bg-brand-dark'
      : 'border border-border bg-white text-ink hover:bg-canvas',
      block ? 'w-full' : '',
    ]"
  >
    <LoaderCircle v-if="loading" :size="16" class="animate-spin" />
    <slot />
  </button>
</template>
