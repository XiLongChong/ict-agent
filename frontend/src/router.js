import { createRouter, createWebHistory } from "vue-router";
import { Activity, ChartLine, FolderKanban, LayoutDashboard, ListChecks, ListTodo, Newspaper } from "lucide-vue-next";
import RiskOverview from "./components/RiskOverview.vue";
import HealthScores from "./components/HealthScores.vue";
import ListManagement from "./components/ListManagement.vue";
import Sentiments from "./components/Sentiments.vue";
import Projects from "./components/Projects.vue";
import CaseQueue from "./components/CaseQueue.vue";
import BusinessView from "./components/BusinessView.vue";
import CaseWorkspace from "./components/CaseWorkspace.vue";

export const navItems = [
  { path: "/risk", label: "风险预警", icon: LayoutDashboard },
  { path: "/health", label: "健康度", icon: Activity },
  { path: "/lists", label: "名单管理", icon: ListChecks },
  { path: "/sentiments", label: "舆情监控", icon: Newspaper },
  { path: "/projects", label: "项目评估", icon: FolderKanban },
  { path: "/cases", label: "案件队列", icon: ListTodo },
  { path: "/business", label: "经营分析", icon: ChartLine },
];

const router = createRouter({
  history: createWebHistory("/"),
  routes: [
    { path: "/", redirect: "/risk" },
    { path: "/risk", name: "risk", component: RiskOverview, meta: { title: "风险预警" } },
    { path: "/health", name: "health", component: HealthScores, meta: { title: "健康度" } },
    { path: "/lists", name: "lists", component: ListManagement, meta: { title: "名单管理" } },
    { path: "/sentiments", name: "sentiments", component: Sentiments, meta: { title: "舆情监控" } },
    { path: "/projects", name: "projects", component: Projects, meta: { title: "项目评估" } },
    { path: "/cases", name: "cases", component: CaseQueue, meta: { title: "案件队列" } },
    { path: "/cases/:caseId", name: "case", component: CaseWorkspace, meta: { title: "案件处理", standalone: true } },
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
