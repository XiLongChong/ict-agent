# 佳华智审风险调查 Agent 技术方案

## 1. 当前目标

系统用一个轻量规则集发现值得调查的异常，再由准确率优先的 Agent 完成证据调查。规则不负责复杂
定性，Agent 也不能修改规则或业务数据。完成标准不是“模型给出了答案”，而是能判断有证据的风险
信号、只对未知根因局部弃答、结论可引用、运行中断时不丢失已有证据，且过程可在页面观察和回放。

```mermaid
flowchart LR
    CSV["7 张比赛 CSV"] --> DB["业务 DuckDB（原子重建）"]
    DB --> RULE["5 条确定性规则"]
    RULE --> CASE["独立案件库"]
    CASE --> CATALOG["发现案件可用语义数据"]
    CATALOG --> TOOLS["自主选择受控证据查询"]
    TOOLS --> VALIDATE["证据与结论校验"]
    VALIDATE --> REPORT["完整或部分报告"]
    REPORT --> REVIEW["人工审核与处置记录"]
    CATALOG --> STREAM["NDJSON 调查事件流"]
    TOOLS --> STREAM
    VALIDATE --> STREAM
```

## 2. 技术与职责

| 层 | 实现 | 职责 |
|---|---|---|
| 页面 | 原生 HTML/CSS/JavaScript | 风险总览、案件、实时调查过程、报告回放、审核和经营看板 |
| HTTP | FastAPI + Pydantic | `/api/v1` 校验、错误映射、NDJSON 流和 OpenAPI |
| 应用服务 | `service.py` | 经营、扫描、案件、调查保存和审核用例 |
| Agent | Pydantic AI | DeepSeek 高强度思考、工具事件、结构化输出和输出校验 |
| 业务分析 | `tools.py` / `rules.py` | 参数化指标 SQL、证据查询和版本化确定性规则 |
| 数据 | `data.py` | 业务 DuckDB 只读查询、原子导入、独立案件库写入 |

通用数据问答 Agent 已删除。经营看板继续直接调用确定性工具，避免无关工具进入案件调查上下文。

## 3. 数据与口径

业务库由 7 张正式 CSV 原子全量重建；案件、调查和审核保存在独立 DuckDB，导入业务数据不会覆盖
案件记录。合同号、客户号、订单号和物料号始终按字符串处理。金额单位不由模型判断，完全遵循赛事
官方数据字典，当前字段按“元”解释。快照、退货、零分母和关联规则见 `metric-contract.md`。

## 4. Agent 调查协议

### 4.1 模型设置

- 唯一模型：DeepSeek `deepseek-v4-flash`，官方 Provider。
- Pydantic AI `thinking="high"`，准确率优先；思考模式不设置温度。
- 禁止并行工具调用，保证页面事件和证据顺序可审计。
- 每次最多 14 次模型请求、12 次工具调用和 40,000 个累计输出 token；高思考模式的推理 token 会跨
  多轮工具调用累计，不能使用只够单次回答的预算。
- 不实现模型 fallback、手写工具循环或私有消息协议。

### 4.2 冻结案件输入契约

规则存储模型在进入 Agent 前统一映射为 `InvestigationCaseInput 2.0`：案件编号、发现来源、类型、主体、
观察日、优先级、风险敞口、摘要、来源版本、信号列表和数据质量状态。每条信号包含名称、原因、严重度、
命中指标、阈值来源、数据来源和期间。当前规则案件使用 `discovery_source=RULE`；规则尚未提供独立数据
质量判断时显式写 `UNKNOWN`。

### 4.3 动态应收调查

应收 Agent 只注册两个动作：

1. `discover_business_data`：返回当前客户可访问的数据集、粒度、指标、时间窗口和限制，不暴露表结构。
2. `query_business_evidence`：选择受控查询参数并可多次下钻，每次结果生成独立 `evidence_id`。

