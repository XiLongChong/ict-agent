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
- 应收动态调查：从语义数据目录选择数据集、粒度、指标和时间窗口，按月、订单、合同、授信和展期
  自主下钻；所有查询自动锁定案件客户且不接受 SQL。
- 库存调查：逐季变化、最新库龄结构、销售速度、退货和毛利。
- 数据发现、查询开始/完成、证据摘要和报告校验通过 NDJSON 实时推送到页面，
  并可在报告中回放。
- 结构化早期预警/恶化判断、事实、支持/削弱/无法判断假设、真实 `evidence_id`、监测项和人工审核闭环。
- 确定性经营看板：销售、成本、含税粗算毛利、回款、最新应收、月度趋势和库存健康。
- 6 个代表性真实案件评测样本和离线运行器；评测集已建立但尚未运行，不宣称任何准确率。

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
uvicorn ict_agent.api:app --app-dir backend/src --reload
```

常用入口：

- 页面：`http://127.0.0.1:8000/`
- Swagger：`http://127.0.0.1:8000/docs`
- 健康检查：`GET /api/v1/health`
- 确定性经营看板：`GET /api/v1/overview`
- 规则扫描：`POST /api/v1/rule-runs`
- 风险总览：`GET /api/v1/risk/overview`
- 案件队列：`GET /api/v1/cases`
- 调查事件流：`POST /api/v1/cases/{case_id}/investigations`
- 人工审核：`POST /api/v1/cases/{case_id}/reviews`

调查接口返回 `application/x-ndjson`，每行是一个 `InvestigationStreamEvent`。事件顺序为载入案件、
数据发现、工具开始/完成、报告校验和最终保存；最终事件携带完整
`InvestigationRecord`。

## 工程验收与 Agent 评测

日常工程验收不调用真实模型：

```powershell
ruff check .
ruff format . --check
mypy backend/src
pytest -q
```

真实 Agent 评测是单独流程，会消耗 DeepSeek 额度，说明见
[backend/evals/README.md](backend/evals/README.md)。当前评测集尚未运行。

正式数据导入的历史基线为销售 937,476 行、回款 1,097,055 行、应收快照 379,462 行。当前规则在
模拟数据上形成 29 个案件、30 条规则命中；其中应收 10 件、库存 19 件，黑名单应收已从主动队列排除。
规则阈值与限制见
[docs/risk-rule-baseline.md](docs/risk-rule-baseline.md)。这些数值只验证数据和规则链路，不代表 Agent
调查准确率。

## 安全与产品边界

- 原始 CSV、DuckDB、`.env`、日志、调查记录和 `artifacts/` 不提交 Git。
- 模型不能执行 SQL、Python、Shell、联网、文件访问或业务写操作。应收查询只能从后端白名单中选择
  数据集、粒度、指标、时间窗口、排序和返回行数。
- 应收案件必须覆盖月度应收、订单应收、销售回款和至少一种背景证据；Agent 可根据新增证据继续查询
  合同、授信和展期。库存案件暂时保持 3 项固定工具完整覆盖。
- 规则命中只是候选案件；Agent 建议不能自动调额、停供、催收或结案。
- 页面只展示工具事件、证据和经过校验的判断，不展示模型私有思维链。
- 当前不包含登录、多租户、自由 SQL、RAG、多 Agent、模型 fallback 或自动业务处置。
- 指标事实基线见 [docs/metric-contract.md](docs/metric-contract.md)，架构见
  [docs/technical-solution.md](docs/technical-solution.md)。
