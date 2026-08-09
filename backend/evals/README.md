# 调查 Agent 评测集

这套评测用于后续真实 DeepSeek 回归，不属于日常 `pytest`。当前包含 3 个应收案件和 3 个库存
案件，重点评估：应收数据发现与必需查询粒度覆盖、库存固定工具覆盖、风险信号可行动、证据
引用完整、假设状态合法、是否只对未知根因局部弃答，以及是否出现已确认坏账、停供、不可回收、促销
归因等无依据结论。

评测数据以赛事官方数据字典为准，金额字段统一按“元”解释，不在 Agent 内自行换单位口径。

运行前必须已经导入正式 7 表数据并配置 `DEEPSEEK_API_KEY`：

```powershell
.\.venv\Scripts\python.exe backend\evals\run_investigation_eval.py
```

运行前先执行一次 `POST /api/v1/rule-runs`，保证案件库已经保存当前 `2026.08-v2` 案件。也可以用
`--case-id` 只跑一个案件。运行器直接调用调查 Agent，不写入调查记录；结果保存到已被
Git 忽略的 `artifacts/investigation-eval-*.json`。自动判定只覆盖结构性底线，`human_review`
中的语义问题仍需在结果文件中人工复核。

本评测集创建后尚未运行，因此仓库文档不得把任何准确率或通过率写成已验证结果。
