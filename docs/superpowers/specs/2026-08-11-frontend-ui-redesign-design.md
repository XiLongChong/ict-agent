# 前端 UI 改造设计：Vuetify → TailAdmin（Tailwind）风格

> 日期：2026-08-11 · 分支：`feature/tailadmin-ui` · 状态：已获用户确认

## 背景与目标

当前 `frontend/` 是 Vuetify 3 + Vue 3 + Vite 的风险调查工作台。上一提交 `a917454` 已用 CSS 覆盖
把 TailAdmin 的配色/排版皮肤化，但组件底子仍是 Material（Vuetify），个别地方仍有"AI 味"。

**用户目标**：以 `d:\新建文件夹\vue-tailadmin-admin-dashboard-main`（TailAdmin Vue，Tailwind CSS 4）
为视觉基准，彻底去掉 Vuetify，重建为更美观、不像通用 AI 生成界面的专业风控工作台。

**已确认的两个决策**：
1. **彻底迁移到 Tailwind CSS 4**（去掉 Vuetify，用模板真实设计系统重建全部页面）。
2. **引入 ApexCharts**（vue3-apexcharts）做数据可视化（圆环图 + 趋势折线图）。

## 技术栈

| 项 | 现状 | 迁移后 |
|---|---|---|
| UI 样式 | Vuetify 3 | Tailwind CSS 4（`@tailwindcss/postcss`） |
| 图标 | @mdi/font | `lucide-vue-next` |
| 图表 | CSS 手绘圆环 / v-progress | `apexcharts` + `vue3-apexcharts` |
| 语言 | 纯 JS | 纯 JS（不转 TS） |
| 路由 / 状态 / API | vue-router + 自建 store + lib | 不变 |
| 后端 | FastAPI 同源提供 dist | 不变 |

**依赖变更**：
- 新增：`tailwindcss@^4`、`@tailwindcss/postcss`、`postcss`、`lucide-vue-next`、`apexcharts`、`vue3-apexcharts`
- 移除：`vuetify`、`@mdi/font`、`vite-plugin-vuetify`
- 保留：`vue`、`vue-router`、`vite`、`@vitejs/plugin-vue`、`@fontsource/dm-sans`、`@fontsource/jetbrains-mono`

## 目录结构（改造后）

```text
frontend/src/
├─ main.js            # 去 Vuetify，挂 Tailwind（import "./styles.css"）+ router
├─ styles.css         # @import "tailwindcss" + @theme 设计令牌 + 少量复用组件类
├─ lib.js             # 不变（labels/formatMoney/api/streamNdjson 等）
├─ store.js           # 不变（workspace reactive + loadAll/loadRiskData/runScan）
├─ router.js          # 仅 navItems 的 icon 字段从 mdi 名改为 lucide 组件名
├─ App.vue            # Tailwind 重建：侧栏 + 顶栏 + 主区
└─ components/
   ├─ ui/             # 自建 Tailwind 基础件
   │  ├─ Badge.vue    # 状态/优先级/严重度标签（语义色）
   │  ├─ Button.vue   # 主/次/文字按钮
   │  ├─ Card.vue     # 扁平卡片（可选，多数直接写类）
   │  ├─ Tabs.vue     # 案件工作台 Tab 条
   │  └─ Field.vue    # 原生 select/input/textarea 的 Tailwind 皮肤封装
   ├─ RiskOverview.vue        # 重写
   ├─ CaseQueue.vue           # 重写
   ├─ CaseWorkspace.vue       # 重写
   ├─ InvestigationThread.vue # 重写
   └─ BusinessView.vue        # 重写
```

配置变更：`vite.config.js` 去掉 `vite-plugin-vuetify` 插件；新增 `frontend/postcss.config.js`
（`@tailwindcss/postcss`）；`index.html` 不需要大改（CSS 由 main.js 引入）。

## 视觉系统（设计令牌）

Tailwind 4 用 CSS `@theme` 定义令牌，全部取自模板：

- **背景/表面**：`--color-canvas: #f9fafb`、`--color-surface: #ffffff`
- **边框/分隔**：`--color-border: #e4e7ec`
- **文本**：`--color-ink: #101828`、`--color-muted: #667085`、`--color-faint: #98a2b3`
- **语义色**：主 `#465fff`（hover `#3641f5`）、成功 `#039855`、警告 `#f79009`、危险 `#d92d20`、
  主洗 `#ecf3ff`
- **圆角/阴影**：8px；`0 1px 3px rgba(16,24,40,.05)`
- **字体**：DM Sans（正文）+ JetBrains Mono（eyebrow / 编号 / 代码）

组件质感：白色卡片 + 细边框 + 微阴影；hover 时边框/阴影加深；侧栏白底、激活项为
蓝色圆角药丸 + 左侧 3px 指示条；顶栏白底 94% 透明度 + backdrop-blur。

## 页面改造规格

