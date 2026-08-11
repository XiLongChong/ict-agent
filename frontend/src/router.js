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

const router = createRouter({
  history: createWebHistory("/"),
  routes: [
    { path: "/", redirect: "/risk" },
    { path: "/risk", name: "risk", component: RiskOverview, meta: { title: "风险总览" } },
    { path: "/cases", name: "cases", component: CaseQueue, meta: { title: "案件队列" } },
    { path: "/cases/:caseId", name: "case", component: CaseWorkspace, meta: { title: "案件工作台", full: true } },
    { path: "/business", name: "business", component: BusinessView, meta: { title: "经营分析" } },
    { path: "/:pathMatch(.*)*", redirect: "/risk" },
  ],
  scrollBehavior() {
    return { top: 0 };
  },
});

router.afterEach((to) => {
  document.title = `${to.meta.title || "工作台"} · 佳华智审风险调查工作台`;
});

export default router;
