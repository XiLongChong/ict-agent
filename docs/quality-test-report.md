# 佳华智审（ict-agent）全面功能测试与前端审查报告

> 报告日期：2026-08-14（UTC+8）
> 测试方式：自动化质量基线 + 真实服务 API 冒烟 + 真实 DeepSeek 调查端到端 + 受管浏览器前端审查
> 报告用途：供后续开发/评审 Agent 直接按“缺陷清单”定位与修复，每个缺陷均给出位置、复现、根因与修复建议。
> 测试副作用已清理：案件库已恢复到测试前快照（`reviews=1, notifications=1, investigations=2, cases=88`），测试服务已停止。

---

## 1. 总体结论

| 维度 | 结果 |
|---|---|
| 自动化测试（pytest） | ✅ 90/90 通过（28.0s） |
| 类型检查（mypy strict） | ✅ 17 个源文件无问题 |
| Lint（ruff check .） | ❌ 50 个错误，全部集中在 `data/simulated/generate_simulated_data.py` |
| 格式（ruff format --check） | ❌ 1 个文件需重新格式化（同一文件） |
| API 冒烟测试 | ✅ 30/30 通过（含 404/422/409 边界） |
| 真实 AI 调查（DeepSeek） | ✅ 端到端通过：15 个 NDJSON 事件，2.5 分钟，报告校验通过并落库 |
| 人工复核闭环 | ✅ 三种决策状态流转正确，状态机/参数校验严格 |
| 前端页面 | ✅ 7 个页面全部可渲染、可交互（风险预警/健康度/名单管理/舆情监控/项目评估/案件队列/经营分析 + 案件处理页） |
| 发现的缺陷 | **2 个高严重度（P1）、4 个中严重度（P2）、若干观察项** |

**一句话结论**：系统主链路（七表 → DuckDB → 规则 → 案件 → AI 调查 → 人工复核 → 前端）完全可用且质量较高；问题集中在**阶段 A 风险预警新增模块**（模拟数据、健康度、名单、舆情、项目评估）的边界行为与前后端字段契约，以及一处验收基线（ruff）未达标。

---

## 2. 测试环境

| 项 | 值 |
|---|---|
| Python | 3.12.10（`.venv`，`pip install -e ".[dev]"`） |
| 数据 | `data/processed/ict_agent.duckdb`（快照 `500eae42…`，2026-08-11 导入）+ `ict_agent_cases.duckdb` |
| 服务 | `uvicorn ict_agent.api:app --app-dir backend/src`（测试端口 8123） |
| 模型 | DeepSeek `deepseek-v4-flash`（真实调用一次，`thinking=high`） |
| 前端 | 已构建 `frontend/dist`（Vite build，Vue 3.5 + Tailwind 4 + ApexCharts） |
| Git | HEAD `10543b2`，工作区 42 个未提交变更（阶段 A 新增模块未提交） |

### 2.1 复验命令（修复后照此验收）

```powershell
.venv\Scripts\ruff.exe check .
.venv\Scripts\ruff.exe format . --check
.venv\Scripts\mypy.exe backend/src
.venv\Scripts\python.exe -m pytest -q
```

---

## 3. 通过项明细

### 3.1 自动化基线
- `pytest -q`：**90 passed**（AGENTS.md 中写“53 个 pytest”已过时，实际 90 个；`backend/tests/` 现有 13 个测试文件，含阶段 A 的 `test_health.py / test_listmgmt.py / test_sentiment.py`）。
- `mypy backend/src`：Success，no issues（17 files）。
- `ruff check .` / `ruff format . --check`：**失败**，见缺陷 D-1。

