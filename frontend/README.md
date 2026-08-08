# 风险调查演示前端

本目录是无构建依赖的 HTML/CSS/JavaScript 页面，由 FastAPI 同源提供。启动后访问
`http://127.0.0.1:8000/`。

页面以风险案件为主流程，调用以下接口：

- `POST /api/v1/rule-runs`：幂等执行规则扫描。
- `GET /api/v1/risk/overview`：风险总览。
- `GET /api/v1/cases` 与案件详情：案件队列、规则、调查和审核记录。
- `POST /api/v1/cases/{case_id}/investigations`：运行调查 Agent。
- `POST /api/v1/cases/{case_id}/reviews`：提交人工审核。
- `GET /api/v1/overview`：直接读取 DuckDB 的首页指标，不消耗模型额度。
- `POST /api/v1/chat`：让 DeepSeek 从固定分析工具中选择并生成带证据的答案。

页面无构建依赖，使用原生 HTML、CSS 和 JavaScript；风险案件是默认入口，经营看板和问数作为辅助页。
