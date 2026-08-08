# 佳华智审风险调查 Agent 技术方案

## 1. 目标与当前状态

当前版本交付一个可运行的风险案件闭环：确定性规则扫描七表并创建候选案件，DeepSeek 调查 Agent
针对案件分步查询证据、验证原因假设，最后由人在页面审核、处置或设置复查日期。经营看板与通用
问数继续保留为辅助能力。

```text
七表 CSV → 原子导入 → 只读业务 DuckDB
                         ↓
                   rules.py 规则扫描
                         ↓
               独立案件 DuckDB ← 人工审核
                         ↓
浏览器 / FastAPI → service.py → Pydantic AI agent.py → DeepSeek V4 Flash
                                  ↓
                            tools.py 固定证据工具
```

成功标准是规则命中可回溯、调查报告引用真实工具证据、模型不可修改金额和阈值、人工审核不抹掉
原始规则事实，而不是只生成一段看起来合理的风险描述。

## 2. 技术选型

| 层 | 方案 | 当前用途 |
|---|---|---|
| 页面 | 原生 HTML/CSS/JavaScript | 风险总览、案件队列、调查详情、人工审核和辅助分析 |
| HTTP | FastAPI + Uvicorn | `/api/v1` 契约、Swagger、同源静态页面 |
| Agent | Pydantic AI | 通用问数 Agent、结构化调查 Agent、工具循环和测试替身 |
| 模型 | DeepSeek `deepseek-v4-flash` | 工具选择、调查编排、假设解释和结构化报告 |
| 分析 | DuckDB | 七表只读事实库、确定性聚合与独立案件持久化 |
| 契约 | Pydantic | 请求、响应、工具结果和证据校验 |
| 质量 | pytest、Ruff、mypy | 数值口径、接口和类型边界验证 |

现有依赖已经覆盖当前需求，没有加入前端框架、图表库、ORM、向量数据库或任务队列。

## 3. 模块边界

- `api.py`：HTTP 路由、静态页面和错误映射；不包含 SQL、指标公式或提示词。
- `service.py`：聊天、首页、规则扫描、案件、调查和人工审核应用用例。
- `agent.py`：唯一的 DeepSeek Provider 和 Pydantic AI Agent 创建位置；问数与调查 Agent 分开创建。
- `rules.py`：版本化规则条件、组合模式、稳定案件编号和优先级，不直接执行 SQL。
- `tools.py`：指标、规则特征和调查证据 SQL，所有用户参数使用参数化查询。
- `data.py`：CSV 导入、业务库只读查询和独立案件库读写；不定义业务判断。
- `models.py`：API、工具和证据的 Pydantic 契约。
- `prompts.py`：要求数字来自工具、区分事实和建议的固定指令。
- `config.py`：读取 `.env`，校验官方 DeepSeek 地址与本地路径。
- `frontend/`：只调用 `/api/v1`，不接触数据库、模型密钥和指标公式。

后端保持扁平模块化单体。当前每个关注点都不足以成立三模块子包，不增加 repository、factory
或 provider 抽象层。

## 4. 数据方案

CSV 与表名固定映射：

| CSV | DuckDB 表 |
|---|---|
| 销售流水.csv | `sales` |
| 业务回款明细.csv | `payments` |
| 增值合同签约明细.csv | `contracts` |
| 应收快照_月末24期.csv | `ar_snapshots` |
| 库龄快照_季末8期.csv | `inventory_snapshots` |
| 展期记录.csv | `extensions` |
| 客户授信.csv | `customer_credit` |

导入器先验证文件名和必需列，在目标目录构建临时 DuckDB，7 表全部导入并通过行数、日期范围
校验后再原子替换正式数据库。合同号、订单号、客户号、物料号等标识符显式使用字符串，日期
和业务金额显式定型。任何一表失败都不跳过坏行，也不破坏已有数据库。

原始数据保留在比赛目录，通过 `ICT_DATA_DIR` 引用；业务 DuckDB 由七表原子重建。案件、调查和审核
写入 `ICT_CASE_DATABASE_PATH` 指向的独立 DuckDB，重新导入业务事实不会清空人工记录。两个数据库
都位于仓库忽略目录，不提交本地产物。

## 5. Agent 与工具

通用问数 Agent 继续注册原有 6 个分析工具：

1. `get_business_overview`
2. `get_latest_ar_summary`
3. `get_ar_trend`
4. `get_customer_risk_profile`
5. `get_inventory_health`
6. `get_project_progress`

调查 Agent 按案件类型只暴露必要工具：

- 应收案件：应收趋势、销售回款、当前应收明细、订单级展期匹配、授信上下文和正式合同闭环。
- 库存案件：逐季库存变化、最新库龄结构、最近销售/退货/毛利。

问数 Agent 每次最多 4 次模型请求和 4 次工具调用；调查 Agent 最多 8 次请求和 8 次工具调用，并且
至少取得两项独立工具证据。工具通过
依赖注入获得只读数据库，执行过程中同步收集 `Evidence`。客户端回传的历史只接受受限的
user/assistant 文本，不能伪造工具调用。

调查 Agent 使用 Pydantic 结构化输出，假设只能标记为 `SUPPORTED / WEAKENED / UNRESOLVED`。
模型引用的证据编号必须来自本轮工具结果；无效编号由程序移除。证据完整度根据真实工具数量计算，
不接受模型自由生成置信度百分比。

当前不开放通用 SQL。固定工具已经覆盖基础演示范围，而自由 SQL 会额外引入 AST 白名单、
资源限制和长尾口径风险，待出现明确需求再单独设计。

## 6. API

- `GET /api/v1/health`：HTTP 存活检查。
- `GET /api/v1/overview`：首页确定性指标，不消耗模型额度。
- `POST /api/v1/chat`：DeepSeek 工具分析，返回 `answer`、`evidence`、`request_id`。
- `POST /api/v1/rule-runs`：对最新快照幂等执行规则扫描。
- `GET /api/v1/risk/overview`：案件状态、类型、关键数量和风险敞口。
- `GET /api/v1/cases`：筛选案件队列。
- `GET /api/v1/cases/{case_id}`：规则、最新调查和审核历史。
- `POST /api/v1/cases/{case_id}/investigations`：运行结构化调查 Agent。
- `POST /api/v1/cases/{case_id}/reviews`：提交人工审核、处置或复查日期。

前端由同一 FastAPI Origin 提供，因此当前不需要 CORS。若未来拆分部署，只添加真实前端
Origin，不使用通配符。

## 7. 安全与正确性

- 模型没有 SQL、Python、Shell、文件、网络或写操作工具。
- 模型没有案件写工具；调查完成后由应用服务保存报告，业务处置只能由人工审核接口记录。
- 所有业务输入都经过 Pydantic 或工具函数校验，SQL 参数不使用字符串拼接。
- HTTP 错误不返回密钥、SQL或堆栈；服务日志只记录 request ID、错误类型和状态码。
- 应收和库存只聚合单一最新快照，趋势按期独立聚合；退货保留负值。
- 小型七表夹具验证精确公式，真实数据再与赛题报告方向交叉校验。
- 规则集、单规则、观察期和实体共同形成稳定标识，重复扫描不重复建案。
- 模型不可用时，规则案件和确定性证据仍能查看。

## 8. 当前不做

登录与企业权限、多租户、RAG/向量库、自由 SQL、多 Agent、自动业务处置、SSE/WebSocket、预测模型、
复杂风险评分、数据库 migration 和生产部署仍不属于当前版本。当前人工审核是本地单用户演示，审核人
为留痕字段，不代表已经实现身份认证。
