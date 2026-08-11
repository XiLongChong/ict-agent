# 前端 UI 改造实现计划（Vuetify → TailAdmin/Tailwind）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 去掉 Vuetify，用 Tailwind CSS 4 + lucide 图标 + ApexCharts 按 TailAdmin 模板设计系统重建前端全部页面，保留全部现有功能。

**Architecture:** 纯前端改造，后端与数据层不动。删除 Vuetify 依赖与用法，以 Tailwind 工具类 + 少量自建 `ui/` 基础件重建 App 壳与 5 个页面组件；`lib.js`/`store.js`/`router.js` 仅改色值映射与图标引用；ApexCharts 用于风险构成圆环与应收趋势折线。前端无测试框架，每个任务的"测试"= `npm run build` 通过 + grep 断言无 Vuetify 残留，最终集成任务做视觉与功能验收。

**Tech Stack:** Vue 3.5、Vite 7、Tailwind CSS 4（`@tailwindcss/postcss`）、lucide-vue-next、apexcharts + vue3-apexcharts、vue-router 4。

**工作目录约定：** 所有命令在 `D:\作业\aaachagent\ict-agent-fresh\frontend` 下运行（bash，Git Bash）。

## Global Constraints

- 数据层接口不变：`store.js` 的 `workspace`/`loadAll`/`loadRiskData`/`runScan`，`lib.js` 的 `api`/`streamNdjson`/`formatMoney`/`labels` 签名不动（色值映射函数允许改返回值）。
- 功能保真：风险总览、案件队列、案件工作台（3 Tab + 审核表单）、NDJSON 调查流、经营分析 5 个页面全部保留，行为与现有版本等价。
- 不引入 TypeScript、Pinia、暗色模式；不搬模板的电商/日历/拖拽/地图等无关页面。
- 颜色令牌以 spec 为准：canvas `#f9fafb`、surface `#fff`、border `#e4e7ec`、ink `#101828`、muted `#667085`、faint `#98a2b3`、brand `#465fff`、danger `#d92d20`、warning `#f79009`、success `#039855`；8px 圆角、`0 1px 3px rgba(16,24,40,.05)` 阴影。
- 中文界面文案保持现有措辞；金额用 `formatMoney`。
- 前端无测试框架，验收靠构建 + grep 断言 + 无头截图 + 人工功能冒烟。

---

### Task 1: 依赖与构建配置切换到 Tailwind

**Files:**
- Modify: `package.json`
- Modify: `vite.config.js`
- Create: `postcss.config.js`
- Modify: `src/main.js`
- Rewrite: `src/styles.css`
- Modify: `src/router.js`（navItems 图标换 lucide）

**Interfaces:**
- Produces: `main.js` 以 `createApp(App).use(router).mount("#app")` 挂载（无 Vuetify）；`styles.css` 提供 `@theme` 令牌（`brand/brand-wash/brand-deep/border/canvas/ink/muted/faint/danger/danger-wash/warning/warning-deep/success/success-deep/card 阴影`）与组件类 `.card/.eyebrow/.page-heading/.section-intro/.panel-head/.section-index/.table-base/.empty-state/.money-cell/.subtle-copy`；`router.js` 的 `navItems[].icon` 是 lucide Vue 组件引用。后续任务都依赖这些令牌和类。

- [ ] **Step 1: 更新 package.json 依赖**

将 `frontend/package.json` 内容替换为：

```json
{
  "name": "ict-agent-frontend",
  "private": true,
  "version": "1.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite --host 127.0.0.1",
    "build": "vite build",
    "preview": "vite preview --host 127.0.0.1"
  },
  "dependencies": {
    "@fontsource/dm-sans": "^5.2.6",
    "@fontsource/jetbrains-mono": "^5.2.6",
    "apexcharts": "^4.4.0",
    "lucide-vue-next": "^0.474.0",
    "vue": "^3.5.18",
    "vue-router": "^4.5.0",
    "vue3-apexcharts": "^1.8.0"
  },
  "devDependencies": {
    "@tailwindcss/postcss": "^4.0.0",
    "@vitejs/plugin-vue": "^6.0.1",
    "postcss": "^8.5.1",
    "tailwindcss": "^4.0.0",
    "vite": "^7.1.1"
  }
}
```

- [ ] **Step 2: 安装依赖**

Run: `cd "D:\作业\aaachagent\ict-agent-fresh\frontend" && npm install`
Expected: 安装成功，无 peer 冲突报错。

- [ ] **Step 3: 更新 vite.config.js 与新增 postcss.config.js**

将 `vite.config.js` 替换为：

```js
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  base: "/static/",
  build: { outDir: "dist", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
```

新建 `postcss.config.js`：

```js
export default { plugins: { "@tailwindcss/postcss": {} } };
```

- [ ] **Step 4: 重写 main.js（去 Vuetify）**

将 `src/main.js` 替换为：

```js
import { createApp } from "vue";
import "@fontsource/dm-sans/400.css";
import "@fontsource/dm-sans/500.css";
import "@fontsource/dm-sans/600.css";
import "@fontsource/dm-sans/700.css";
import "@fontsource/jetbrains-mono/500.css";
import App from "./App.vue";
import router from "./router";
import "./styles.css";

createApp(App).use(router).mount("#app");
```

- [ ] **Step 5: 重写 styles.css（Tailwind 令牌 + 组件类）**

将 `src/styles.css` 整体替换为：

```css
@import "tailwindcss";

@theme {
  --color-canvas: #f9fafb;
  --color-surface: #ffffff;
  --color-border: #e4e7ec;
  --color-ink: #101828;
  --color-muted: #667085;
  --color-faint: #98a2b3;
  --color-brand: #465fff;
  --color-brand-dark: #3641f5;
  --color-brand-deep: #3538cd;
  --color-brand-wash: #eef4ff;
  --color-danger: #d92d20;
  --color-danger-deep: #b42318;
  --color-danger-wash: #fef3f2;
  --color-warning: #f79009;
  --color-warning-deep: #b54708;
  --color-warning-wash: #fffaeb;
  --color-success: #039855;
  --color-success-deep: #027a48;
  --color-success-wash: #ecfdf3;
  --shadow-card: 0 1px 3px rgba(16, 24, 40, 0.05);
  --font-sans: "DM Sans", "Microsoft YaHei", system-ui, sans-serif;
  --font-mono: "JetBrains Mono", monospace;
}

@layer base {
  html, body, #app { min-height: 100%; }
  body { background: var(--color-canvas); color: var(--color-ink); font-family: var(--font-sans); -webkit-font-smoothing: antialiased; }
  h1, h2, h3, h4, h5, h6 { letter-spacing: 0; }
  button, input, textarea, select { font: inherit; }
}

@layer components {
  .card { background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 8px; box-shadow: var(--shadow-card); }
  .eyebrow { font-family: var(--font-mono); font-size: 11px; letter-spacing: 0.04em; text-transform: uppercase; color: var(--color-faint); }
  .page-heading { margin-bottom: 24px; }
  .page-heading span { color: var(--color-faint); font-size: 11px; font-weight: 500; }
  .page-heading h1 { margin: 7px 0 0; font-size: 28px; font-weight: 700; color: var(--color-ink); }
  .section-intro h2 { margin: 7px 0 0; font-size: 27px; font-weight: 700; color: var(--color-ink); }
  .section-intro p { margin: 0; max-width: 600px; color: var(--color-muted); font-size: 13px; }
  .panel-head { display: flex; align-items: center; justify-content: space-between; gap: 14px; min-height: 58px; padding: 12px 18px; border-bottom: 1px solid var(--color-border); }
  .panel-head h3 { margin: 0; font-size: 15px; color: #1d2939; }
  .section-index { display: inline-grid; place-items: center; width: 28px; height: 28px; border-radius: 6px; background: var(--color-brand-wash); color: var(--color-brand-deep); font-family: var(--font-mono); font-size: 11px; font-weight: 600; }
  .table-base { width: 100%; border-collapse: collapse; font-size: 13px; }
  .table-base thead th { padding: 10px 16px; text-align: left; color: var(--color-muted); font-size: 12px; font-weight: 600; background: var(--color-canvas); border-bottom: 1px solid var(--color-border); white-space: nowrap; }
  .table-base tbody td { padding: 13px 16px; border-bottom: 1px solid var(--color-border); vertical-align: middle; }
  .table-base tbody tr { cursor: pointer; transition: background 0.12s ease-out; }
  .table-base tbody tr:hover { background: var(--color-canvas); }
  .empty-state { padding: 32px; text-align: center; color: var(--color-muted); }
  .money-cell { font-weight: 600; white-space: nowrap; }
  .subtle-copy { color: var(--color-muted); font-size: 12px; }
}
```

- [ ] **Step 6: 更新 router.js 的 navItems 图标**

将 `src/router.js` 开头改为（其余路由定义不变）：

