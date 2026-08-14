<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { AlertCircle, Menu, PanelLeftClose, PanelLeftOpen, Radar } from "lucide-vue-next";
import BrandMark from "./components/BrandMark.vue";
import { navItems } from "./router";
import { loadAll, loadRiskData, resetWorkspaceStatus, runScan, workspace } from "./store";

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

const pageTitle = computed(() => route.meta.title || "佳华智审");
const isStandalone = computed(() => Boolean(route.meta.standalone));
const expandedState = computed(() => !isMobile.value && expanded.value);
const labelsVisible = computed(() => isMobile.value || expanded.value);
const toastVisible = ref(false);

async function refreshRiskDataOnFocus() {
  try {
    await loadRiskData();
  } catch (error) {
    workspace.status = { text: error.message, error: true };
  }
}

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
    resetWorkspaceStatus();
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
onMounted(() => {
  void loadAll();
  window.addEventListener("focus", refreshRiskDataOnFocus);
});
onUnmounted(() => window.removeEventListener("focus", refreshRiskDataOnFocus));
</script>

<template>
  <div class="min-h-screen bg-canvas">
    <div v-if="mobileNav && !isStandalone" class="fixed inset-0 z-40 bg-black/40 md:hidden" @click="mobileNav = false"></div>

    <aside
      v-if="!isStandalone"
      class="fixed inset-y-0 left-0 z-50 flex flex-col overflow-hidden border-r border-border bg-surface transition-[width,transform] duration-200 ease-out motion-reduce:transition-none md:translate-x-0"
      :class="[labelsVisible ? 'w-[200px]' : 'w-16', mobileNav ? 'translate-x-0' : '-translate-x-full']"
    >
      <div class="flex h-[72px] items-center px-3">
        <span class="grid h-9 w-10 flex-none place-items-center">
          <BrandMark />
        </span>
        <div
          class="ml-2 flex-none whitespace-nowrap leading-tight transition-opacity duration-100"
          :class="labelsVisible ? 'delay-100 opacity-100' : 'opacity-0'"
        >
          <strong class="block text-[15px] text-ink">佳华智审</strong>
        </div>
      </div>

      <nav class="flex-1 space-y-1 overflow-x-hidden overflow-y-auto px-3 py-4">
        <button
          v-for="item in navItems"
          :key="item.path"
          type="button"
          @click="navigate(item.path)"
          :title="item.label"
          class="relative flex h-11 w-full items-center rounded-lg text-[13px] font-semibold transition-colors"
          :class="isActive(item.path) ? 'bg-brand-wash text-brand-deep' : 'text-muted hover:bg-canvas hover:text-brand'"
        >
          <span v-if="isActive(item.path)" class="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r bg-brand"></span>
          <span class="grid h-11 w-10 flex-none place-items-center"><component :is="item.icon" :size="18" /></span>
          <span
            class="ml-2 flex-none whitespace-nowrap transition-opacity duration-100"
            :class="labelsVisible ? 'delay-100 opacity-100' : 'opacity-0'"
          >{{ item.label }}</span>
        </button>
      </nav>

      <button
        type="button"
        class="mx-3 mb-4 hidden h-10 items-center rounded-lg border border-border text-sm font-semibold text-muted transition-colors hover:bg-brand-wash hover:text-brand md:flex"
        :aria-label="labelsVisible ? '收起侧边栏' : '展开侧边栏'"
        :title="labelsVisible ? '收起侧边栏' : '展开侧边栏'"
        @click="toggleNavigation"
      >
        <span class="grid h-10 w-10 flex-none place-items-center">
          <PanelLeftClose v-if="labelsVisible" :size="18" />
          <PanelLeftOpen v-else :size="18" />
        </span>
        <span
          class="ml-2 flex-none whitespace-nowrap transition-opacity duration-100"
          :class="labelsVisible ? 'delay-100 opacity-100' : 'opacity-0'"
        >收起侧边栏</span>
      </button>
    </aside>

    <div
      class="flex min-h-screen min-w-0 flex-col overflow-x-clip transition-[padding-left] duration-200 ease-out motion-reduce:transition-none md:will-change-[padding-left]"
      :class="isStandalone ? '' : expandedState ? 'md:pl-[200px]' : 'md:pl-16'"
    >
      <header v-if="!isStandalone" class="sticky top-0 z-30 flex h-[72px] items-center gap-4 border-b border-border bg-surface/95 px-4 backdrop-blur md:px-6">
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

      <main
        class="w-full"
        :class="isStandalone ? '' : 'mx-auto max-w-[1536px] px-4 py-7 md:px-8'"
      >
        <router-view v-slot="{ Component, route: currentRoute }">
          <component :is="Component" :key="currentRoute.fullPath" />
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
