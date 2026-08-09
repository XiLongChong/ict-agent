# 调查 Agent 评测机

本评测只回答“案件已经进入调查后，Agent 做得怎么样”，不运行或评价规则引擎。3 个应收与 3 个库存
样本都在 `investigation_cases.json` 中保存完整的 `InvestigationCaseInput 2.0`；运行器不读取案件库、
不要求先执行规则扫描，也不写调查记录。因此同一输入可以在代码改造前后重复使用。

## 评价内容

单次运行总分 100：

| 维度 | 分值 | 主要内容 |
|---|---:|---|
| 执行完整性 | 10 | 完整、部分或失败 |
| 调查策略 | 20 | 能力发现、必要证据覆盖、非冗余与调用上限 |
| 证据质量 | 20 | 来源、期间、内容、指标定义与证据组多样性 |
| 引用与推理 | 30 | 引用存在、事实/风险引用、假设状态、风险证据多样性 |
| 结论边界 | 10 | 禁用绝对结论、未知项局部弃答、可接受风险阶段 |
| 人工交接 | 10 | 必须人工审核、行动、监测项和优先级 |

总分不能掩盖底线。自动通过还必须同时满足：完整运行、必要证据覆盖、引用完整、假设状态合法、没有
无依据绝对结论、风险阶段可行动、明确要求人工审核。自动分不判断复杂中文语义是否真正被证据蕴含；
每案另有三项人工问题，只有自动门槛和人工复核都通过时 `release_pass=true`。

评测器还会记录：原始报告与证据、工具清单、证据调用数、耗时、请求/工具/Token 用量、代码版本、
评测集哈希、业务库快照 ID、模式指纹和七表来源哈希。`--repeats` 会按案件计算分数范围、阶段一致性和
自动门槛一致性。

## 运行

先完成正式七表导入并配置 `DEEPSEEK_API_KEY`，无需运行规则扫描：

```powershell
.\.venv\Scripts\python.exe backend\evals\run_investigation_eval.py `
  --label candidate --repeats 2
```

常用操作：

```powershell
# 单案快速回归，可重复传入 --eval-id
.\.venv\Scripts\python.exe backend\evals\run_investigation_eval.py `
  --label quick --eval-id AR-C007-EXPOSURE-BUILDUP

# 评测标准校准后，用原始报告和证据重新评分，不调用模型
.\.venv\Scripts\python.exe backend\evals\run_investigation_eval.py `
  --rescore artifacts\investigation-evals\candidate.json

# 比较两个同案件、同重复序号的产物
.\.venv\Scripts\python.exe backend\evals\run_investigation_eval.py `
  --compare artifacts\investigation-evals\baseline.json `
  artifacts\investigation-evals\candidate.json

# 某项修复后，把同快照、同评测集的定向复跑替换进完整产物；来源 Run ID 会保留
.\.venv\Scripts\python.exe backend\evals\run_investigation_eval.py `
  --replace-runs artifacts\investigation-evals\candidate.json `
  artifacts\investigation-evals\targeted-rerun.json --label candidate-validated

# 生成人工复核模板；填写 PASS/FAIL 和 note 后合并最终门槛
.\.venv\Scripts\python.exe backend\evals\run_investigation_eval.py `
  --review-template artifacts\investigation-evals\candidate.json
.\.venv\Scripts\python.exe backend\evals\run_investigation_eval.py `
  --apply-reviews artifacts\investigation-evals\candidate.json `
  artifacts\investigation-evals\candidate-review-template.json
```

模型运行默认把 JSON 与 Markdown 摘要写入已被 Git 忽略的 `artifacts/investigation-evals/`。重新评分
与定向替换都不会覆盖原产物；定向替换要求模型、评测集哈希和数据快照完全一致。人工复核文件必须与
运行 `run_id`、评测项、重复序号和问题文本精确匹配，防止把旧审核误套到新结果。

## 已验证基线

改造前真实 DeepSeek 基线已于同一冻结评测集上运行 1 次：6 案全部产生报告，其中 1 案为部分报告；
自动硬门槛通过 4/6，平均 93.83 分（按当前评分规则重新计算），总耗时 764.992 秒。C007 因累计输出
Token 超过 40,000 而中断；旧运行器无法从中断结果取得该案完整用量，因此改造前总 Token 不能与
包含完整 C007 用量的候选总量直接相减。公平的成本比较必须使用两边都完整的同一案件子集。

最终候选在同一评测集和数据快照上完成 6 案 × 2 轮：12/12 完整、自动门槛 12/12、人工语义复核
12/12、最终发布门槛 12/12，平均分与最低分均为 100，六个案件的阶段和自动门槛均跨轮一致。最终
产物由完整双轮运行与 ZAG 重复窗口修复后的定向双轮复跑组成，并保留两个来源 Run ID；原始产物未被
覆盖。与改造前同重复序号的 6 案比较：平均分 93.83 → 100，自动通过率 66.67% → 100%，总耗时下降
34.41%，证据查询 37 → 24；Token 只比较双方都有完整用量的 5 案，下降 42.65%。