```js
import { createRouter, createWebHistory } from "vue-router";
import { ChartLine, LayoutDashboard, ListTodo } from "lucide-vue-next";
import RiskOverview from "./components/RiskOverview.vue";
import CaseQueue from "./components/CaseQueue.vue";
import BusinessView from "./components/BusinessView.vue";
import CaseWorkspace from "./components/CaseWorkspace.vue";

export const navItems = [
  { path: "/risk", label: "风险总览", icon: LayoutDashboard },
  { path: "/cases", label: "案件队列", icon: ListTodo },
  { path: "/business", label: "经营分析", icon: ChartLine },
];
```

- [ ] **Step 7: 构建验证**

Run: `npm run build`
Expected: 构建成功，dist 生成。
Run: `grep -rn "createVuetify\|vuetify\|@mdi" src/ vite.config.js postcss.config.js || echo "CLEAN"`
Expected: 输出 `CLEAN`（无 Vuetify 残留）。

- [ ] **Step 8: 提交**

```bash
git add package.json package-lock.json vite.config.js postcss.config.js src/main.js src/styles.css src/router.js
git commit -m "chore: switch frontend to Tailwind CSS 4 stack, drop Vuetify"
```

---

### Task 2: lib.js 色值映射 + 自建 ui 基础件

**Files:**
- Modify: `src/lib.js`（4 个色值映射函数改返回语义 tone 名）
- Create: `src/components/ui/Badge.vue`
- Create: `src/components/ui/Button.vue`
- Create: `src/components/ui/Tabs.vue`
- Create: `src/components/ui/SelectInput.vue`
- Create: `src/components/ui/TextInput.vue`
- Create: `src/components/ui/TextArea.vue`

**Interfaces:**
- Consumes: `styles.css` 的 `@theme` 令牌（Task 1）。
- Produces: `priorityColor/statusColor/stageColor/hypothesisColor` 返回 `"danger"|"warning"|"success"|"brand"|"neutral"|"info"` 之一；`ui/Badge.vue`（props `tone`，默认 `brand`）、`ui/Button.vue`（props `variant`/`loading`/`disabled`，emit `click`）、`ui/Tabs.vue`（props `tabs:[{value,label,icon}]`、`modelValue`，emit `update:modelValue`）、`ui/SelectInput.vue`（props `modelValue`/`options:[{title,value}]`，emit `update:modelValue`）、`ui/TextInput.vue`（props `modelValue`/`type`/`placeholder`/`search`/`clearable`，emit `update:modelValue`/`clear`）、`ui/TextArea.vue`（props `modelValue`/`placeholder`/`rows`/`maxlength`，emit `update:modelValue`）。

- [ ] **Step 1: 修改 lib.js 色值映射**

在 `src/lib.js` 中替换这 4 行为（其余不动）：

```js
export const priorityColor = (value) => ({ CRITICAL: "danger", HIGH: "warning", MEDIUM: "brand", LOW: "neutral" }[value] || "neutral");
export const statusColor = (value) => ({ PENDING_REVIEW: "warning", ACTION_REQUIRED: "danger", MONITORING: "info", CLOSED_RESOLVED: "success", CLOSED_FALSE_POSITIVE: "success", OPEN: "brand", INVESTIGATING: "brand" }[value] || "brand");
export const stageColor = (value) => ({ DETERIORATING: "danger", EARLY_WARNING: "warning", LIMITED: "neutral" }[value] || "neutral");
export const hypothesisColor = (value) => ({ SUPPORTED: "success", WEAKENED: "neutral", UNRESOLVED: "warning" }[value] || "neutral");
```

- [ ] **Step 2: 创建 Badge.vue**

新建 `src/components/ui/Badge.vue`：

```vue
<script setup>
import { computed } from "vue";
const props = defineProps({ tone: { type: String, default: "brand" } });
const cls = computed(() => ({
  brand: "bg-brand-wash text-brand-deep",
  danger: "bg-danger-wash text-danger",
  warning: "bg-warning-wash text-warning-deep",
  success: "bg-success-wash text-success-deep",
  info: "bg-gray-100 text-ink",
  neutral: "bg-gray-100 text-muted",
}[props.tone] || "bg-gray-100 text-muted"));
</script>
<template>
  <span class="inline-flex items-center gap-1 whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-semibold" :class="cls"><slot /></span>
</template>
```

- [ ] **Step 3: 创建 Button.vue**

新建 `src/components/ui/Button.vue`：

```vue
<script setup>
import { LoaderCircle } from "lucide-vue-next";
defineProps({ variant: { type: String, default: "primary" }, loading: Boolean, disabled: Boolean });
defineEmits(["click"]);
</script>
<template>
  <button
    type="button"
    :disabled="disabled || loading"
    @click="$emit('click')"
    class="inline-flex h-10 items-center justify-center gap-2 rounded-lg px-4 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50"
    :class="variant === 'primary' ? 'bg-brand text-white hover:bg-brand-dark' : 'border border-border bg-white text-ink hover:bg-canvas'"
  >
    <LoaderCircle v-if="loading" :size="16" class="animate-spin" />
    <slot />
  </button>
</template>
```

- [ ] **Step 4: 创建 Tabs.vue**

新建 `src/components/ui/Tabs.vue`：

```vue
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
```

- [ ] **Step 5: 创建 SelectInput.vue**

新建 `src/components/ui/SelectInput.vue`：

```vue
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
```

- [ ] **Step 6: 创建 TextInput.vue**

新建 `src/components/ui/TextInput.vue`：

```vue
<script setup>
import { Search, X } from "lucide-vue-next";
defineProps({ modelValue: String, type: { type: String, default: "text" }, placeholder: String, search: Boolean, clearable: Boolean });
defineEmits(["update:modelValue", "clear"]);
</script>
<template>
  <div class="relative">
    <Search v-if="search" :size="16" class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
    <input
      :type="type"
      :value="modelValue"
      :placeholder="placeholder"
      @input="$emit('update:modelValue', $event.target.value)"
      class="h-10 w-full rounded-lg border border-border bg-white text-sm text-ink outline-none transition-colors placeholder:text-faint hover:border-gray-300 focus:border-brand focus:ring-2 focus:ring-brand-wash"
      :class="search ? 'pl-9 pr-9' : 'px-3'"
    />
    <button
      v-if="clearable && modelValue"
      type="button"
      @click="$emit('clear')"
      class="absolute right-2 top-1/2 grid h-6 w-6 -translate-y-1/2 place-items-center rounded text-faint hover:bg-gray-100 hover:text-muted"
    >
      <X :size="14" />
    </button>
  </div>
</template>
```

- [ ] **Step 7: 创建 TextArea.vue**

新建 `src/components/ui/TextArea.vue`：

```vue
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
```

- [ ] **Step 8: 构建验证**

Run: `npm run build`
Expected: 构建成功。
Run: `grep -c "v-card\|v-btn\|v-icon" src/components/ui/*.vue src/lib.js | grep -v ":0" || echo "CLEAN"`
Expected: 输出 `CLEAN`。

- [ ] **Step 9: 提交**

```bash
git add src/lib.js src/components/ui/
git commit -m "feat: add Tailwind ui primitives and semantic tone mappers"
```

---

### Task 3: App 壳（侧栏 + 顶栏 + 主区）

**Files:**
- Rewrite: `src/App.vue`

**Interfaces:**
- Consumes: `navItems`（icon 为 lucide 组件）、`store` 的 `workspace/loadAll/runScan`、路由 `route.meta.full`、`styles.css` 令牌。
- Produces: 应用壳：`fixed` 侧栏（260px，可折叠 88px；移动端抽屉 + 遮罩）、`sticky` 顶栏（菜单钮/面包屑/系统状态/重新扫描）、主区（普通页 max-w-1536 内边距；`meta.full` 全幅）、错误 toast。路由视图经 `router-view` + 轻量过渡渲染。

- [ ] **Step 1: 重写 App.vue**

将 `src/App.vue` 整体替换为：