### 3.2 API 功能（30/30）
| 接口 | 结果 | 备注 |
|---|---|---|
| `GET /api/v1/health` | ✅ 200 | status=ok |
| `GET /api/v1/data-snapshot` | ✅ 200 | 7 个来源（文件名/哈希/行数/日期范围）齐全 |
| `GET /api/v1/overview` | ✅ 200 | overview/latest_ar/inventory/ar_trend |
| `POST /api/v1/rule-runs` | ✅ 200 | 幂等：88 案件 / 201 命中，重复扫描 `cases_created=0` |
| `GET /api/v1/risk/overview` | ✅ 200 | 待调查 86 / 待复核 1 / 处理中 1 / 关闭 0 |
| `GET /api/v1/cases` | ✅ 200 | 88 案件，按状态/类型筛选、limit 边界（422）正确 |
| `GET /api/v1/cases/{id}` | ✅ 200 / 404 | 详情含 rule_hits/latest_investigation/reviews |
| `POST /api/v1/cases/{id}/reviews` | ✅ | CONFIRMED→处理中、重复复核 409、非法决策 422、reason 过短 422、非待复核状态 409 |
| `POST /api/v1/cases/{id}/investigations` | ✅ | 真实 DeepSeek：15 事件流，`REPORT_COMPLETED` 报告通过证据校验并保存，案件状态推进至待复核 |
| `GET/POST /api/v1/health-scores*` | ✅ | 76 条，重算 200（count=76） |
| `GET/POST /api/v1/list-recommendations*` | ⚠️ | 正常审批 200；**不存在/已处理返回 200+NOT_FOUND**（见 D-2） |
| `GET/POST /api/v1/alerts*` | ✅ | 确认 200，不存在 404 |
| `GET/POST /api/v1/sentiments*` | ⚠️ | **核验写留痕 503**（见 D-3）；404/422 边界正确 |
| `GET/POST /api/v1/projects*` | ✅ | 1149 条（存量合同 + 8 个模拟新项目）；事前评估正常/700万强制人工/黑名单拦截/404 均正确 |
| `GET /api/v1/warning/overview` | ✅ | 聚合正确（pending 8、健康下降 12、名单待批 14、未处理舆情 9 等） |
| `/openapi.json` | ✅ | 21 个路径 |
| 8 个前端路由 | ✅ | 全部 200 |

### 3.3 真实 AI 调查（AR|C058 深圳朔图，黑名单应收）
- 事件序列：`RUN_STARTED → TOOL_STARTED/COMPLETED（discover + 5 次受控查询）→ VALIDATION_STARTED → REPORT_COMPLETED`，共 15 个事件、153s。
- 证据 5 条（`receivables/month、receivables/order、sales_payments/month、credit/customer、sales_payments/month 24 期`），均带 `evidence_id`。
- 报告内容合格：区分风险信号（恶化）/ 事实 / 推测 / 无法判断，未断言已确认坏账、未停供结论；`investigation_summary` 引用真实金额（3.199 亿元、669 天、-2107.7 万负销售等）。
- 案件状态正确推进：`PENDING_AGENT_REVIEW → PENDING_HUMAN_REVIEW`；详情页可回放。

### 3.4 前端审查（受管浏览器）
| 页面 | 结论 |
|---|---|
| `/risk` 风险预警 | ✅ 待办、健康度分布（11/50/14/1）、优先调查列表、预警卡片均渲染 |
| `/health` 健康度 | ✅ 表格 + 类型/等级筛选 + 重算按钮；六维驱动说明正确 |
| `/lists` 名单管理 | ✅ 15 条建议（已采纳/待审批），审批按钮、状态筛选 |
| `/sentiments` 舆情监控 | ✅ 12 条舆情，“模拟数据”徽标正确显示，核验按钮、状态筛选 |
| `/projects` 项目评估 | ✅ 8 个模拟新项目 + 存量合同标签页，事前评估入口；“模拟数据”徽标 |
| `/cases` 案件队列 | ✅ 分页（88 条/10 行/5 页）、类型/状态/等级筛选、搜索框、跳页 |
| `/cases/{id}` 案件处理 | ✅ 案件概况（风险信号/敞口/规则版本）、AI审查（结论/建议/折叠依据/证据）、人工复核（三选项+审核人+理由）三个 tab 均正常 |
| `/business` 经营分析 | ✅ 四大指标（131.22 亿/123.24 亿/11.14 亿/60.6%）+ 应收趋势图（近 6/12 个月/全部切换） |

---

## 4. 缺陷清单（按严重度）

