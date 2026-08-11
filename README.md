# 佳华智审

佳华智审是一个面向 ICT 分销业务的风险案件处理系统。系统把销售、回款、合同、应收、库存、展期和授信数据导入 DuckDB，通过确定性规则发现候选案件，再由 AI 在受控数据范围内完成证据审查，最后交由人工复核。

项目已经形成完整的可运行链路：

```text
七张业务 CSV → DuckDB → 规则扫描 → 风险案件 → AI 审查 → 人工复核
```

规则命中只代表案件需要调查，不会自动停供、调额、催收或结案。

## 主要功能

- **风险总览**：展示待调查、待复核、处理中案件和风险敞口。
- **案件队列**：支持按案件类型、处理状态、风险等级筛选和分页查看。
- **案件处理**：集中查看案件概况、规则信号、AI 审查报告和人工复核记录。
- **AI 审查**：根据案件类型查询应收、销售回款、合同、授信、展期或库存证据，并生成可追溯的结论和处理建议。
- **人工复核**：支持确认风险、要求补充证据和确认无风险三类结论。
- **经营分析**：提供销售、回款、应收、库存等确定性经营指标和趋势。

案件状态统一为：

```text
待调查 → 待复核 → 处理中 / 已关闭
            ↓
        重新调查
```

## 技术组成

- 后端：FastAPI、Pydantic、Pydantic AI
- 数据：DuckDB
- AI 模型：DeepSeek API
- 前端：Vue 3、Tailwind CSS、ApexCharts
- 部署：Docker Compose

AI 只能调用系统注册的只读证据工具，不能执行任意 SQL、Python、Shell、文件访问或联网搜索。页面展示查询过程、证据和经过校验的报告，不展示模型的私有思维链。

## 数据准备

项目需要以下七张 CSV，文件名必须完全一致：

- `销售流水.csv`
- `业务回款明细.csv`
- `增值合同签约明细.csv`
- `应收快照_月末24期.csv`
- `库龄快照_季末8期.csv`
- `展期记录.csv`
- `客户授信.csv`

CSV、生成的 DuckDB、`.env`、日志和调查产物都不会提交到 Git。

## 本地运行

环境要求：Python 3.12、Node.js 22。

### 1. 安装后端

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

编辑 `.env`：

```dotenv
DEEPSEEK_API_KEY=你的密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
ICT_DATA_DIR=D:/path/to/数据集目录
ICT_DATABASE_PATH=data/processed/ict_agent.duckdb
ICT_CASE_DATABASE_PATH=data/processed/ict_agent_cases.duckdb
```

`ICT_DATA_DIR` 应直接指向包含七张 CSV 的目录。

### 2. 导入数据

```powershell
python backend/scripts/import_data.py
```

导入程序会校验文件名、字段和数据类型，并在校验通过后原子替换业务数据库。导入失败不会破坏上一次可用数据。

### 3. 构建前端并启动服务

```powershell
cd frontend
npm ci
npm run build
cd ..
uvicorn ict_agent.api:app --app-dir backend/src --reload
```

启动后访问：

- 系统页面：`http://127.0.0.1:8000/`
- API 文档：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/api/v1/health`

## Docker 服务器部署

服务器建议使用 4 核 CPU、8 GB 内存和 50 GB SSD，不需要 GPU。服务器需要能够访问 DeepSeek API。

### 1. 拉取项目并配置环境

```bash
git clone https://github.com/XiLongChong/ict-agent.git
cd ict-agent
cp .env.example .env
```

编辑 `.env`，至少填写 `DEEPSEEK_API_KEY`。如果需要，可以调整：

```dotenv
ICT_PORT=8000
ICT_SERVER_DATA_DIR=./data/raw
ICT_SERVER_PROCESSED_DIR=./data/processed
```

### 2. 上传数据集

不要把数据集提交到 Git。可以使用 `scp` 或 SFTP 上传到服务器：

```bash
scp 本地数据目录/*.csv user@server:/path/to/ict-agent/data/raw/
```

### 3. 构建并启动

```bash
docker compose up -d --build
docker compose logs -f ict-agent
```

首次启动会自动从七张 CSV 生成业务数据库和案件库；后续重启会复用 `data/processed/` 中的数据库。

更新数据集时，建议先停止服务再重新导入：

```bash
docker compose stop ict-agent
docker compose run --rm ict-agent python backend/scripts/import_data.py
docker compose up -d ict-agent
```

服务器上需要定期备份 `data/processed/`。

## 项目结构

```text
backend/src/ict_agent/   后端、规则、AI、数据和 API
backend/scripts/         数据导入和只读证据命令
backend/tests/           单元与集成测试
backend/evals/           AI 审查评测
frontend/                Vue 前端
docs/                    指标、架构和规则说明
data/raw/                本地原始 CSV，不提交
data/processed/          本地 DuckDB，不提交
```

## 工程检查

日常检查不会调用真实模型：

```powershell
ruff check .
ruff format . --check
mypy backend/src
pytest -q
```

真实 AI 评测会调用 DeepSeek 并产生费用，使用方式见 [backend/evals/README.md](backend/evals/README.md)。

## 当前边界

- 当前没有登录、多租户和权限管理。
- 当前不提供自由 SQL、代码执行、联网搜索、RAG 或多 Agent。
- AI 结论不能替代人工复核，也不会自动执行后续业务动作。
- 指标口径以 [docs/metric-contract.md](docs/metric-contract.md) 为准，系统架构见 [docs/technical-solution.md](docs/technical-solution.md)。
