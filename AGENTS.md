# ICT Agent 项目开发规则

本文件是整个仓库的唯一 Agent 规则入口，适用于根目录及所有子目录。后续开发 Agent 在修改
任何文件前必须先完整阅读本文件，再按下方“文档读取路由”读取与任务直接相关的资料。

## 1. 当前项目状态

当前仓库已经是可运行的 MVP，不是初始化骨架：

- 7 张比赛 CSV 可以原子导入本地 DuckDB。
- FastAPI 同时提供确定性经营分析接口，以及风险扫描、案件、NDJSON 调查事件流和人工审核接口。
- Pydantic AI 通过 DeepSeek 官方 OpenAI Chat Completions API 使用 `deepseek-v4-flash` 高强度思考与 JSON Mode；案件调查按应收、库存或事前交易类型自动
  执行完整的最小只读工具集，并对最终证据引用和结论状态进行校验。
- 已支持经营概览、最新应收、应收趋势、客户画像、库存健康和合同闭环。
- 已落地 23 条版本化确定性风险规则、独立案件库、风险信号判断、完整与部分调查报告、证据引用和人工审核闭环。
- `frontend/` 是由 FastAPI 同源提供的响应式风险调查工作台，实时展示工具和证据，同时保留
  确定性经营分析；通用数据问答已经删除。
- 已统一规则扫描与成交前交易信号入口，业务类型只作为交易级调查上下文，不再维护平行的健康度、
  名单建议或静态项目审批链。
- 飞书已接入新案件、调查完成/中断和人工复核通知；审批仍在同一案件页面完成。
- 当前自动验收基线为 Ruff、mypy 和 102 个 pytest 测试。

不得把已经完成的系统描述成“待搭建”，不得根据旧计划重复创建骨架，也不得为了未来功能
破坏已经跑通的真实数据 → DuckDB → 工具 → Agent → API → 页面链路。

## 2. 项目目录与职责

```text
ict-agent/
├─ AGENTS.md                         # 本规则文件，唯一 Agent 入口
├─ README.md                         # 安装、导入、启动和当前能力
├─ pyproject.toml                    # Python 3.12 依赖与质量工具配置
├─ backend/
│  ├─ scripts/import_data.py         # 7 张 CSV 的原子全量导入命令
│  ├─ src/ict_agent/
│  │  ├─ api.py                      # FastAPI 路由、错误映射、静态页面
│  │  ├─ service.py                  # 经营、扫描、案件、调查和审核应用用例
│  │  ├─ agent.py                    # DeepSeek Provider、Agent 和工具注册
│  │  ├─ data.py                     # 业务库导入/只读查询与独立案件库读写
│  │  ├─ tools.py                    # 业务指标语义与确定性分析
│  │  ├─ rules.py                    # 确定性风险规则，只产出原始命中
│  │  ├─ rule_engine.py              # 规则命中→准入漏斗→案件组装编排
│  │  ├─ admission.py                # 命中合法性、去重和主体准入分组
│  │  ├─ case_assembler.py           # 准入信号组到案件/持久化信号的组装
│  │  ├─ rule_models.py              # 规则流水线内部领域对象
│  │  ├─ models.py                   # API、工具和证据模型
│  │  ├─ prompts.py                  # Agent 固定指令
│  │  ├─ config.py                   # `.env` 和路径配置
│  │  ├─ business_type.py            # 交易级业务类型判定
│  │  ├─ pretransaction.py           # 历史分布新交易纯计算模拟器
│  │  └─ evaluation.py               # 调查评测运行器
│  ├─ tests/                         # 七表微型夹具及单元/集成测试
│  └─ evals/                         # 真实 DeepSeek 调查评测集与独立运行器
├─ frontend/                         # 无构建依赖的 HTML/CSS/JavaScript 页面
│  └─ src/components/                # 风险总览、案件、事前交易和经营分析页面
├─ docs/
│  ├─ technical-solution.md          # 当前已落地架构与安全边界
│  ├─ metric-contract.md             # 当前代码执行的指标口径
│  ├─ deep-research-report.md        # 业务研究与复杂规则候选，必须保留
│  ├─ risk-rule-baseline.md           # 首批正式规则、阈值、回测与复查点
│  ├─ risk-investigation-upgrade-design.md # 风险案件层与调查闭环设计基线
│  └─ demo-script.md                 # 当前版本演示路径
├─ data/raw/                         # 可选本地 CSV 目录，不提交数据
├─ data/processed/                   # 生成的 DuckDB，不提交
└─ artifacts/                        # 本地验收产物，不提交
```

