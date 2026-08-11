<script setup>
import { LoaderCircle } from "lucide-vue-next";
defineProps({ variant: { type: String, default: "primary" }, loading: Boolean, disabled: Boolean, block: Boolean });
defineEmits(["click"]);
</script>
<template>
  <button
    type="button"
    :disabled="disabled || loading"
    @click="$emit('click')"
    class="inline-flex h-10 items-center justify-center gap-2 rounded-lg px-4 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50"
    :class="[variant === 'primary' ? 'bg-brand text-white hover:bg-brand-dark' : 'border border-border bg-white text-ink hover:bg-canvas', block ? 'w-full' : '']"
  >
    <LoaderCircle v-if="loading" :size="16" class="animate-spin" />
    <slot />
  </button>
</template>