### D-1 [P1] ruff 验收基线不通过：`data/simulated/generate_simulated_data.py` 50 个错误
- **位置**：`data/simulated/generate_simulated_data.py`（49×E501 超长行 + 1×UP009 多余编码声明）
- **现象**：`ruff check .` 报 50 errors；`ruff format . --check` 报 1 file would be reformatted。`backend/src`、`backend/tests`、`backend/scripts` 均干净。
- **根因**：模拟数据生成脚本内含大量长中文常量行（项目/阶段/舆情行，行内嵌 100+ 字符），未按 ruff 规则换行；且该文件未被 `pyproject.toml` 的 `extend-exclude` 排除。
- **修复建议**：二选一（推荐前者）：
  1. 运行 `ruff format data/simulated/generate_simulated_data.py` 自动重排（1 个 fixable），再人工检查 E501 剩余项；
  2. 或在 `pyproject.toml [tool.ruff] extend-exclude` 中加入 `data/simulated/`（若该目录视为演示数据生成工具而非受管源码）。
- **验收**：`ruff check . && ruff format . --check` 退出码 0。

### D-2 [P1] 名单建议审批对“不存在/已处理”建议返回 200 而非 404
- **位置**：`backend/src/ict_agent/listmgmt.py`（`review_recommendation`）/ `api.py` `/api/v1/list-recommendations/{id}/reviews`
- **现象**：
  ```
  POST /api/v1/list-recommendations/NOPE/reviews  → 200 {"recommendation_id":"NOPE","status":"NOT_FOUND","message":"建议不存在或已处理。"}
  POST /api/v1/list-recommendations/{已审批}/reviews → 200 同上（重复审批不报冲突）
  ```
- **根因**：业务函数把“不存在/已处理”作为正常业务分支返回 `NOT_FOUND` 状态对象，未抛 `404/409`；与 OpenAPI `responses` 声明（404）及同域其他接口（alerts 404、reviews 409）语义不一致。
- **影响**：前端/调用方无法用 HTTP 状态码区分成功与失败，重复审批被当作“成功”处理；幂等语义无明示。
- **修复建议**：仿照 `review_case`：不存在→`ServiceError(…, 404)`，已处理→`ServiceError(…, 409)`，`api.py` 增加 409 响应声明；同步更新 `backend/tests/test_listmgmt.py`。
- **验收**：`curl -X POST …/list-recommendations/NOPE/reviews` 返回 404；重复审批返回 409；pytest 通过。

### D-3 [P1] 舆情核验二次执行必现 503（通知主键冲突）
- **位置**：`backend/src/ict_agent/sentiment.py:verify_sentiment` + `backend/src/ict_agent/data.py:CaseStore.save_notification`（`save_alert` 同理）
- **现象**：对已核验过的舆情再次 `POST /sentiments/{id}/verify` → `503 {"error":"通知无法写入案件数据库。"}`。
- **根因**：
  1. 模拟舆情 CSV 不可变（`verify_status` 永远是 `PENDING`），`verify_sentiment` 的状态检查只看 CSV；
  2. 通知/预警使用固定 ID（`NTF_SENT_{id}` / `ALT_SENT_{id}`）裸 INSERT，无 `ON CONFLICT`/存在性检查；
  3. 首次核验成功后通知已落库，二次核验走到 INSERT 时主键冲突 → `DataAccessError` → 503。
- **影响**：当前环境已存在 `NTF_SENT_S2026-001`，即对 S2026-001 的第一次核验就会 503（演示必现路径）；错误文案掩盖真实原因（主键冲突）。
- **修复建议**（任选，推荐 A+B）：
  - A：`verify_sentiment` 先查 `store` 是否已存在该舆情留痕（或维护核验状态表），已核验则返回 409/已核验结果，不重复写；
  - B：`save_notification` / `save_alert` 改为幂等写入（`INSERT … ON CONFLICT DO NOTHING` 或先查后写），与 `save_alert` docstring 声明的“幂等”语义一致（当前实现与注释不符）；
  - C：将“已核验”状态持久化到案件库（当前只存在于内存响应），使状态判断与写入一致。
