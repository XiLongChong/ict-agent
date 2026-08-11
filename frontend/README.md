# 风险调查工作台前端

前端使用 Vue 3、Vite、Tailwind CSS 4 与 vue-router（history 模式），采用 TailAdmin 风格的现代数据工作台。
主流程为风险总览 → 案件队列 → Agent 调查 → 人工审核；经营分析只展示确定性指标，不调用模型。

## 路由

| 路径 | 页面 |
| --- | --- |
| `/risk` | 风险总览 |
| `/cases` | 案件队列 |
| `/cases/:caseId` | 案件工作台（Agent 调查 / 规则信号 / 人工审核） |
| `/business` | 经营分析 |

任何未知前端路径会由后端 SPA 兜底路由回退到 `index.html`，交给客户端路由处理；`/api/*` 与 `/static/*` 不受影响。

## 开发与构建

```powershell
cd frontend
npm install
npm run dev
npm run build
```

开发服务器会把 `/api` 代理到 `http://127.0.0.1:8000`。FastAPI 在生产/演示模式下同源提供
`frontend/dist/`，因此修改源码后需执行 `npm run build`。

案件调查使用 `POST /api/v1/cases/{case_id}/investigations` 的 NDJSON 流。界面在固定高度的调查工作区中
按事件发生顺序自上而下追加工具调用和证据，并自动跟随最新进度；最终报告出现在时间线底部。页面不展示
私有思维链，只展示可复核工具活动、证据、经过校验的判断与人工审核边界。