```vue
<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { AlertCircle, Menu, Radar } from "lucide-vue-next";
import { navItems } from "./router";
import { loadAll, runScan, workspace } from "./store";

const route = useRoute();
const router = useRouter();
const mobileNav = ref(false);
const expanded = ref(true);
const isMobile = () => (typeof window !== "undefined" ? window.innerWidth < 768 : false);

const pageTitle = computed(() => (route.name === "case" ? "案件工作台" : route.meta.title || "工作台"));
const crumb = computed(() => (route.name === "case" ? "案件队列" : "工作台"));
const expandedState = computed(() => !isMobile() && expanded.value);
const toastVisible = ref(false);

function isActive(path) {
  if (path === "/cases") return route.path.startsWith("/cases");
  return route.path === path;
}
function navigate(path) {
  router.push(path);
  if (isMobile()) mobileNav.value = false;
}
function toggleNavigation() {
  if (!isMobile()) expanded.value = !expanded.value;
  else mobileNav.value = !mobileNav.value;
}
watch(
  () => route.fullPath,
  () => {
    if (isMobile()) mobileNav.value = false;
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
      :class="[expandedState ? 'w-[260px]' : 'w-[88px]', mobileNav ? 'translate-x-0' : '-translate-x-full']"
    >
      <div class="flex h-[72px] items-center gap-3 px-5" :class="{ 'justify-center px-3': !expandedState }">
        <span class="grid h-9 w-9 flex-none place-items-center rounded-lg bg-brand" aria-hidden="true">
          <span class="flex items-end gap-[3px]">
            <i class="block w-1 rounded-sm bg-white" style="height: 10px"></i>
            <i class="block w-1 rounded-sm bg-white" style="height: 16px"></i>
            <i class="block w-1 rounded-sm bg-white" style="height: 13px"></i>
          </span>
        </span>
        <div v-show="expandedState" class="leading-tight">
          <strong class="block text-[15px] text-ink">佳华智审</strong>
          <small class="block text-[11px] text-faint">风险调查工作台</small>
        </div>
      </div>

      <span v-show="expandedState" class="px-6 pb-2 pt-4 font-mono text-[10px] font-semibold uppercase tracking-[0.08em] text-faint">工作台</span>

      <nav class="flex-1 space-y-1 overflow-y-auto px-4 py-2">
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
          <component :is="item.icon" :size="18" class="flex-none" :class="{ 'mx-auto': !expandedState }" />
          <span v-show="expandedState">{{ item.label }}</span>
        </button>
      </nav>

      <div v-show="expandedState" class="m-4 rounded-lg border border-border bg-canvas p-3">
        <span class="mb-2 block h-2 w-2 rounded-full bg-success shadow-[0_0_0_4px_#d1fadf]"></span>
        <strong class="block text-xs text-ink">只读调查模式</strong>
        <small class="block text-[11px] text-faint">Agent 不执行自动业务处置</small>
      </div>
    </aside>

    <div class="flex min-h-screen flex-col" :class="expandedState ? 'md:pl-[260px]' : 'md:pl-[88px]'">
      <header class="sticky top-0 z-30 flex h-[72px] items-center gap-4 border-b border-border bg-surface/95 px-4 backdrop-blur md:px-6">
        <button
          type="button"
          class="grid h-10 w-10 flex-none place-items-center rounded-lg border border-border text-muted transition-colors hover:bg-brand-wash hover:text-brand"
          aria-label="切换导航"
          @click="toggleNavigation"
        >
          <Menu :size="20" />
        </button>
        <div class="leading-tight">
          <span class="block text-[11px] text-faint">{{ crumb }}</span>
          <strong class="block text-[15px] text-ink">{{ pageTitle }}</strong>
        </div>
        <div class="flex-1"></div>
        <div class="hidden items-center gap-2 text-xs text-muted sm:flex">
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
```

- [ ] **Step 2: 构建验证**

Run: `npm run build`
Expected: 构建成功。
Run: `grep -c "v-navigation-drawer\|v-app-bar\|v-main\|v-app " src/App.vue | grep -v ":0" || echo "CLEAN"`
Expected: 输出 `CLEAN`。

- [ ] **Step 3: 提交**

```bash
git add src/App.vue
git commit -m "feat: rebuild app shell with Tailwind (sidebar, topbar, main)"
```

---

### Task 4: 风险总览页

**Files:**
- Rewrite: `src/components/RiskOverview.vue`

**Interfaces:**
- Consumes: `workspace.overview/cases/loading`、`lib` 的 `formatMoney/labels/statusColor`、`ui/Badge.vue`、`vue3-apexcharts`。
- Produces: 页面渲染 hero + 4 指标卡 + 优先调查列表 + ApexCharts 圆环 + 构成比例条 + 数据边界注。点击案件跳 `/cases/:caseId`。

- [ ] **Step 1: 重写 RiskOverview.vue**

将 `src/components/RiskOverview.vue` 整体替换为：

```vue
<script setup>
import { computed } from "vue";
import { useRouter } from "vue-router";
import VueApexCharts from "vue3-apexcharts";
import { AlertTriangle, ArrowRight, ClipboardSearch, Clock, DatabaseBackup, Radar, Wallet } from "lucide-vue-next";
import Badge from "./ui/Badge.vue";
import { formatMoney, labels, statusColor } from "../lib";
import { workspace } from "../store";

const router = useRouter();
const overview = computed(() => workspace.overview);
const loading = computed(() => workspace.loading);
const priorityCases = computed(() => workspace.cases.slice(0, 5));

const ar = computed(() => workspace.overview?.cases_by_type?.ACCOUNTS_RECEIVABLE || 0);
const inventory = computed(() => workspace.overview?.cases_by_type?.INVENTORY || 0);
const total = computed(() => ar.value + inventory.value);
const arShare = computed(() => (total.value ? Math.round((ar.value / total.value) * 100) : 0));
const invShare = computed(() => (total.value ? Math.round((inventory.value / total.value) * 100) : 0));

const metrics = computed(() => [
  { label: "关键级案件", value: workspace.overview?.critical_cases ?? "—", note: "当前规则侧重早期预警", tone: "danger", icon: AlertTriangle },
  { label: "等待调查", value: workspace.overview?.open_cases ?? "—", note: "规则已经命中", tone: "brand", icon: ClipboardSearch },
  { label: "等待审核", value: workspace.overview?.pending_review_cases ?? "—", note: "Agent 已完成取证", tone: "warning", icon: Clock },
  { label: "风险敞口", value: workspace.overview ? formatMoney(workspace.overview.exposure_amount) : "—", note: "未关闭案件合计", tone: "success", icon: Wallet, compact: true },
]);
const toneIcon = {
  danger: "bg-danger-wash text-danger",
  brand: "bg-brand-wash text-brand-deep",
  warning: "bg-warning-wash text-warning-deep",
  success: "bg-success-wash text-success-deep",
};

const donutOptions = computed(() => ({
  chart: { type: "donut" },
  labels: ["客户应收", "库存积压"],
  colors: ["#465fff", "#039855"],
  stroke: { width: 0 },
  dataLabels: { enabled: false },
  legend: { show: false },
  plotOptions: {
    pie: {
      donut: {
        size: "78%",
        labels: {
          show: true,
          name: { show: false },
          value: { show: true, fontSize: "26px", fontWeight: 700, color: "#101828" },
          total: { show: true, label: "案件总数", fontSize: "12px", fontWeight: 500, color: "#98a2b3" },
        },
      },
    },
  },
}));
const donutSeries = computed(() => [ar.value, inventory.value]);

const barTone = {
  CRITICAL: "bg-danger",
  HIGH: "bg-warning",
  MEDIUM: "bg-brand",
  LOW: "bg-gray-200",
};

function openCase(caseId) {
  router.push(`/cases/${encodeURIComponent(caseId)}`);
}
function showCases() {
  router.push("/cases");
}
</script>

<template>
  <div class="space-y-5">
    <section class="card flex items-center justify-between gap-6 border-brand/20 bg-brand-wash/50 px-6 py-5">
      <div>
        <span class="eyebrow inline-flex items-center gap-1.5"><Radar :size="13" /> 规则发现 · Agent 调查 · 人工审核</span>
        <h2 class="mt-2 text-[22px] font-bold text-ink">风险调查态势</h2>
        <p v-if="overview?.latest_run" class="mt-1 text-xs text-muted">
          规则集 {{ overview.latest_run.rule_set_version }} · 观察期 {{ overview.latest_run.observation_date }} · 命中 {{ overview.latest_run.rule_hits }} 条规则
        </p>
        <p v-else class="mt-1 text-xs text-muted">尚未执行规则扫描，请点击右上角"重新扫描"。</p>
      </div>
      <div class="min-w-[130px] border-l border-brand/20 pl-5">
        <strong class="block text-4xl leading-none text-brand">{{ overview?.total_cases ?? "—" }}</strong>
        <span class="mt-1 block text-[11px] text-muted">当前风险案件</span>
      </div>
    </section>

    <div class="grid grid-cols-2 gap-4 xl:grid-cols-4">
      <section v-for="m in metrics" :key="m.label" class="card min-h-[148px] p-5">
        <span class="mb-4 grid h-10 w-10 place-items-center rounded-lg" :class="toneIcon[m.tone]">
          <component :is="m.icon" :size="20" />
        </span>
        <span class="block text-xs text-muted">{{ m.label }}</span>
        <strong class="mt-1 block leading-tight text-ink" :class="m.compact ? 'text-[19px]' : 'text-[25px]'">{{ m.value }}</strong>
        <small class="mt-1 block text-[11px] text-faint">{{ m.note }}</small>
      </section>
    </div>

    <div class="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.45fr)_minmax(340px,0.75fr)]">
      <section class="card">
        <div class="panel-head">
          <div class="flex items-center gap-2"><span class="section-index">A</span><h3>优先调查</h3></div>
          <button type="button" @click="showCases" class="inline-flex items-center gap-1 text-sm font-semibold text-brand hover:text-brand-dark">
            查看全部 <ArrowRight :size="15" />
          </button>
        </div>
        <div class="px-2.5 py-2">
          <button
            v-for="item in priorityCases"
            :key="item.case_id"
            type="button"
            @click="openCase(item.case_id)"
            class="grid w-full grid-cols-[3px_minmax(0,1fr)_auto] items-center gap-3 rounded-md px-2 py-3 text-left transition-colors hover:bg-canvas"
          >
            <span class="h-full w-[3px] rounded" :class="barTone[item.priority] || 'bg-gray-200'"></span>
            <span>
              <strong class="block text-[13px] text-ink">{{ item.entity_label }}</strong>
              <small class="mt-0.5 block max-w-[650px] truncate text-xs text-muted">{{ item.summary }}</small>
            </span>
            <span class="text-right">
              <strong class="block text-[13px] text-ink">{{ formatMoney(item.exposure_amount) }}</strong>
              <Badge class="mt-1" :tone="statusColor(item.status)">{{ labels.status[item.status] }}</Badge>
            </span>
          </button>
          <div v-if="!loading && !priorityCases.length" class="empty-state">尚无风险案件</div>
        </div>
      </section>

      <section class="card pb-4">
        <div class="panel-head"><div class="flex items-center gap-2"><span class="section-index">B</span><h3>案件构成</h3></div></div>
        <div class="px-5 pt-3">
          <VueApexCharts type="donut" height="210" :options="donutOptions" :series="donutSeries" />
        </div>
        <div class="space-y-4 px-5 pt-1">
          <div>
            <div class="mb-2 flex justify-between text-[13px]">
              <span class="flex items-center gap-2 text-muted"><i class="h-2.5 w-2.5 rounded-sm bg-brand"></i>客户应收调查</span>
              <strong class="text-ink">{{ ar }} 件</strong>
            </div>
            <div class="h-2 rounded-full bg-gray-100"><div class="h-2 rounded-full bg-brand" :style="{ width: arShare + '%' }"></div></div>
          </div>
          <div>
            <div class="mb-2 flex justify-between text-[13px]">
              <span class="flex items-center gap-2 text-muted"><i class="h-2.5 w-2.5 rounded-sm bg-success"></i>库存积压调查</span>
              <strong class="text-ink">{{ inventory }} 件</strong>
            </div>
            <div class="h-2 rounded-full bg-gray-100"><div class="h-2 rounded-full bg-success" :style="{ width: invShare + '%' }"></div></div>
          </div>
        </div>
        <div class="m-4 mt-5 rounded-lg bg-canvas p-3.5">
          <div class="flex gap-2.5">
            <DatabaseBackup :size="18" class="mt-0.5 flex-none text-muted" />
            <div>
              <strong class="text-xs text-ink">数据边界</strong>
              <p class="mt-0.5 text-[11px] leading-5 text-muted">库存为公司仓库库存；当前数据不包含促销、下游库存、银行未核销和项目验收记录。</p>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
```