查询语义层当前开放 `receivables / sales_payments / extensions / credit / contracts`。所有查询由服务端
自动锁定案件客户，只允许白名单组合，不接收 SQL、任意字段、任意关联或无限行结果。最低覆盖包括应收
月度趋势、最新订单级应收、销售回款月度对齐，以及展期、授信或合同中的至少一种背景证据；Agent 可
继续查询直到关键假设得到支持、削弱或明确缺失证据。

库存垂直切片暂时保持三个固定工具：

1. `inspect_inventory_history`
2. `inspect_inventory_age_profile`
3. `inspect_material_sales`

### 4.4 证据与输出校验

每个工具返回结构化表格、来源、期间、口径、warning 和唯一 `evidence_id`，完整保存在调查记录中。
Pydantic AI 输出校验器拒绝以下报告并要求模型修正：

- 应收未发现数据目录或未达到最低证据覆盖；
- 库存三项必需工具未完成；
- 缺少独立风险信号判断，或其证据编号无效；
- 事实没有证据引用，或引用不存在的 `evidence_id`；
- `SUPPORTED` 无支持证据、`WEAKENED` 无反驳证据；
- `UNRESOLVED` 没有说明缺失证据或证据冲突；
- 在缺少相应数据时直接断言已确认坏账、一定不可回收、停供、无回款能力或已进入诉讼程序。

报告展示四层信息：风险信号判断、工具直接证明的事实、证据支持的推测、明确无法判断的根因或结果。
白名单、授信、回款和信用保险只能作为缓释证据。历史展期不能替代当前订单精确匹配；库存不能虚构
促销或下游数据，也不能与特定客户应收建立数据不支持的因果关系。

### 4.5 部分报告

模型在取得至少一项工具证据后中断时，系统保留已经取得的证据，生成确定性的部分报告：证据摘要进入 facts；
最低覆盖完成时保留规则与证据共同支持的早期预警或恶化信号，根因固定为 `UNRESOLVED`；最低覆盖
未完成时标为 `LIMITED`。异常原文、密钥、路径、SQL 和堆栈不进入 HTTP 响应。

## 5. 调查事件流

`POST /api/v1/cases/{case_id}/investigations` 返回 `application/x-ndjson`。每行是一个带顺序号的
`InvestigationStreamEvent`：

- `RUN_STARTED`
- `TOOL_STARTED`
- `TOOL_COMPLETED`
- `VALIDATION_STARTED`
- `REPORT_COMPLETED`
- `ERROR`

最终 `REPORT_COMPLETED` 携带完整 `InvestigationRecord`。页面使用 Fetch Streams 增量解析；报告中的
trace 保存工具完成和报告校验轨迹，供刷新后回放。页面不展示模型私有思维链。

## 6. HTTP 接口

| 接口 | 说明 |
|---|---|
| `GET /api/v1/health` | 健康检查 |
| `GET /api/v1/overview` | 确定性经营看板 |
| `POST /api/v1/rule-runs` | 幂等规则扫描 |
| `GET /api/v1/risk/overview` | 风险总览 |
| `GET /api/v1/cases` | 案件队列 |
| `GET /api/v1/cases/{case_id}` | 规则、最新调查和审核历史 |
| `POST /api/v1/cases/{case_id}/investigations` | NDJSON 调查事件流 |
| `POST /api/v1/cases/{case_id}/reviews` | 人工审核和状态推进 |

通用 `/api/v1/chat` 已删除。

## 7. 评测与边界

`backend/evals/` 包含 3 个应收和 3 个库存真实案件。应收按必需数据集/粒度检查动态查询覆盖，库存按
固定工具检查；两者都检查风险信号、引用、假设状态和无依据绝对结论。每个样本另有人工语义
复核清单。评测运行器直接调用 Agent，不写案件库，产物写入已忽略的 `artifacts/`。评测集尚未运行，
因此当前没有准确率或通过率结论。

当前不做登录、多租户、自由 SQL、RAG、联网、代码执行、多 Agent、自动数据刷新、模型 fallback、
预测评分或自动业务处置。新增能力不得破坏 CSV → DuckDB → 工具 → Agent → API → 页面主链路。