### App 壳（App.vue）
- 侧栏（260px，可折叠为 88px rail）：顶部品牌块（柱状图 mark + 佳华智审）、"工作台"分组标题、
  3 个导航项（风险总览/案件队列/经营分析，激活高亮）、底部只读调查模式边界卡。
- 顶栏：菜单按钮、面包屑（工作台/案件队列 → 当前页标题）、系统状态点（`workspace.status`）、
  "重新扫描"按钮（loading 态）。
- 移动端：侧栏变抽屉；路由切换自动关闭。
- 页面切换保留轻量过渡。

### 风险总览（RiskOverview.vue）
- hero 区：eyebrow + "风险调查态势" + 规则集/观察期/命中描述 + 右侧案件总数大数字。
- 4 个指标卡（关键级/等待调查/等待审核/风险敞口）：图标 + 标签 + 大数字 + 注释，语义色图标底。
- 左面板"优先调查"：Top5 案件，优先级色条 + 主体 + 摘要 + 金额 + 状态 Badge。
- 右面板"案件构成"：**ApexCharts 圆环图**（应收 vs 库存）+ 两个比例条 + 数据边界注。

### 案件队列（CaseQueue.vue）
- 类型 / 状态两个原生 select（Tailwind 皮肤）+ 搜索框（lucide 放大镜、可清空）+ 计数。
- 表格：优先级 Badge / 主体+类型 / 触发摘要 / 敞口 / 状态 Badge / 观察期 / 右箭头；行 hover、
  点击进详情；空态提示。

### 案件工作台（CaseWorkspace.vue）
- 头部：返回按钮、案件类型+ID eyebrow、主体名、优先级/状态 Badge。
- 事实条：风险敞口 / 观察日期 / 规则版本 / 规则命中数（图标 + 值）。
- 自绘 Tabs：Agent 调查 / 规则信号 / 人工审核。
- 规则信号：规则卡网格（rule_id + 严重度 Badge + 名称 + 原因 + 来源/期间）。
- 人工审核：审核决定 select、审核人、原因、可选动作、MONITOR 时复查日期；提交按钮校验与
  loading；右侧审核历史列表。
- 加载 / 错误态（返回按钮）。

### 调查流（InvestigationThread.vue）
- 头部：eyebrow + "开始/重新调查"按钮。
- 事件时间线：滚动容器，按 sequence 追加；事件点图标 + 标题 + 消息 + 查询参数 + 证据预览；
  打字指示（3 个跳动点）。
- 最终报告：报告到达头、结论卡（建议优先级 Badge + 完整度进度条 + 摘要）、风险信号判断
  （阶段 Badge + 主要驱动/反向信号/后续监测）、确定事实（证据引用 Badge）、证据支持的判断
  （假设状态 Badge + 待补证）、建议动作 + 数据限制双栏、证据面板 + 轨迹回放折叠面板、
  人工边界注。
- 错误 alert：本次调查未生成报告。

### 经营分析（BusinessView.vue）
- 4 个指标卡（累计销售额/回款额/最新应收/超期率）。
- 应收趋势：**ApexCharts 折线/面积图**（应收余额、超期应收两序列）+ 保留明细表
  （期间/余额/超期/超期率）。

## 数据流与错误处理

- 数据流零改动：`store.js` 的 `loadAll/loadRiskData/runScan`、`lib.js` 的 `api/streamNdjson`
  原样使用；后端 API 契约不变。
- 组件只负责展示与表单组装，逻辑与现有 `<script setup>` 保持一致（把 Vuetify 交互换成
  原生元素 + 自建组件，事件绑定一一对应）。
- 错误处理：网络/接口错误沿用现有 `workspace.status` 与各组件 `error` 状态；不吞异常。

## 验收

1. `npm run build` 通过；`grep -rn "from \"vuetify\"\|createVuetify\|@mdi\|v-icon\|v-card\|v-app\|v-btn" src/` 无残留（除注释）。
2. 无头 Chrome 截图 4 个路由（桌面 1440×900 + 移动 ~390px）核对观感与响应式。
3. 功能实测：
   - 侧栏导航/折叠/移动抽屉、面包屑、系统状态、重新扫描按钮。
   - 案件队列筛选 + 搜索 + 计数 + 点击进详情。
   - 案件工作台 Tab 切换、审核表单校验/提交、规则信号卡、历史列表。
   - NDJSON 调查流：选一个真实案件跑一次 DeepSeek 调查，验证事件流 + 最终报告渲染
     （会产生模型额度消耗，先经用户确认）。
   - 经营分析趋势图与明细表渲染。
4. 后端 ruff/mypy/pytest 不受影响（纯前端改动）。

## 范围外（YAGNI）

- 不做暗色模式、登录、多租户、权限。
- 不转 TypeScript。
- 不引入 Pinia（现有自建 store 够用）。
- 不搬模板的电商/日历/拖拽/地图等无关页面与组件。
- 不改动后端、数据口径与 API 契约。