- [ ] **Step 2: 构建验证**

Run: `npm run build`
Expected: 构建成功。
Run: `grep -c "v-card\|v-progress\|v-icon\|v-chip" src/components/RiskOverview.vue | grep -v ":0" || echo "CLEAN"`
Expected: 输出 `CLEAN`。

- [ ] **Step 3: 提交**

```bash
git add src/components/RiskOverview.vue
git commit -m "feat: rebuild risk overview with Tailwind and ApexCharts donut"
```

---

### Task 5: 案件队列页

**Files:**
- Rewrite: `src/components/CaseQueue.vue`

**Interfaces:**
- Consumes: `workspace.cases/loading`、`lib` 的 `formatMoney/labels/priorityColor/statusColor`、`ui/Badge/SelectInput/TextInput`。
- Produces: 类型/状态筛选 + 搜索 + 计数 + 表格；点击行跳 `/cases/:caseId`。

- [ ] **Step 1: 重写 CaseQueue.vue**

将 `src/components/CaseQueue.vue` 整体替换为：

```vue
<script setup>
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { ChevronRight } from "lucide-vue-next";
import Badge from "./ui/Badge.vue";
import SelectInput from "./ui/SelectInput.vue";
import TextInput from "./ui/TextInput.vue";
import { formatMoney, labels, priorityColor, statusColor } from "../lib";
import { workspace } from "../store";

const router = useRouter();
const type = ref("");
const status = ref("");
const query = ref("");
const typeOptions = [
  { title: "全部类型", value: "" },
  { title: "客户应收", value: "ACCOUNTS_RECEIVABLE" },
  { title: "库存积压", value: "INVENTORY" },
];
const statusOptions = [{ title: "全部状态", value: "" }, ...Object.entries(labels.status).map(([value, title]) => ({ title, value }))];
const filtered = computed(() => {
  const keyword = String(query.value ?? "").trim().toLocaleLowerCase();
  return workspace.cases.filter((item) => {
    const matchesFilters = (!type.value || item.case_type === type.value) && (!status.value || item.status === status.value);
    if (!matchesFilters || !keyword) return matchesFilters;
    const searchable = [item.case_id, item.entity_label, item.summary, labels.caseType[item.case_type], labels.status[item.status]]
      .filter(Boolean)
      .join(" ")
      .toLocaleLowerCase();
    return searchable.includes(keyword);
  });
});

function openCase(caseId) {
  router.push(`/cases/${encodeURIComponent(caseId)}`);
}
</script>

<template>
  <div class="space-y-5">
    <div class="section-intro flex items-end justify-between gap-6">
      <div><span class="eyebrow">CASE QUEUE</span><h2>风险案件队列</h2></div>
      <p>规则命中是调查入口；优先级用于排队，不代表自动业务定性。</p>
    </div>

    <section class="card overflow-hidden">
      <div class="flex flex-wrap items-center gap-3 border-b border-border px-5 py-4">
        <SelectInput v-model="type" :options="typeOptions" class="w-[180px]" />
        <SelectInput v-model="status" :options="statusOptions" class="w-[180px]" />
        <TextInput v-model="query" search clearable class="w-[320px] max-w-full" placeholder="搜索案件、客户或物料" @clear="query = ''" />
        <span class="ml-auto text-xs text-muted">共 {{ filtered.length }} 个案件</span>
      </div>

      <div class="overflow-x-auto">
        <table class="table-base min-w-[1050px]">
          <thead>
            <tr><th>优先级</th><th>案件主体</th><th>触发摘要</th><th>风险敞口</th><th>状态</th><th>观察期</th><th></th></tr>
          </thead>
          <tbody>
            <tr
              v-for="item in filtered"
              :key="item.case_id"
              tabindex="0"
              @click="openCase(item.case_id)"
              @keydown.enter="openCase(item.case_id)"
            >
              <td><Badge :tone="priorityColor(item.priority)">{{ labels.priority[item.priority] }}</Badge></td>
              <td>
                <strong class="block text-[13px] text-ink">{{ item.entity_label }}</strong>
                <small class="block text-xs text-muted">{{ labels.caseType[item.case_type] }}</small>
              </td>
              <td class="text-xs leading-[1.55] text-muted">{{ item.summary }}</td>
              <td class="money-cell">{{ formatMoney(item.exposure_amount) }}</td>
              <td><Badge :tone="statusColor(item.status)">{{ labels.status[item.status] }}</Badge></td>
              <td class="text-muted">{{ item.observation_date }}</td>
              <td><ChevronRight :size="16" class="text-faint" /></td>
            </tr>
            <tr v-if="!workspace.loading && !filtered.length"><td colspan="7" class="empty-state">当前筛选条件下没有案件</td></tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>
```

- [ ] **Step 2: 构建验证**

Run: `npm run build`
Expected: 构建成功。
Run: `grep -c "v-select\|v-text-field\|v-table" src/components/CaseQueue.vue | grep -v ":0" || echo "CLEAN"`
Expected: 输出 `CLEAN`。

- [ ] **Step 3: 提交**

```bash
git add src/components/CaseQueue.vue
git commit -m "feat: rebuild case queue with Tailwind table and native selects"
```

---

### Task 6: 案件工作台页

**Files:**
- Rewrite: `src/components/CaseWorkspace.vue`

**Interfaces:**
- Consumes: `route.params.caseId`、`lib` 的 `api/formatMoney/labels/priorityColor/statusColor`、`store` 的 `loadRiskData`、`ui/Badge/Button/SelectInput/Tabs/TextArea/TextInput`、`InvestigationThread`（Task 7 前仍可存在，模板引用不变）。
- Produces: 头部 + 事实条 + 3 Tab（Agent 调查 / 规则信号 / 人工审核）+ 审核表单与历史 + 加载/错误态。

- [ ] **Step 1: 重写 CaseWorkspace.vue**

将 `src/components/CaseWorkspace.vue` 整体替换为：

