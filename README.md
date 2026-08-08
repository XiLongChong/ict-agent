# 佳华智审——ICT 渠道风险调查 Agent

这是一个可直接演示的 ICT 分销风险案件调查系统。系统把 7 张比赛 CSV 全量导入本地只读
DuckDB，由版本化规则发现应收和库存异常，再让 DeepSeek 调查 Agent 按案件类型分步查询固定证据工具，
输出原因假设、支持/反驳证据、缺失证据和建议，最后由人在页面审核、处置或设置复查日期。

经营看板与通用问数继续保留，但默认入口已经改为“风险总览 → 案件队列 → Agent 调查 → 人工审核”。

当前已支持：

- `2026.08-v1` 首期规则集：3 条应收规则、3 条库存规则和跨表组合检测。
- 同一实体多规则合并、稳定案件编号、幂等重复扫描和独立案件数据库。
- 客户应收调查：趋势、销售回款、当前订单、精确展期匹配、授信和正式合同闭环。
- 公司库存调查：逐季变化、库龄结构、销售速度、退货和毛利；不编造促销或下游库存。
- 结构化调查报告、真实证据编号、人工审核、持续观察和复查日期。
- 累计销售、成本、含税粗算毛利、回款和合同签约概览。
- 最新应收余额、超期金额、30/60 天以上超期和月度趋势。
- 按客户编号查看授信名单、销售、回款、应收、超期利息和展期次数。
- 最新库存、库龄分桶、180 天以上呆滞库存和借物超期金额。
- 按正式合同号查看签约、出库、回款和最新应收闭环。
- 灰色响应式演示页面、FastAPI OpenAPI/Swagger 和结构化工具证据。

## 运行方式

要求 Python 3.12。首次安装：

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

`ICT_DATA_DIR` 直接指向包含 7 张 CSV 的原目录即可，不需要把约 835MB 原始数据复制进仓库。

### 数据集配置

公开仓库不包含比赛 CSV、生成的 DuckDB 或案件调查记录。请自行下载赛事数据，并确保
`ICT_DATA_DIR` 指向直接包含以下文件的目录：

- `销售流水.csv`
- `业务回款明细.csv`
- `增值合同签约明细.csv`
- `应收快照_月末24期.csv`
- `库龄快照_季末8期.csv`
- `展期记录.csv`
- `客户授信.csv`

导入命令会严格检查文件名、必需字段和字段类型；任何一张表不符合契约时都会终止，且不会覆盖
上一次可用数据库。

导入数据并启动。导入成功后会自动执行一次幂等规则扫描：

```powershell
python backend/scripts/import_data.py
uvicorn ict_agent.api:app --app-dir backend/src --reload
```

打开以下地址：

- 演示页面：`http://127.0.0.1:8000/`
- Swagger：`http://127.0.0.1:8000/docs`
- 健康检查：`GET http://127.0.0.1:8000/api/v1/health`
- 首页数据：`GET http://127.0.0.1:8000/api/v1/overview`
- Agent 对话：`POST http://127.0.0.1:8000/api/v1/chat`
- 规则扫描：`POST http://127.0.0.1:8000/api/v1/rule-runs`
- 风险总览：`GET http://127.0.0.1:8000/api/v1/risk/overview`
- 案件队列：`GET http://127.0.0.1:8000/api/v1/cases`

聊天请求示例：

```json
{
  "message": "截至最新一期，公司应收余额、超期金额和超期率分别是多少？",
  "history": []
}
```

## 验收

```powershell
ruff check .
ruff format . --check
mypy backend/src
pytest -q
```

真实数据导入应得到销售 937,476 行、回款 1,097,055 行、应收快照 379,462 行等
7 张业务表。当前数据上的关键校验值为：2026-07-31 应收余额约 11.14 亿元，超期应收约
6.75 亿元，超期率约 60.60%；2026-06-30 的 180 天以上库存约 3,204.20 万元。

首期规则在当前真实模拟数据上得到 38 个案件、47 条规则命中：19 个应收案件、19 个库存案件，
其中 8 个 `CRITICAL`、22 个 `HIGH`、8 个 `MEDIUM`。阈值依据、扫描对比和已知限制见
[docs/risk-rule-baseline.md](docs/risk-rule-baseline.md)。

## 设计边界

- 原始 CSV、DuckDB、`.env`、日志和本地验收产物都不提交 Git。
- 模型不能执行 SQL、Python、Shell、联网搜索、文件访问或业务写操作，只能调用案件类型对应的固定
  只读工具。
- 规则命中只创建候选案件；模型建议不能自动调额、停供、催收或结案。
- 当前不包含企业登录、多租户、自由 SQL、RAG、多 Agent、流式输出或复杂风控评分。
- 指标口径以 [docs/metric-contract.md](docs/metric-contract.md) 为唯一事实基线。
- 架构和默认方案见 [docs/technical-solution.md](docs/technical-solution.md)。