- **验收**：同一舆情连续核验两次：第一次 200，第二次 4xx 明确提示或 200 幂等；`pytest -q` 通过。

### D-4 [P2] 风险预警页“风险敞口”金额单位错误：700 万元显示为“700.00 元”
- **位置**：`frontend/src/components/RiskOverview.vue:49` + `frontend/src/lib.js:formatMoney`
- **现象**：`/risk` 页“风险敞口”显示 `700.00 元`；后端 `GET /api/v1/warning/overview` 返回 `risk_exposure: 700.0`（单位万元，来自模拟舆情 `影响金额_万元`）。
- **根因**：`risk_exposure` 的契约单位是万元（alerts.risk_amount 存 `impact_amount_wan`），前端误用面向“元”的 `formatMoney`（<10000 走“元”分支）。
- **影响**：金额单位误导，违背 metric-contract “金额必须标注单位”的要求；用户会误读风险敞口。
- **修复建议**：改用 `formatMoneyWan(value)`（lib.js 已存在），或把后端 `risk_exposure` 换算为元并保持前端不变——推荐前者并同步核对 `WarningOverviewResponse` 字段注释。
- **验收**：页面显示 `700.00 万元`；无其他“万元数值 + 元单位”组合。

### D-5 [P2] 存量合同项目全部被标记 `simulated=true`（真实性混淆）
- **位置**：`backend/src/ict_agent/models.py:ProjectViewResponse.simulated = True`（默认值）+ `backend/src/ict_agent/project.py:list_projects`（返回 dict 缺 `simulated` 键）
- **现象**：`GET /api/v1/projects` 1149 条全部 `simulated=true`（含真实合同号 `1Y01012205810Q` 等）。
- **根因**：`list_projects` 的存量项目 dict 没有 `simulated` 键，`ProjectViewResponse.model_validate` 落到默认值 `True`；只有 `list_new_projects` 显式写了 `True`。
- **影响**：前端会把真实合同项目也打上“模拟数据”徽标/水印，违反 `data/simulated/sim_README.md` 隔离规则第 3 条（模拟对象才显示模拟标记）；黑名单/真实项目辨识失真。
- **修复建议**：`ProjectViewResponse.simulated` 默认值改为 `False`；`list_projects` 返回 dict 显式加 `"simulated": False`；新增单测断言存量项目 simulated=False、新项目 True（`test_listmgmt.py` / 新增 `test_project.py`）。
- **验收**：`/api/v1/projects` 中真实合同号项目 `simulated=false`；pytest 通过。

### D-6 [P2] 项目视图丢失“授信金额”字段，前端永远显示“授信 —”
- **位置**：`backend/src/ict_agent/models.py:ProjectViewResponse`（无 credit 字段）← `backend/src/ict_agent/project.py:list_new_projects`（返回 dict 含 `credit_amount_wan`）← `data/simulated/sim_new_projects.csv`（有值，如 P2026-101 授信 200 万）
- **现象**：`/projects` 页 8 个模拟新项目全部显示“授信 —”，但 CSV 有授信值。
- **根因**：`service.list_projects_service` 构造 `ProjectViewResponse` 时未映射 `credit_amount_wan`，响应模型也没有该字段，链路断裂。
- **影响**：事前评估缺少授信上下文展示（评估逻辑本身仍用授信，只是不展示）。
- **修复建议**：`ProjectViewResponse` 增加 `credit_amount_wan: float | None = None`；service 映射 `item["credit_amount_wan"]`；前端展示授信值（用 `formatMoneyWan`）。
- **验收**：`/api/v1/projects` 模拟项目带 credit 值；页面不再显示“授信 —”。

### D-7 [P2] 文档与实现不同步（AGENTS.md / technical-solution.md）
- **现象**：
  - AGENTS.md 声称“53 个 pytest”，实际 90 个；
  - AGENTS.md 目录结构未列出阶段 A 新增模块（`health.py / listmgmt.py / sentiment.py / project.py / simdata.py / evaluation.py`、`data/simulated/`、前端 `HealthScores.vue / ListManagement.vue / Sentiments.vue / Projects.vue / TrendSpark.vue / ui/Modal.vue`）；
  - `technical-solution.md` 接口表未包含阶段 A 的 11 个接口（health-scores、list-recommendations、alerts、sentiments、projects、warning/overview、pre-assessment）。