```vue
<script setup>
import { computed, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { AlertCircle, ArrowLeft, CalendarDays, CodeXml, LoaderCircle, Radar, Sparkles, UserCheck, Wallet } from "lucide-vue-next";
import Badge from "./ui/Badge.vue";
import Button from "./ui/Button.vue";
import SelectInput from "./ui/SelectInput.vue";
import Tabs from "./ui/Tabs.vue";
import TextArea from "./ui/TextArea.vue";
import TextInput from "./ui/TextInput.vue";
import InvestigationThread from "./InvestigationThread.vue";
import { api, formatMoney, labels, priorityColor, statusColor } from "../lib";
import { loadRiskData } from "../store";

const route = useRoute();
const router = useRouter();
const caseItem = ref(null);
const loading = ref(true);
const error = ref("");
const tab = ref("investigation");
const submitting = ref(false);
const form = reactive({ decision: "", reviewer: "", reason: "", action: "", next_review_at: "" });
const decisionOptions = [
  { title: "暂时接受，持续观察", value: "MONITOR" },
  { title: "风险成立，需要处置", value: "ACTION_REQUIRED" },
  { title: "确认误报或数据问题", value: "FALSE_POSITIVE" },
  { title: "风险已经解决", value: "RESOLVED" },
];
const canSubmit = computed(() => form.decision && form.reviewer.trim() && form.reason.trim().length >= 2 && (form.decision !== "MONITOR" || form.next_review_at));
const tabs = [
  { value: "investigation", label: "Agent 调查", icon: Sparkles },
  { value: "signals", label: "规则信号", icon: Radar },
  { value: "review", label: "人工审核", icon: UserCheck },
];
const facts = computed(() =>
  caseItem.value
    ? [
        { icon: Wallet, tone: "text-brand-deep bg-brand-wash", label: "风险敞口", value: formatMoney(caseItem.value.exposure_amount) },
        { icon: CalendarDays, tone: "text-muted bg-gray-100", label: "观察日期", value: caseItem.value.observation_date },
        { icon: CodeXml, tone: "text-muted bg-gray-100", label: "规则版本", value: caseItem.value.rule_set_version },
        { icon: Radar, tone: "text-warning-deep bg-warning-wash", label: "规则命中", value: `${caseItem.value.rule_hits.length} 条` },
      ]
    : []
);

async function loadCase(id) {
  loading.value = true;
  error.value = "";
  try {
    caseItem.value = await api(`/api/v1/cases/${encodeURIComponent(id)}`);
  } catch (exception) {
    error.value = exception.message;
    caseItem.value = null;
  } finally {
    loading.value = false;
  }
}

watch(
  () => route.params.caseId,
  (id) => {
    tab.value = "investigation";
    Object.assign(form, { decision: "", reviewer: "", reason: "", action: "", next_review_at: "" });
    if (id) loadCase(id);
  },
  { immediate: true }
);

async function refresh() {
  if (!route.params.caseId) return;
  await Promise.all([loadCase(route.params.caseId), loadRiskData()]);
}

async function submitReview() {
  if (!caseItem.value || !canSubmit.value) return;
  submitting.value = true;
  try {
    await api(`/api/v1/cases/${encodeURIComponent(caseItem.value.case_id)}/reviews`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        decision: form.decision,
        reviewer: form.reviewer.trim(),
        reason: form.reason.trim(),
        action: form.action.trim() || null,
        next_review_at: form.decision === "MONITOR" ? form.next_review_at : null,
      }),
    });
    Object.assign(form, { decision: "", reason: "", action: "", next_review_at: "" });
    await refresh();
  } finally {
    submitting.value = false;
  }
}

function reviewLabel(decision) {
  return ({ MONITOR: "持续观察", ACTION_REQUIRED: "需要处置", FALSE_POSITIVE: "确认误报", RESOLVED: "已经解决" })[decision] || decision;
}
</script>

<template>
  <div class="min-h-[calc(100vh-72px)]">
    <header class="flex items-center gap-3 border-b border-border bg-surface px-4 py-3 md:px-5">
      <button
        type="button"
        class="grid h-10 w-10 flex-none place-items-center rounded-lg border border-border text-muted transition-colors hover:bg-canvas"
        aria-label="返回案件队列"
        @click="router.push('/cases')"
      >
        <ArrowLeft :size="18" />
      </button>
      <div class="leading-tight">
        <span v-if="caseItem" class="block font-mono text-[10px] font-medium uppercase tracking-wide text-brand-deep">{{ labels.caseType[caseItem.case_type] }} · {{ caseItem.case_id }}</span>
        <span v-else class="block font-mono text-[10px] font-medium uppercase tracking-wide text-faint">CASE WORKSPACE</span>
        <h2 class="text-xl font-bold text-ink">{{ caseItem ? caseItem.entity_label : "案件工作台" }}</h2>
      </div>
      <div class="flex-1"></div>
      <template v-if="caseItem">
        <Badge :tone="priorityColor(caseItem.priority)">{{ labels.priority[caseItem.priority] }}优先级</Badge>
        <Badge :tone="statusColor(caseItem.status)">{{ labels.status[caseItem.status] }}</Badge>
      </template>
    </header>

    <div v-if="loading" class="grid min-h-[50vh] place-content-center justify-items-center gap-3 text-muted">
      <LoaderCircle :size="28" class="animate-spin text-brand" />
      <span class="text-sm">正在装载案件、证据和审核记录</span>
    </div>

    <div v-else-if="error" class="grid min-h-[50vh] place-content-center justify-items-center gap-3 text-center">
      <AlertCircle :size="42" class="text-danger" />
      <h3 class="text-lg font-bold text-ink">案件加载失败</h3>
      <p class="text-sm text-muted">{{ error }}</p>
      <Button @click="router.push('/cases')"><ArrowLeft :size="16" /> 返回案件队列</Button>
    </div>

    <template v-else-if="caseItem">
      <div class="grid grid-cols-2 gap-px border-b border-border bg-border md:grid-cols-4">
        <div v-for="f in facts" :key="f.label" class="flex items-center gap-3 bg-surface px-5 py-4">
          <span class="grid h-9 w-9 flex-none place-items-center rounded-lg" :class="f.tone"><component :is="f.icon" :size="16" /></span>
          <div>
            <span class="block text-[11px] text-muted">{{ f.label }}</span>
            <strong class="block text-sm text-ink">{{ f.value }}</strong>
          </div>
        </div>
      </div>

      <Tabs v-model="tab" :tabs="tabs" />

      <div v-if="tab === 'investigation'" class="bg-surface">
        <InvestigationThread :case-item="caseItem" @completed="refresh" />
      </div>

      <div v-else-if="tab === 'signals'" class="mx-auto w-full max-w-[1200px] px-5 py-6">
        <div class="section-intro mb-5">
          <div><span class="eyebrow">RULE SIGNALS</span><h2>规则触发</h2></div>
          <p>{{ caseItem.summary }}</p>
        </div>
        <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
          <section v-for="hit in caseItem.rule_hits" :key="hit.rule_hit_id" class="card p-4">
            <div class="flex items-center justify-between">
              <span class="font-mono text-[10px] font-semibold text-brand-deep">{{ hit.rule_id }}</span>
              <Badge :tone="priorityColor(hit.severity)">{{ labels.priority[hit.severity] }}</Badge>
            </div>
            <h3 class="mt-2 text-[15px] font-semibold text-ink">{{ hit.rule_name }}</h3>
            <p class="mt-1 text-[13px] leading-6 text-muted">{{ hit.reason }}</p>
            <small class="mt-2 block text-xs text-faint">{{ hit.sources.join(" / ") }} · {{ hit.period }}</small>
          </section>
        </div>
      </div>

      <div v-else class="mx-auto grid w-full max-w-[1100px] grid-cols-1 gap-6 px-5 py-6 md:grid-cols-[380px_1fr]">
        <section class="card h-fit p-5">
          <span class="eyebrow">HUMAN REVIEW</span>
          <h2 class="mt-1 text-xl font-bold text-ink">提交审核决定</h2>
          <div class="mt-4 space-y-4">
            <SelectInput v-model="form.decision" :options="decisionOptions" />
            <TextInput v-model="form.reviewer" maxlength="100" placeholder="审核人" />
            <TextArea v-model="form.reason" rows="3" maxlength="1000" placeholder="审核原因" />
            <TextInput v-model="form.action" maxlength="1000" placeholder="后续动作（可选）" />
            <div v-if="form.decision === 'MONITOR'">
              <span class="mb-1.5 block text-sm font-medium text-ink">复查日期</span>
              <TextInput v-model="form.next_review_at" type="date" />
            </div>
            <Button block :disabled="!canSubmit" :loading="submitting" @click="submitReview">提交人工审核</Button>
          </div>
        </section>

        <section>
          <div class="section-intro mb-4">
            <div><span class="eyebrow">AUDIT TRAIL</span><h2>审核历史</h2></div>
          </div>
          <div class="space-y-3">
            <article v-for="review in caseItem.reviews" :key="review.review_id" class="card p-4">
              <div class="flex items-center gap-2">
                <strong class="text-sm text-ink">{{ review.reviewer }}</strong>
                <Badge tone="neutral">{{ reviewLabel(review.decision) }}</Badge>
                <span class="ml-auto text-[10px] text-faint">{{ review.created_at }}</span>
              </div>
              <p class="mt-2 text-sm leading-6 text-muted">{{ review.reason }}</p>
              <small v-if="review.action" class="mt-1 block text-xs text-muted">后续动作：{{ review.action }}</small>
            </article>
            <div v-if="!caseItem.reviews.length" class="card empty-state">还没有人工审核记录</div>
          </div>
        </section>
      </div>
    </template>
  </div>
</template>
```

