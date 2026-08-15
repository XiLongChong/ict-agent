# 佳华智审

佳华智审是一个面向 ICT 业务的风险调查 Agent。它不替代企业已有预警系统，而是把规则预警、外部预警
和成交前交易信号统一成案件，由 AI 在受控数据范围内跨销售、回款、合同、应收、库存、展期和授信数据
调查原因，最后交由人工复核，并通过飞书推动协同。

项目已经形成完整的可运行链路：

```text
企业预警 / 规则扫描（规则命中 → 准入漏斗 → 案件组装） / 事前交易（模拟订单 → 准入 → 案件组装） → 统一案件 → AI 证据调查 → 人工复核 → 飞书协同
```

信号只代表案件需要调查，不会自动停供、调额、催收或结案。项目不再维护与规则引擎重叠的健康度、
名单建议和独立项目审批链。

## 主要功能

- **风险总览**：展示待调查、待复核、处理中案件和风险敞口。
- **案件队列**：支持按调查策略、处理状态、风险等级筛选和分页查看。
- **事前交易**：按客户同业务类型历史订单生成正常、临界或异常新交易，经统一准入与案件组装后创建调查案件；演示模式下有效模拟订单一律立案，不改真实数据。
- **案件处理**：集中查看案件概况、来源信号、AI 审查报告和人工复核记录。
- **AI 审查**：根据调查策略查询拟交易、业务画像、应收、销售回款、合同、授信、展期或库存证据，并生成可追溯的结论和处理建议；页面按需加载最后一次 DeepSeek Chat Completions HTTP 请求和响应摘要，并可直接下载完整事务 JSON。
- **人工复核**：支持确认风险、要求补充证据和确认无风险三类结论。
- **飞书协同**：规则扫描发送聚合卡片；成交前新案件、调查完成/中断和复核完成发送案件卡片。
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

AI 只能调用系统注册的只读证据工具，不能执行任意 SQL、Python、Shell、文件访问或联网搜索。模型通过
DeepSeek 官方 OpenAI 格式端点 `POST /chat/completions` 调用；英文系统指令和工具说明负责调查策略，
官方 `response_format={"type":"json_object"}` 保证最终响应为 JSON，Pydantic Schema 与业务校验器继续
约束字段和证据结论。页面默认展示查询过程、证据和经过校验的报告；展开开发调试区时才加载最后一次
HTTP 方法、URL、脱敏请求头、累计请求体和响应摘要。完整 SSE 事务只在下载时由后端读取，避免巨型调试
数据阻塞案件页面。

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
FEISHU_APP_ID=你的飞书应用 App ID
FEISHU_APP_SECRET=你的飞书应用 App Secret
ICT_PUBLIC_BASE_URL=https://你的可访问系统地址
ICT_DATA_DIR=D:/path/to/数据集目录
ICT_DATABASE_PATH=data/processed/ict_agent.duckdb
ICT_CASE_DATABASE_PATH=data/processed/ict_agent_cases.duckdb
```

`ICT_DATA_DIR` 应直接指向包含七张 CSV 的目录。

飞书接入为可选能力。两项飞书配置同时填写后，服务会建立官方长连接。把已发布的机器人加入群聊，
发送“@机器人 绑定通知群”，即可把该群设为案件协同群；绑定信息保存在案件库中，服务重启不会丢失。
主动通知通过官方消息 API 发送，不依赖接收群消息的长连接是否短暂断开。
本地地址无法被飞书客户端打开时，可暂时留空 `ICT_PUBLIC_BASE_URL`，卡片仍会发送但不显示跳转按钮。

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

### 开发时热更新

开发阶段使用两个独立终端，不需要在每次修改后手动停止和重启服务：

```powershell
# 终端 1：后端代码变化后自动重载
.venv\Scripts\python.exe -m uvicorn ict_agent.api:app --app-dir backend/src --host 127.0.0.1 --port 8000 --reload

# 终端 2：前端热更新
Set-Location frontend
npm run dev
```

Vite 会把 `/api` 请求代理到 `http://127.0.0.1:8000`。开发者或自动化 Agent 可以直接管理这两套
服务；优先使用可复用的专用终端会话，以便持续读取日志并精确停止进程。重启前应核对 8000 端口的
监听 PID、完整命令和父子进程，只停止当前项目的进程树；启动后必须通过健康接口确认服务恢复。

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

服务器无法直连 Docker Hub 或 npm（如国内网络）时，填写镜像加速前缀后再构建：

```dotenv
ICT_BASE_REGISTRY=docker.m.daocloud.io/library/
ICT_NPM_REGISTRY=https://registry.npmmirror.com
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

当前项目不提供旧数据库结构迁移或兼容层。部署包含数据库结构变更的版本时，建议保留原目录备份后
以空的 `data/processed/` 启动，让容器根据七张 CSV 全量重建业务库和案件库：

```bash
docker compose stop ict-agent
mv data/processed data/processed.before-schema-update
mkdir -p data/processed
docker compose up -d --build
```

确认新版本验收通过后再清理备份目录。案件、调查、人工审核、飞书通知记录和通知群绑定不会迁移；
如需继续接收飞书通知，需要在目标群重新发送“绑定通知群”。若通过
`ICT_SERVER_PROCESSED_DIR` 修改了目录，上述命令应替换为该明确目录。

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
- 当前飞书接入面向同一企业租户的演示群，不包含个人授权或跨租户绑定。
- 当前不提供自由 SQL、代码执行、联网搜索、RAG 或多 Agent。
- AI 结论不能替代人工复核，也不会自动执行后续业务动作。
- 指标口径以 [docs/metric-contract.md](docs/metric-contract.md) 为准，系统架构见 [docs/technical-solution.md](docs/technical-solution.md)。