- **影响**：后续 Agent 按文档路由会漏掉阶段 A 模块，误判“骨架/未实现”。
- **修复建议**：AGENTS.md 更新测试数量与目录树；technical-solution.md 补阶段 A 接口表；metric-contract 若涉及 `risk_exposure` 单位（万元）补一句口径（呼应 D-4）。
- **注意**：按项目规则，AGENTS.md 更新需用户授权（“同意并开始建立”引导），当前仅报告差异。

### D-8 [P3] 其他小问题
| 编号 | 位置 | 问题 |
|---|---|---|
| D-8a | `/api/v1/projects` | `project_id` 不唯一（同一合同号多行，如 `1Y01012205810Q` 出现 2 次）；前端 :key 与潜在路由冲突。建议项目视图对合同号去重或追加行号。 |
| D-8b | 前端健康度表格 | “趋势：12 个时点 51” 文案含义不明（点数后跟起始分数），建议 `TrendSpark` 改为“近 12 期，51 分起”或去掉尾部数字。 |
| D-8c | `/risk` 优先调查列表 | 5 条全部为“黑名单客户仍有应收敞口”，同质化；演示脚本要求“打开非黑名单案件”，建议排序加入案件类型/规则多样性。 |
| D-8d | Git 工作区 | 42 个文件未提交（阶段 A 全部新增）；`data/simulated/` 未跟踪（是否提交属项目决策，但至少应决定并执行）。 |

---

## 5. 观察项（需确认，不一定是缺陷）

1. **调查事件流疑似并行工具调用**：`POST /investigations` 事件流在 15.6s 同时出现 4 个 `TOOL_STARTED`、15.7–15.8s 连续 4 个 `TOOL_COMPLETED`。technical-solution.md 声明“禁止并行工具调用，保证页面事件和证据顺序可审计”。若为 Pydantic AI 顺序执行但事件毫秒级发出则可接受；若模型并行发起，需在 `agent.py` 确认 `max_parallel_tool_calls` 设置。建议核查并补一条断言。
2. **`health-scores` 重算**：`POST /api/v1/health-scores/recalculate` 返回 `{"count": 76}`，且更新 `computed_at`；但重算后名单建议/预警联动是否完整（如重复重算是否重复生成建议）建议补测试确认（当前未发现重复建议）。
3. **模拟舆情“已排除”样例的核验**：`sim_sentiments.csv` 含已排除舆情（S2026-004/009/011），页面状态正确；核验接口对非 PENDING 返回 404（文案为“未找到舆情”）语义略混（应为 409），可考虑统一（低优先级）。
4. **数据量**：`data/raw` 中销售流水 93.7 万行（约 400MB），规则扫描 3–5s、健康度重算秒级，性能无异常；`/api/v1/projects` 返回 1149 行未分页，页面可接受，但建议后续分页。

---

## 6. 修复优先级建议

| 批次 | 内容 | 目标 |
|---|---|---|
| 第一批（阻断验收） | D-1 ruff 基线 | 恢复 `ruff check .` 绿色 |
| 第一批 | D-3 舆情核验 503、D-2 审批 200 | 阶段 A 写路径正确性 |
| 第二批 | D-4 单位、D-5 simulated、D-6 授信字段 | 前端展示正确性 |
| 第三批 | D-7 文档同步、D-8 小问题 | 可维护性 |

每批完成后运行 §2.1 复验命令；涉及阶段 A 的修改需回归 `test_health.py / test_listmgmt.py / test_sentiment.py`（共 90 个测试中的 50+ 个）。

---

## 7. 附录：测试产物

- 冒烟脚本：`artifacts/api_smoke_test.py`（可复用，含边界用例；本地未提交，artifacts/ 被 gitignore）
- 测试数据快照：业务库 `500eae42938123becfd29a52`（2026-08-11 导入，7 表齐全）
- 真实调查用例：`AR|C058`（应收/黑名单/4 条规则命中，报告 5 条证据）
