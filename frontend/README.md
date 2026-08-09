# 风险调查演示前端

本目录是无构建依赖的原生 HTML/CSS/JavaScript 页面，由 FastAPI 同源提供。主流程是风险总览 →
案件队列 → Agent 调查 → 人工审核；经营分析页只展示确定性指标，不调用模型。通用数据问答已删除。
Agent 调查区使用固定版本的 Web Awesome 3.11.0 Web Components，通过 jsDelivr 按需导入其 npm
发行包中的 8 个组件；没有引入全局样式重置，因此不会改变其他经营页面的原生组件契约。

调查请求使用 `POST /api/v1/cases/{case_id}/investigations`，浏览器逐行读取
`application/x-ndjson`，展示数据发现、查询开始/完成、证据摘要、报告校验和最终保存。
页面不展示私有思维链。报告单独显示风险阶段、主要驱动、反向信号和监测项。中断后如果服务端保存了
部分报告，页面照常显示已取得事实；最低证据覆盖完成时保留风险信号，只把具体根因标为“无法判断”。

其他接口：

- `POST /api/v1/rule-runs`：幂等执行规则扫描。
- `GET /api/v1/risk/overview`：风险总览。
- `GET /api/v1/cases` 与 `GET /api/v1/cases/{case_id}`：案件队列和详情。
- `POST /api/v1/cases/{case_id}/reviews`：提交人工审核。
- `GET /api/v1/overview`：读取确定性经营指标。