- [ ] **Step 2: 构建验证**

Run: `npm run build`
Expected: 构建成功。
Run: `grep -c "v-tabs\|v-window\|v-select\|v-text-field\|v-textarea\|v-btn\|v-card" src/components/CaseWorkspace.vue | grep -v ":0" || echo "CLEAN"`
Expected: 输出 `CLEAN`。

- [ ] **Step 3: 提交**

```bash
git add src/components/CaseWorkspace.vue
git commit -m "feat: rebuild case workspace with Tailwind tabs and review form"
```

---

### Task 7: 调查流组件

**Files:**
- Rewrite: `src/components/InvestigationThread.vue`

**Interfaces:**
- Consumes: props `caseItem`、emit `completed`、`lib` 的 `hypothesisColor/labels/priorityColor/queryArguments/stageColor/streamNdjson`、`ui/Badge`。
- Produces: 事件时间线（NDJSON 流）、打字指示、最终报告（结论卡/风险判断/事实+引用/假设/建议+限制/证据与轨迹折叠面板）、错误提示。用原生 `<details>` 实现折叠面板。

- [ ] **Step 1: 重写 InvestigationThread.vue**

将 `src/components/InvestigationThread.vue` 整体替换为：

```vue
<script setup>
import { nextTick, ref, watch } from "vue";
import { AlertCircle, CheckCircle2, Database, PlayCircle, Search, ShieldCheck, Sparkles, UserCheck } from "lucide-vue-next";
import Badge from "./ui/Badge.vue";
import { hypothesisColor, labels, priorityColor, queryArguments, stageColor, streamNdjson } from "../lib";

const props = defineProps({ caseItem: Object });
const emit = defineEmits(["completed"]);
const running = ref(false);
const events = ref([]);
const record = ref(props.caseItem?.latest_investigation || null);
const error = ref("");
const thread = ref(null);

watch(
  () => props.caseItem,
  (value) => {
    record.value = value?.latest_investigation || null;
    events.value = [];
    error.value = "";
  },
  { deep: false }
);

async function scrollToBottom() {
  await nextTick();
  if (thread.value) thread.value.scrollTo({ top: thread.value.scrollHeight, behavior: "smooth" });
}

async function investigate() {
  if (!props.caseItem || running.value) return;
  running.value = true;
  events.value = [];
  record.value = null;
  error.value = "";
  let finalRecord = null;
  let terminalError = "";
  try {
    await streamNdjson(`/api/v1/cases/${encodeURIComponent(props.caseItem.case_id)}/investigations`, { method: "POST" }, (event) => {
      events.value.push(event);
      if (event.event_type === "REPORT_COMPLETED" && event.record) {
        finalRecord = event.record;
        record.value = event.record;
      }
      if (event.event_type === "ERROR") terminalError = event.message;
      void scrollToBottom();
    });
    if (!finalRecord) throw new Error(terminalError || "调查事件流结束，但没有生成可保存的报告。");
    emit("completed");
  } catch (exception) {
    error.value = exception.message;
  } finally {
    running.value = false;
    void scrollToBottom();
  }
}

const eventIcon = {
  RUN_STARTED: PlayCircle,
  TOOL_STARTED: Search,
  TOOL_COMPLETED: Database,
  VALIDATION_STARTED: ShieldCheck,
  REPORT_COMPLETED: CheckCircle2,
  ERROR: AlertCircle,
};
const eventTone = {
  RUN_STARTED: "text-muted",
  TOOL_STARTED: "text-muted",
  TOOL_COMPLETED: "text-brand",
  VALIDATION_STARTED: "text-muted",
  REPORT_COMPLETED: "text-success",
  ERROR: "text-danger",
};
function evidenceById(id) {  return record.value?.evidence?.find((item) => item.evidence_id === id);
}
function completeness(value) {
  return ({ LOW: 33, MEDIUM: 66, HIGH: 100 })[value] || 0;
}
</script>

<template>
  <section class="mx-auto flex h-full w-full max-w-[1100px] flex-col">
    <header class="flex items-center gap-4 px-5 py-4">
      <div class="leading-tight">
        <span class="eyebrow">AGENT INVESTIGATION</span>
        <h3 class="mt-0.5 text-[16px] font-bold text-ink">调查对话</h3>
        <p class="text-xs text-muted">按真实执行顺序展示工具与证据，不展示私有思维链。</p>
      </div>
      <div class="flex-1"></div>
      <button
        type="button"
        :disabled="running"
        class="inline-flex h-10 items-center gap-2 rounded-lg bg-brand px-4 text-sm font-semibold text-white transition-colors hover:bg-brand-dark disabled:opacity-60"
        @click="investigate"
      >
        <Sparkles :size="16" :class="running ? 'animate-spin' : ''" />
        {{ record ? "重新调查" : "开始调查" }}
      </button>
    </header>

    <div ref="thread" class="h-[calc(100vh-280px)] min-h-[360px] overflow-y-auto px-4 pb-10 md:px-6" aria-live="polite">
      <div class="mx-auto flex max-w-[880px] flex-col gap-1 border-l border-border pl-6">
        <div class="relative -ml-[31px] my-2 flex items-center gap-2">
          <span class="grid h-8 w-8 flex-none place-items-center rounded-full border border-border bg-surface"><Database :size="14" class="text-muted" /></span>
          <span class="text-[11px] text-muted">只读业务工具 · 无任意 SQL · 结论输出前校验证据引用</span>
        </div>

        <div v-if="!events.length && !record" class="my-10 flex items-start gap-4">
          <span class="grid h-12 w-12 flex-none place-items-center rounded-xl bg-brand-wash text-brand-deep"><Sparkles :size="22" /></span>
          <div>
            <h4 class="text-[15px] font-semibold text-ink">准备从证据开始调查</h4>
            <p class="mt-1 max-w-[520px] text-[13px] leading-6 text-muted">Agent 会先发现当前案件可用数据，再逐步查询、核验证据，最终报告将出现在这条时间线底部。</p>
          </div>
        </div>

        <div v-for="event in events" :key="event.sequence" class="relative -ml-[31px] grid grid-cols-[28px_minmax(0,1fr)] gap-3 py-2">
          <span class="grid h-8 w-8 flex-none place-items-center rounded-full border border-border bg-surface" :class="eventTone[event.event_type]">
            <component :is="eventIcon[event.event_type] || PlayCircle" :size="15" />
          </span>
          <div class="rounded-lg border border-border bg-surface p-3.5">
            <div class="flex items-center gap-2">
              <strong class="text-[13px] text-ink">{{ event.tool_name ? labels.tool[event.tool_name] : labels.event[event.event_type] }}</strong>
              <span class="ml-auto font-mono text-[10px] text-faint">#{{ String(event.sequence).padStart(2, "0") }}</span>
            </div>
            <p class="mt-1 text-[13px] leading-6 text-muted">{{ event.message }}</p>
            <small v-if="event.evidence?.arguments || event.arguments" class="mt-1 block font-mono text-[11px] text-faint">{{ queryArguments(event.evidence || event) }}</small>
            <div v-if="event.evidence" class="mt-2 flex items-start gap-2 rounded-md bg-canvas p-2.5">
              <Database :size="14" class="mt-0.5 flex-none text-muted" />
              <div>
                <strong class="block text-xs text-ink">{{ event.evidence.summary }}</strong>
                <small class="block text-[11px] text-faint">{{ event.evidence.period }} · 证据 {{ event.evidence.evidence_id.slice(0, 8) }}</small>
              </div>
            </div>
          </div>
        </div>

        <div v-if="running" class="relative -ml-[31px] my-3 flex items-center gap-2 text-muted">
          <span class="flex items-end gap-1"><i class="h-2 w-1 animate-bounce rounded bg-faint"></i><i class="h-3 w-1 animate-bounce rounded bg-faint" style="animation-delay: 0.12s"></i><i class="h-2 w-1 animate-bounce rounded bg-faint" style="animation-delay: 0.24s"></i></span>
          <small class="text-xs">Agent 正在继续调查</small>
        </div>

        <div v-if="record" class="relative -ml-[31px] mt-4 space-y-4">
          <div class="flex items-center gap-3">
            <span class="grid h-10 w-10 place-items-center rounded-xl bg-success-wash text-success"><CheckCircle2 :size="20" /></span>
            <div><small class="block font-mono text-[10px] uppercase tracking-wide text-faint">VERIFIED OUTCOME</small><h3 class="text-lg font-bold text-ink">调查报告</h3></div>
          </div>

          <section class="card p-5">
            <div class="flex flex-wrap items-center gap-3">
              <Badge :tone="priorityColor(record.report.recommended_priority)">建议{{ labels.priority[record.report.recommended_priority] }}优先级</Badge>
              <span class="text-xs text-muted">证据完整度 {{ record.report.evidence_completeness }}</span>
            </div>
            <p class="mt-3 text-sm leading-6 text-muted">{{ record.report.investigation_summary }}</p>
            <div class="mt-3 h-1.5 rounded-full bg-gray-100"><div class="h-1.5 rounded-full bg-brand" :style="{ width: completeness(record.report.evidence_completeness) + '%' }"></div></div>
          </section>

          <section class="card p-5">
            <div class="flex items-center gap-2">
              <h4 class="text-[15px] font-bold text-ink">风险信号判断</h4>
              <Badge :tone="stageColor(record.report.risk_assessment.stage)">{{ labels.riskStage[record.report.risk_assessment.stage] }}</Badge>
            </div>
            <p class="mt-2 text-[13px] leading-6 text-muted">{{ record.report.risk_assessment.statement }}</p>
            <div class="mt-4 grid grid-cols-1 gap-4 md:grid-cols-3">
              <div><strong class="text-xs text-ink">主要驱动</strong><ul class="mt-1 space-y-1 text-[13px] text-muted"><li v-for="item in record.report.risk_assessment.drivers" :key="item">· {{ item }}</li></ul></div>
              <div><strong class="text-xs text-ink">反向信号</strong><ul class="mt-1 space-y-1 text-[13px] text-muted"><li v-for="item in record.report.risk_assessment.counter_signals" :key="item">· {{ item }}</li><li v-if="!record.report.risk_assessment.counter_signals.length">· 暂无</li></ul></div>
              <div><strong class="text-xs text-ink">后续监测</strong><ul class="mt-1 space-y-1 text-[13px] text-muted"><li v-for="item in record.report.risk_assessment.watch_items" :key="item">· {{ item }}</li></ul></div>
            </div>
          </section>

          <section class="card p-5">
            <div class="flex items-center justify-between"><h4 class="text-[15px] font-bold text-ink">确定事实</h4><span class="text-xs text-faint">{{ record.report.facts.length }} 项</span></div>
            <div class="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
              <article v-for="fact in record.report.facts" :key="fact.statement" class="rounded-lg border border-border p-3">
                <p class="text-[13px] leading-6 text-muted">{{ fact.statement }}</p>
                <div class="mt-2 flex flex-wrap gap-1.5">
                  <Badge v-for="id in fact.evidence_ids" :key="id" tone="brand">{{ labels.tool[evidenceById(id)?.tool_name] || "证据" }} · {{ evidenceById(id)?.period }}</Badge>
                </div>
              </article>
            </div>
          </section>

          <section class="card p-5">
            <div class="flex items-center justify-between"><h4 class="text-[15px] font-bold text-ink">证据支持的判断</h4><span class="text-xs text-faint">{{ record.report.hypotheses.length }} 项</span></div>
            <div class="mt-3 space-y-3">
              <article v-for="item in record.report.hypotheses" :key="item.hypothesis_id" class="rounded-lg border border-border p-3">
                <div class="flex items-start gap-2"><Badge :tone="hypothesisColor(item.status)">{{ labels.hypothesis[item.status] }}</Badge><strong class="text-[13px] text-ink">{{ item.statement }}</strong></div>
                <p v-if="item.missing_evidence.length" class="mt-1.5 text-[12px] text-warning-deep"><b>仍需补证：</b>{{ item.missing_evidence.join("；") }}</p>
              </article>
            </div>
          </section>

          <div class="grid grid-cols-1 gap-4 md:grid-cols-2">
            <section class="card p-5"><h4 class="text-[15px] font-bold text-ink">建议动作</h4><ul class="mt-2 space-y-1.5 text-[13px] text-muted"><li v-for="item in record.report.recommended_actions" :key="item">· {{ item }}</li></ul></section>
            <section class="card p-5"><h4 class="text-[15px] font-bold text-ink">数据限制</h4><ul class="mt-2 space-y-1.5 text-[13px] text-muted"><li v-for="item in record.report.limitations" :key="item">· {{ item }}</li><li v-if="!record.report.limitations.length">· 未报告额外限制</li></ul></section>
          </div>

          <details class="card">
            <summary class="cursor-pointer select-none px-5 py-4 text-sm font-semibold text-ink">查看本轮 {{ record.evidence.length }} 项工具证据</summary>
            <div class="space-y-3 border-t border-border px-5 py-4">
              <article v-for="(item, index) in record.evidence" :key="item.evidence_id" class="grid grid-cols-[24px_minmax(0,1fr)] gap-3">
                <span class="font-mono text-xs text-faint">{{ String(index + 1).padStart(2, "0") }}</span>
                <div>
                  <div class="flex items-center gap-2"><strong class="text-[13px] text-ink">{{ labels.tool[item.tool_name] }}</strong><small class="text-[11px] text-faint">{{ item.period }} · {{ item.sources.join(" / ") }}</small></div>
                  <p class="mt-1 text-[13px] leading-6 text-muted">{{ item.summary }}</p>
                  <code class="mt-1 block font-mono text-[11px] text-faint">{{ item.evidence_id }}</code>
                </div>
              </article>
            </div>
          </details>

          <details v-if="record.report.trace?.length" class="card">
            <summary class="cursor-pointer select-none px-5 py-4 text-sm font-semibold text-ink">回放调查轨迹</summary>
            <div class="space-y-3 border-t border-border px-5 py-4">
              <article v-for="item in record.report.trace" :key="item.created_at + item.title">
                <div class="flex items-center gap-2"><strong class="text-[13px] text-ink">{{ item.title }}</strong><small class="ml-auto text-[11px] text-faint">{{ item.created_at }}</small></div>
                <p class="mt-1 text-[12px] leading-5 text-muted">{{ item.detail }}</p>
              </article>
            </div>
          </details>

          <div class="flex items-center gap-2 rounded-lg bg-canvas p-3 text-xs text-muted"><UserCheck :size="15" class="flex-none" />Agent 提供调查证据，最终业务处置仍由人工审核决定。</div>
        </div>

        <div v-if="error" class="my-4 flex items-start gap-3 rounded-lg border border-danger/30 bg-danger-wash p-4">
          <AlertCircle :size="18" class="mt-0.5 flex-none text-danger" />
          <div><strong class="block text-sm text-danger">本次调查未生成报告</strong><p class="mt-1 text-[13px] text-danger-deep">{{ error }}</p></div>
        </div>
      </div>
    </div>
  </section>
</template>
```

