<script setup>
import { X } from "lucide-vue-next";
defineProps({ open: Boolean, title: String, width: { type: String, default: "max-w-lg" } });
defineEmits(["close"]);
</script>
<template>
  <Teleport to="body">
    <div v-if="open" class="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" :aria-modal="open" aria-label="对话框">
      <div class="absolute inset-0 bg-black/40" @click="$emit('close')"></div>
      <div class="relative max-h-[calc(100vh-2rem)] w-full overflow-y-auto rounded-xl border border-border bg-surface shadow-lg" :class="width">
        <div class="flex items-center justify-between gap-4 border-b border-border px-5 py-4">
          <h3 class="text-[0.9375rem] font-bold text-ink">{{ title }}</h3>
          <button
            type="button"
            class="grid h-8 w-8 flex-none place-items-center rounded-lg text-muted transition-colors hover:bg-canvas hover:text-ink"
            aria-label="关闭"
            @click="$emit('close')"
          >
            <X :size="18" />
          </button>
        </div>
        <div class="px-5 py-4"><slot /></div>
      </div>
    </div>
  </Teleport>
</template>
