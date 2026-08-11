<script setup>
defineProps({ modelValue: String, placeholder: String, rows: { type: Number, default: 3 }, maxlength: Number });
defineEmits(["update:modelValue"]);
</script>
<template>
  <textarea
    :value="modelValue"
    :placeholder="placeholder"
    :rows="rows"
    :maxlength="maxlength"
    @input="$emit('update:modelValue', $event.target.value)"
    class="w-full resize-y rounded-lg border border-border bg-white px-3 py-2.5 text-sm text-ink outline-none transition-colors placeholder:text-faint hover:border-gray-300 focus:border-brand focus:ring-2 focus:ring-brand-wash"
  ></textarea>
</template>