- [ ] **Step 2: 构建验证**

Run: `npm run build`
Expected: 构建成功。
Run: `grep -c "v-btn\|v-card\|v-chip\|v-progress\|v-icon\|v-alert\|v-expansion" src/components/InvestigationThread.vue | grep -v ":0" || echo "CLEAN"`
Expected: 输出 `CLEAN`。

- [ ] **Step 3: 提交**

```bash
git add src/components/InvestigationThread.vue
git commit -m "feat: rebuild investigation thread with Tailwind timeline and report"
```

---

### Task 8: 经营分析页

**Files:**
- Rewrite: `src/components/BusinessView.vue`

**Interfaces:**
- Consumes: `workspace.business/loading`、`lib` 的 `formatMoney/formatPercent/metricMap`、`vue3-apexcharts`。
- Produces: 4 指标卡 + ApexCharts 应收趋势面积图 + 明细表（保留）。

- [ ] **Step 1: 重写 BusinessView.vue**

将 `src/components/BusinessView.vue` 整体替换为：

```vue
<script setup>
import { computed } from "vue";
import VueApexCharts from "vue3-apexcharts";
import { AlertCircle, CashCheck, ChartLine, Wallet } from "lucide-vue-next";
import { formatMoney, formatPercent, metricMap } from "../lib";
import { workspace } from "../store";

const data = computed(() => workspace.business);
const loading = computed(() => workspace.loading);
const cards = computed(() => {
  if (!workspace.business) return [];
  const overview = metricMap(workspace.business.overview);
  const ar = metricMap(workspace.business.latest_ar);
  return [
    { label: "累计销售额", value: formatMoney(overview["销售额"]), note: "含退货负值", tone: "brand", icon: ChartLine },
    { label: "累计回款额", value: formatMoney(overview["回款额"]), note: "全数据窗口", tone: "success", icon: CashCheck },
    { label: "最新应收余额", value: formatMoney(ar["应收余额"]), note: workspace.business.latest_ar.period, tone: "warning", icon: Wallet },
    { label: "最新超期率", value: formatPercent(ar["超期率"]), note: `超期 ${formatMoney(ar["超期应收"])}`, tone: "danger", icon: AlertCircle },
  ];
});
const toneIcon = {
  brand: "bg-brand-wash text-brand-deep",
  success: "bg-success-wash text-success-deep",
  warning: "bg-warning-wash text-warning-deep",
  danger: "bg-danger-wash text-danger",
};
const trend = computed(() => (workspace.business?.ar_trend?.rows || []).slice(-8).reverse());
const trendCategories = computed(() => trend.value.map((r) => r[0]));
const trendSeries = computed(() => [
  { name: "应收余额", data: trend.value.map((r) => Number(r[1])) },
  { name: "超期应收", data: trend.value.map((r) => Number(r[2])) },
]);
function axisMoney(value) {
  const n = Number(value);
  if (Math.abs(n) >= 100000000) return `${(n / 100000000).toFixed(1)}亿`;
  if (Math.abs(n) >= 10000) return `${(n / 10000).toFixed(0)}万`;
  return String(n);
}
const trendOptions = computed(() => ({
  chart: { type: "area", toolbar: { show: false }, fontFamily: "DM Sans, 'Microsoft YaHei', sans-serif" },
  colors: ["#465fff", "#d92d20"],
  stroke: { curve: "smooth", width: 2 },
  fill: { type: "gradient", gradient: { opacityFrom: 0.15, opacityTo: 0 } },
  dataLabels: { enabled: false },
  xaxis: { categories: trendCategories.value, axisBorder: { show: false }, axisTicks: { show: false }, labels: { style: { colors: "#98a2b3", fontSize: "12px" } } },
  yaxis: { labels: { formatter: axisMoney, style: { colors: "#98a2b3", fontSize: "12px" } } },
  grid: { borderColor: "#e4e7ec", strokeDashArray: 3 },
  legend: { position: "top", horizontalAlign: "right", fontSize: "12px", labels: { colors: "#667085" }, markers: { size: 4 } },
  tooltip: { y: { formatter: (value) => formatMoney(value) } },
}));
</script>

<template>
  <div class="space-y-5">
    <div class="section-intro flex items-end justify-between gap-6">
      <div><span class="eyebrow">BUSINESS FACTS</span><h2>经营分析</h2></div>
      <p>{{ data?.overview?.period || "正在读取" }} · 确定性指标，不消耗模型额度。</p>
    </div>

    <div class="grid grid-cols-2 gap-4 xl:grid-cols-4">
      <section v-for="card in cards" :key="card.label" class="card min-h-[148px] p-5">
        <span class="mb-4 grid h-10 w-10 place-items-center rounded-lg" :class="toneIcon[card.tone]"><component :is="card.icon" :size="20" /></span>
        <span class="block text-xs text-muted">{{ card.label }}</span>
        <strong class="mt-1 block text-[19px] leading-tight text-ink">{{ card.value }}</strong>
        <small class="mt-1 block text-[11px] text-faint">{{ card.note }}</small>
      </section>
    </div>

    <section class="card">
      <div class="panel-head">
        <div class="flex items-center gap-2"><span class="section-index">C</span><h3>最近应收趋势</h3></div>
        <span class="subtle-copy">每个月末独立聚合</span>
      </div>
      <div class="px-5 pt-4">
        <VueApexCharts type="area" height="260" :options="trendOptions" :series="trendSeries" />
      </div>
      <div class="overflow-x-auto border-t border-border">
        <table class="table-base">
          <thead><tr><th>期间</th><th>应收余额</th><th>超期应收</th><th>超期率</th></tr></thead>
          <tbody>
            <tr v-for="row in trend" :key="row[0]">
              <td><span class="font-mono text-xs text-muted">{{ row[0] }}</span></td>
              <td class="money-cell">{{ formatMoney(row[1]) }}</td>
              <td class="money-cell">{{ formatMoney(row[2]) }}</td>
              <td>
                <span class="inline-flex rounded-md px-2 py-0.5 text-xs font-semibold" :class="Number(row[3]) > 0.3 ? 'bg-danger-wash text-danger' : 'bg-success-wash text-success-deep'">{{ formatPercent(row[3]) }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>
```