后端 Python 包保持当前扁平结构。只有同一关注点已经出现至少 3 个有实际职责的模块时才允许
建立子包；不得提前创建 repository、factory、provider 等空抽象。

## 3. 文档读取路由与事实优先级

所有任务先读本文件和 `README.md`，然后按任务选择：

- 修改架构、API、Agent、安全边界或前后端职责：读 `docs/technical-solution.md`。
- 修改指标、SQL、字段、表关联或工具结果：读 `docs/metric-contract.md`，并运行对应数值测试。
- 讨论或实现复杂风控规则、阈值、权重、预警、客户分层或治理方案：同时读
  `docs/deep-research-report.md`。
- 修改演示流程或交付说明：读 `docs/demo-script.md`。

事实优先级：

1. 用户当前明确要求。
2. `docs/metric-contract.md` 中已经冻结且由测试覆盖的执行口径。
3. 当前代码、OpenAPI 和自动化测试表现出的已实现契约。
4. `docs/technical-solution.md` 的架构边界。
5. `docs/deep-research-report.md` 的研究结论和候选复杂规则。

`docs/deep-research-report.md` 必须保留，但它不是“看到就全部实现”的开发清单。报告中的新阈值、
评分、算法和 SQL 只有在用户明确选择产品规则后，才能先同步到 `metric-contract.md`，再进入
代码和测试。研究报告与当前执行口径冲突时不得自行选边，必须向用户报告差异。

仓库不再维护静态 `development-plan.md`。后续开发范围以用户当前任务为准；需要多阶段实施时，
在当前任务中制定可验证的工作计划，不新增一份长期失真的任务卡文档。

## 4. 不可违反的工程原则

1. **不保留向后兼容。** 过时实现直接删除；不增加兼容层、migration 或 fallback。接口或数据
   结构变化时，同一批次同步修改所有调用方、测试和文档。
2. **选择满足当前需求的最简单实现。** 不做预防性抽象，不增加没有当前调用方的接口或配置层。
3. **保持端到端链路可运行。** 每个批次都必须保住真实数据导入、确定性计算和 API 主链路。
4. **关注点分离。** UI、HTTP、应用服务、Agent、业务指标和数据访问各守边界。
5. **先检查现有依赖。** 新增依赖前检查 `pyproject.toml` 和已有库能力；能复用则不加包。
6. **优先成熟方案。** 涉及框架、协议或模型能力时核对当前官方文档，不从零重写成熟能力。
7. **新增行为必须测试。** 数值口径使用小型固定夹具，不以完整比赛数据代替单元测试。
8. **错误必须可行动。** 不吞异常，不向 HTTP 返回密钥、SQL、文件路径或堆栈。

## 5. 固定架构边界

- FastAPI 路由只做 HTTP 校验、调用应用服务和错误映射；`api.py` 中不得写 SQL、指标公式或
  Agent 提示词。
- Agent 使用 Pydantic AI 的 OpenAI Chat 模型适配和 DeepSeek Provider；不得手写工具调用循环、模型消息协议或结构化输出解析，不得在业务模块
  直接调用 OpenAI SDK。
- DeepSeek Provider 和 Agent 创建只放在 `agent.py`；其他模块不得读取模型密钥或调用模型 API。
- DuckDB 连接、建表和查询执行只放在 `data.py`；业务指标语义和参数化 SQL 放在 `tools.py`。
- 字段、关联和固定口径只在 `docs/metric-contract.md` 冻结，页面和提示词不得复制另一套公式。
- 模型不能执行 Python、Shell、文件访问、联网搜索或任意 SQL，只能调用注册工具。
- 金额、比例、日期和客户结论必须来自本轮工具结果，禁止模型心算或凭上下文补数。
- 前端不得接触 DuckDB、模型密钥或业务 SQL；当前前后端同源，不添加通配符 CORS。
- 所有公开 API 使用 `/api/v1`，请求和响应使用 Pydantic 模型，以生成的 OpenAPI 为接口契约。

## 6. 数据与安全规则

