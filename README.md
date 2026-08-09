# 佳华智审——ICT 渠道风险调查 Agent

这是一个可直接演示的 ICT 分销风险案件调查系统。7 张比赛 CSV 被原子导入本地 DuckDB；版本化规则
只负责筛出应收与库存候选案件，DeepSeek `deepseek-v4-flash` 调查 Agent 再以准确率优先的思考模式，
先发现当前案件可用的数据能力，再自主组合受控只读查询并随证据调整调查方向，最后
校验证据引用、输出可复核报告并交由人工审核。

系统明确区分四类内容：风险信号判断、工具直接证明的事实、证据支持的合理推测、当前数据无法判断的
根因或最终结果。模型中途失败时，已经取得的证据仍会保存为部分报告；系统不会为了“给出结论”而
补写缺少依据的原因，也不会因为根因未知就抹掉已经成立的风险信号。

当前能力：

- `2026.08-v2` 规则集：2 条经营中客户应收规则、3 条库存规则；黑名单应收不进入主动发现队列。
- 统一证据网关：应收与库存都只注册 `discover_evidence_capabilities`、
  `search_business_records`、`query_business_evidence` 三个工具；9 个数据集/粒度组合由单一类型化
  语义注册表约束，所有查询自动锁定案件主体且不接受 SQL。
- 应收调查覆盖月度/订单应收、销售回款、合同、授信和展期；库存通过同一查询契约覆盖季度历史、
  最新库龄结构、销售速度、退货和毛利。
- 数据发现、查询开始/完成、证据摘要和报告校验通过 NDJSON 实时推送到页面，
  并可在报告中回放。
- 结构化早期预警/恶化判断、事实、支持/削弱/无法判断假设、真实 `evidence_id`、监测项和人工审核闭环。
- 确定性经营看板：销售、成本、含税粗算毛利、回款、最新应收、月度趋势和库存健康。
- 6 个脱离规则引擎的冻结案件输入、100 分分项评分、硬门槛、重复稳定性、前后对比和人工语义复核。
- 每次七表导入生成来源文件 SHA-256、模式指纹和稳定快照 ID；只读查询连接关闭外部访问、扩展自动
  加载和临时落盘，并锁定运行配置。

通用数据问答及其 Agent 工具已经删除，避免无关能力影响案件调查。经营看板仍由确定性工具直接计算，
不消耗模型额度。

## 运行

要求 Python 3.12：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

在 `.env` 中填写：

```dotenv
DEEPSEEK_API_KEY=你的密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
ICT_DATA_DIR=D:/path/to/AFFT模拟数据集
ICT_DATABASE_PATH=data/processed/ict_agent.duckdb
ICT_CASE_DATABASE_PATH=data/processed/ict_agent_cases.duckdb
```

`ICT_DATA_DIR` 必须直接包含以下 7 个正式文件名：

- `销售流水.csv`
- `业务回款明细.csv`
- `增值合同签约明细.csv`
- `应收快照_月末24期.csv`
- `库龄快照_季末8期.csv`
- `展期记录.csv`
- `客户授信.csv`

导入会严格检查文件名、必需字段和类型；失败不会覆盖上一次可用数据库。金额单位完全遵循赛事官方
数据字典和字段说明，当前统一按“元”处理，不在 Agent 中自行猜测或换口径。

```powershell
python backend/scripts/import_data.py
cd frontend
npm install
npm run build
cd ..
uvicorn ict_agent.api:app --app-dir backend/src --reload
```

常用入口：

- 页面：`http://127.0.0.1:8000/`
- Swagger：`http://127.0.0.1:8000/docs`
- 健康检查：`GET /api/v1/health`
- 数据快照：`GET /api/v1/data-snapshot`
- 确定性经营看板：`GET /api/v1/overview`
- 规则扫描：`POST /api/v1/rule-runs`
- 风险总览：`GET /api/v1/risk/overview`
- 案件队列：`GET /api/v1/cases`
- 调查事件流：`POST /api/v1/cases/{case_id}/investigations`
- 人工审核：`POST /api/v1/cases/{case_id}/reviews`

调查接口返回 `application/x-ndjson`，每行是一个 `InvestigationStreamEvent`。事件顺序为载入案件、
数据发现、工具开始/完成、报告校验和最终保存；最终事件携带完整
`InvestigationRecord`。

操作员可以通过只读 CLI 使用与 Agent 完全相同的服务；CLI 没有任意 SQL、文件读取或写操作参数：

```powershell
python backend/scripts/evidence_cli.py --help
python backend/scripts/evidence_cli.py snapshot
python backend/scripts/evidence_cli.py capabilities --case-type ACCOUNTS_RECEIVABLE `
  --customer-id C015 --observation-date 2026-07-31
python backend/scripts/evidence_cli.py query --case-type INVENTORY `
  --material-code ZAG60265CN --inventory-org 仓库W012 `
  --dataset sales --grain month --metric sales_amount --metric net_quantity `
  --time-window last_6_months
```

## 工程验收与 Agent 评测

日常工程验收不调用真实模型：

```powershell
ruff check .
ruff format . --check
mypy backend/src
pytest -q
```

真实 Agent 评测是单独流程，会消耗 DeepSeek 额度，说明见
[backend/evals/README.md](backend/evals/README.md)。改造前基线已真实运行：6 案中 4 案通过自动硬门槛，
1 案因累计输出 Token 超限成为部分报告；该结果只评价案件进入后的 Agent 调查，不评价规则引擎。
最终候选已完成 6 案 × 2 轮真实 DeepSeek 调查：12/12 完整、12/12 自动通过、12/12 人工语义复核
通过、12/12 达到最终发布门槛，跨轮阶段一致。与改造前同重复序号 6 案相比，平均分从 93.83 提升到
100，自动通过率从 66.67% 提升到 100%，耗时下降 34.41%；双方 Token 都完整的 5 案下降 42.65%。

正式数据导入的历史基线为销售 937,476 行、回款 1,097,055 行、应收快照 379,462 行。当前规则在
模拟数据上形成 29 个案件、30 条规则命中；其中应收 10 件、库存 19 件，黑名单应收已从主动队列排除。
规则阈值与限制见
[docs/risk-rule-baseline.md](docs/risk-rule-baseline.md)。这些数值只验证数据和规则链路，不代表 Agent
调查准确率。

## 安全与产品边界

- 原始 CSV、DuckDB、`.env`、日志、调查记录和 `artifacts/` 不提交 Git。
- 模型不能执行 SQL、Python、Shell、联网、文件访问或业务写操作。统一证据查询只能从语义注册表选择
  数据集、粒度、指标、时间窗口、排序和返回行数；业务记录搜索也只能在当前案件关联标识内执行。
- 深度超期应收固定覆盖月度应收、订单应收、销售回款、展期和授信；敞口积累固定覆盖前三项、合同和
  授信。库存固定覆盖季度历史、最新库龄分桶和销售月度数据。
- 规则命中只是候选案件；Agent 建议不能自动调额、停供、催收或结案。
- 页面只展示工具事件、证据和经过校验的判断，不展示模型私有思维链。
- 当前不包含登录、多租户、自由 SQL、RAG、多 Agent、模型 fallback 或自动业务处置。
- 指标事实基线见 [docs/metric-contract.md](docs/metric-contract.md)，架构见
  [docs/technical-solution.md](docs/technical-solution.md)。