- [ ] **Step 2: 构建验证**

Run: `npm run build`
Expected: 构建成功。
Run: `grep -c "v-card\|v-table\|v-icon" src/components/BusinessView.vue | grep -v ":0" || echo "CLEAN"`
Expected: 输出 `CLEAN`。

- [ ] **Step 3: 提交**

```bash
git add src/components/BusinessView.vue
git commit -m "feat: rebuild business view with Tailwind and ApexCharts trend"
```

---

### Task 9: 集成验收

**Files:**
- None（只读验证，除非发现问题需修）。

**Interfaces:**
- Consumes: 全部已完成页面 + 后端运行中的 dist。

- [ ] **Step 1: 全量构建 + 残留扫描**

Run:
```bash
cd "D:\作业\aaachagent\ict-agent-fresh\frontend" && npm run build
grep -rn "from \"vuetify\"\|createVuetify\|@mdi\|v-icon\|v-card\|v-app\|v-btn\|v-select\|v-table\|v-tabs\|v-window\|v-progress\|v-chip\|v-alert\|v-navigation-drawer\|v-app-bar" src/ || echo "ALL CLEAN"
```
Expected: 构建成功，输出 `ALL CLEAN`。

- [ ] **Step 2: 启动后端并截图**

在项目根目录后台启动后端：
```bash
cd "D:\作业\aaachagent\ict-agent-fresh" && .venv/Scripts/python.exe -m uvicorn ict_agent.api:app --app-dir backend/src --host 127.0.0.1 --port 8000
```
等待 3 秒后，用无头 Chrome 对 4 个路由截图（桌面 1440×900、移动 390×844）：
```
http://127.0.0.1:8000/risk
http://127.0.0.1:8000/cases
http://127.0.0.1:8000/business
http://127.0.0.1:8000/cases/AR|C058
```
用 `--headless=new --screenshot=... --window-size=...` 分别截图保存到 `artifacts/`，逐一 Read 检查：页面是否渲染、无白屏、无布局溢出、侧栏/顶栏/卡片/图表正确。

- [ ] **Step 3: 功能冒烟（不消耗模型额度）**

- 侧栏导航 3 项切换、折叠按钮、面包屑、系统状态、重新扫描按钮（POST `/api/v1/rule-runs`，会重新跑规则，不调用模型）。
- 案件队列：类型/状态筛选、搜索关键字、计数变化、行点击进入详情。
- 案件工作台：Tab 切换（Agent 调查/规则信号/人工审核）、规则信号卡渲染、审核表单必填校验（空表单按钮禁用）、MONITOR 出现复查日期输入。
- 经营分析：趋势图与明细表渲染。

- [ ] **Step 4: 真实调查流验证（消耗模型额度，需用户确认）**

选择 1 个案件（如 `AR|C058`），点击"开始调查"，确认：事件流逐条追加、打字指示、最终报告各节渲染、证据面板可展开。若用户不希望在验收阶段消耗额度，则跳过此步并改用已有 `latest_investigation` 渲染检查。

- [ ] **Step 5: 修复 + 复核 + 提交**

对 Step 2-4 发现的问题逐条修复后重新 build 并复测。全部通过后提交剩余改动（若有）：
```bash
git add -A && git commit -m "feat: finalize Tailwind UI migration acceptance"
```

- [ ] **Step 6: 收尾报告**

停止后端；向用户报告：改造完成的页面、视觉验证结论、功能验证结论、遗留问题（如有）、以及"调查流真实调用消耗 DeepSeek 额度"的说明。

---

## Self-Review

- **Spec 覆盖**：技术栈切换（Task 1）✓；ui 基础件 + 色值（Task 2）✓；App 壳侧栏/顶栏/主区（Task 3）✓；风险总览含 ApexCharts 圆环（Task 4）✓；案件队列筛选表格（Task 5）✓；案件工作台 3 Tab + 审核表单/历史（Task 6）✓；调查流时间线 + 报告（Task 7）✓；经营分析 ApexCharts 趋势 + 保留明细表（Task 8）✓；集成验收截图/冒烟/真实调查（Task 9）✓。无缺漏。
- **占位符**：无 TBD/TODO；每个代码步骤给出完整代码与命令。
- **类型一致性**：`priorityColor/statusColor/stageColor/hypothesisColor` 返回 tone 名与 `Badge` 的 `tone` prop 对齐；`ui/` 组件 props/emits 全计划一致；lucide 图标名统一（`AlertCircle/LoaderCircle/Sparkles/UserCheck/CalendarDays/CodeXml/Database/PlayCircle/Search/ShieldCheck/CheckCircle2` 等）。`BusinessView` 用 `Card` 元素类而非组件，与 RiskOverview 的 metric 卡一致。