- 原始 CSV、生成的 DuckDB、`.env`、密钥、日志和 `artifacts/` 本地产物不得提交 Git。
- 数据导入固定匹配 7 个正式文件名；缺文件、缺列或类型错误必须失败，不模糊匹配、不跳过坏行。
- 导入必须继续使用临时数据库校验通过后原子替换的方式，失败不能破坏已有数据库。
- 合同号、客户号、订单号、物料号等业务标识始终按字符串处理，即使样本看起来是数字。
- 应收和库存是快照，只能按单一期次聚合；趋势按每期分别聚合，禁止跨快照直接求和。
- 销售退货保留负数量和负金额；分母为 0 时比例返回 `null` 和 warning。
- 用户参数必须校验并使用参数化查询，不得拼接到 SQL。

## 7. 修改权限与必须询问的事项

用户方向明确后，普通工程选择由开发 Agent 负责，不需要为文件命名、函数拆分、测试方式或错误
处理等常规实现反复询问。

以下事项会改变产品规则或安全边界，未经用户明确授权不得实施：

- 启用研究报告中的新阈值、权重、客户评分、预警或处置规则。
- 开放通用 SQL、RAG、代码执行、联网工具、多 Agent 或模型 fallback。
- 引入登录、多租户、权限、外部数据库、云部署或自动数据刷新。
- 改变指标口径、表关联、DeepSeek 模型、API 主契约或前端产品方向。

任务仅要求评审、诊断或方案时保持只读；任务明确要求修改或搭建时，完成实现、验证和文档同步。
发现研究报告、指标契约、真实数据与用户要求矛盾时停止扩大实现，报告精确冲突并询问用户。

## 8. 本地开发服务管理

- 本地开发固定只保留一套后端服务；前端只有需要源码热更新时才另开一套 Vite。不得让多个 Agent、
  终端或后台进程重复占用 8000 端口。
- Agent 可以按当前工具能力直接启动、停止和重启本项目服务，不需要把普通服务管理交还用户。优先使用
  可复用的专用终端/PTY 会话托管 Uvicorn 或 Vite，使输出可继续读取、进程可精确停止；不得把长期服务
  附着在会等待其退出的一次性验收命令中。
- 专用终端不可用时，可以使用系统原生后台进程方式；Windows 必须隐藏窗口，并保存可核对的 PID、
  完整命令和日志位置。不得通过不透明的 Shell 拼接启动，也不得遗留无人识别的后台服务。
- 重启前必须检查监听 PID、完整命令、父子进程关系和健康接口，确认进程属于当前工作区；停止时只处理
  已核实的这一套进程树，禁止结束全部 `python.exe`、`node.exe` 或其他无关进程。虚拟环境启动器及其
  基础解释器子进程视为同一套服务，不得仅因出现多个 Python PID 就判断为冲突。
- 修改后端代码后先等待 `--reload` 生效并检查健康接口；若工作进程未刷新、运行契约仍是旧版或健康
  检查失败，Agent 应直接执行一次精确重启并复验。修改前端代码时优先依赖 Vite 热更新；使用 FastAPI
  提供的生产构建时，完成 `npm run build` 后刷新页面即可，只有后端静态映射失效时才重启后端。
- 启动或重启后必须确认端口只有一套监听进程、`GET /api/v1/health` 成功，并按任务风险继续检查真实
  HTTP、OpenAPI、浏览器或模型调用；不得仅凭启动命令已返回就声称服务可用。

开发时使用两个独立终端：

```powershell
# 终端 1：后端自动重载
.venv\Scripts\python.exe -m uvicorn ict_agent.api:app --app-dir backend/src --host 127.0.0.1 --port 8000 --reload

# 终端 2：前端热更新
Set-Location frontend
npm run dev
```

## 9. 完成前验收

每次代码修改至少运行：

```powershell
ruff check .
ruff format . --check
pytest -q
```

涉及 Python 类型或跨模块契约时同时运行：

```powershell
mypy backend/src
```

按改动风险追加：

- 数据导入变化：用七表微型夹具测试，并运行一次真实 `backend/scripts/import_data.py`。
- Agent 或工具变化：使用 Pydantic AI 测试模型；交付前再做一次真实 DeepSeek 工具调用。
- API 变化：检查 `/openapi.json` 和真实 HTTP 请求。
- 前端变化：在桌面和移动端浏览器检查页面、交互和控制台。

未通过验收不得声称完成，不得通过删除断言、跳过测试或增加 fallback 掩盖失败。
