<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { AlertCircle, Menu, PanelLeftClose, PanelLeftOpen, Radar } from "lucide-vue-next";
import { navItems } from "./router";
import { loadAll, runScan, workspace } from "./store";

const route = useRoute();
const router = useRouter();
const mobileNav = ref(false);
const expanded = ref(true);
const mobileQuery = typeof window !== "undefined" ? window.matchMedia("(max-width: 767px)") : null;
const isMobile = ref(mobileQuery ? mobileQuery.matches : false);
if (mobileQuery) {
  const onMobileChange = (event) => { isMobile.value = event.matches; };
  mobileQuery.addEventListener("change", onMobileChange);
}

const pageTitle = computed(() => (route.name === "case" ? "案件工作台" : route.meta.title || "佳华智审"));
const expandedState = computed(() => !isMobile.value && expanded.value);
const labelsVisible = computed(() => isMobile.value || expanded.value);
const toastVisible = ref(false);

function isActive(path) {
  if (path === "/cases") return route.path.startsWith("/cases");
  return route.path === path;
}
function navigate(path) {
  router.push(path);
  if (isMobile.value) mobileNav.value = false;
}
function toggleNavigation() {
  if (!isMobile.value) expanded.value = !expanded.value;
  else mobileNav.value = !mobileNav.value;
}
watch(
  () => route.fullPath,
  () => {
    if (isMobile.value) mobileNav.value = false;
  }
);
watch(
  () => workspace.status.error,
  (err) => {
    if (err) {
      toastVisible.value = true;
      setTimeout(() => (toastVisible.value = false), 6000);
    }
  }
);
onMounted(loadAll);
</script>

<template>
  <div class="min-h-screen bg-canvas">
    <div v-if="mobileNav" class="fixed inset-0 z-40 bg-black/40 md:hidden" @click="mobileNav = false"></div>

    <aside
      class="fixed inset-y-0 left-0 z-50 flex flex-col border-r border-border bg-surface transition-all duration-150 ease-out md:translate-x-0"
      :class="[labelsVisible ? 'w-[224px]' : 'w-[72px]', mobileNav ? 'translate-x-0' : '-translate-x-full']"
    >
      <div class="flex h-[72px] items-center gap-3 px-5" :class="{ 'justify-center px-3': !labelsVisible }">
        <span class="grid h-9 w-9 flex-none place-items-center rounded-lg bg-brand" aria-hidden="true">
          <span class="flex items-end gap-[3px]">
            <i class="block w-1 rounded-sm bg-white" style="height: 10px"></i>
            <i class="block w-1 rounded-sm bg-white" style="height: 16px"></i>
            <i class="block w-1 rounded-sm bg-white" style="height: 13px"></i>
          </span>
        </span>
        <div v-show="labelsVisible" class="leading-tight">
          <strong class="block text-[15px] text-ink">佳华智审</strong>
        </div>
      </div>

      <nav class="flex-1 space-y-1 overflow-y-auto px-4 py-4">
        <button
          v-for="item in navItems"
          :key="item.path"
          type="button"
          @click="navigate(item.path)"
          :title="item.label"
          class="relative flex h-11 w-full items-center gap-3 rounded-lg px-3 text-[13px] font-semibold transition-colors"
          :class="isActive(item.path) ? 'bg-brand-wash text-brand-deep' : 'text-muted hover:bg-canvas hover:text-brand'"
        >
          <span v-if="isActive(item.path)" class="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r bg-brand"></span>
          <component :is="item.icon" :size="18" class="flex-none" :class="{ 'mx-auto': !labelsVisible }" />
          <span v-show="labelsVisible">{{ item.label }}</span>
        </button>
      </nav>

      <button
        type="button"
        class="mx-4 mb-4 hidden h-10 items-center gap-3 rounded-lg border border-border px-3 text-sm font-semibold text-muted transition-colors hover:bg-brand-wash hover:text-brand md:flex"
        :class="{ 'justify-center': !labelsVisible }"
        :aria-label="labelsVisible ? '收起侧边栏' : '展开侧边栏'"
        :title="labelsVisible ? '收起侧边栏' : '展开侧边栏'"
        @click="toggleNavigation"
      >
        <PanelLeftClose v-if="labelsVisible" :size="18" class="flex-none" />
        <PanelLeftOpen v-else :size="18" class="flex-none" />
        <span v-show="labelsVisible">收起侧边栏</span>
      </button>
    </aside>

    <div class="flex min-h-screen flex-col" :class="expandedState ? 'md:pl-[224px]' : 'md:pl-[72px]'">
      <header class="sticky top-0 z-30 flex h-[72px] items-center gap-4 border-b border-border bg-surface/95 px-4 backdrop-blur md:px-6">
        <button
          type="button"
          class="grid h-10 w-10 flex-none place-items-center rounded-lg border border-border text-muted transition-colors hover:bg-brand-wash hover:text-brand md:hidden"
          aria-label="切换导航"
          @click="toggleNavigation"
        >
          <Menu :size="20" />
        </button>
        <strong class="block text-[15px] text-ink">{{ pageTitle }}</strong>
        <div class="flex-1"></div>
        <div class="hidden items-center gap-2 text-sm text-muted sm:flex">
          <span class="h-2 w-2 rounded-full" :class="workspace.status.error ? 'bg-danger' : 'bg-success'"></span>
          {{ workspace.status.text }}
        </div>
        <button
          type="button"
          :disabled="workspace.scanning"
          class="inline-flex h-10 items-center gap-2 rounded-lg bg-brand px-4 text-sm font-semibold text-white transition-colors hover:bg-brand-dark disabled:opacity-50"
          @click="runScan"
        >
          <Radar :size="16" :class="workspace.scanning ? 'animate-spin' : ''" />
          重新扫描
        </button>
      </header>

      <main :class="route.meta.full ? '' : 'mx-auto w-full max-w-[1536px] px-4 py-7 md:px-8'">
        <router-view v-slot="{ Component, route: currentRoute }">
          <transition name="page" mode="out-in">
            <component :is="Component" :key="currentRoute.fullPath" />
          </transition>
        </router-view>
      </main>
    </div>

    <div
      v-if="toastVisible"
      class="fixed bottom-5 left-1/2 z-50 flex -translate-x-1/2 items-center gap-2 rounded-lg border border-danger/30 bg-danger text-white px-4 py-3 text-sm shadow-lg"
    >
      <AlertCircle :size="16" />
      {{ workspace.status.text }}
    </div>
  </div>
</template>

<style>
.page-enter-active, .page-leave-active { transition: opacity 0.12s ease-out, transform 0.12s ease-out; }
.page-enter-from { opacity: 0; transform: translateY(4px); }
.page-leave-to { opacity: 0; }
</style>
