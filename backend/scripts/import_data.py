"""从比赛 CSV 原子重建本地 DuckDB。"""

from __future__ import annotations

import argparse
from pathlib import Path

from ict_agent.config import load_settings
from ict_agent.data import CaseStore, DataAccessError, DuckDBStore, rebuild_database
from ict_agent.rules import build_rule_scan


def parse_args() -> argparse.Namespace:
    """解析数据导入命令参数。"""

    settings = load_settings(require_api_key=False, require_data_dir=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=settings.data_dir)
    parser.add_argument("--database", type=Path, default=settings.database_path)
    return parser.parse_args()


def main() -> int:
    """执行导入并打印可人工核对的行数和日期范围。"""

    args = parse_args()
    try:
        summaries = rebuild_database(args.data_dir.resolve(), args.database.resolve())
    except DataAccessError as exc:
        print(f"导入失败：{exc}")
        return 1

    print(f"数据库已生成：{args.database.resolve()}")
    for item in summaries:
        date_range = (
            f"{item.min_date} -> {item.max_date}" if item.min_date is not None else "无时间轴"
        )
        print(f"{item.table}: {item.rows:,} 行，{date_range}")
    try:
        settings = load_settings(require_api_key=False, require_data_dir=False)
        draft = build_rule_scan(DuckDBStore(args.database.resolve()))
        created = CaseStore(settings.case_database_path).save_rule_scan(
            draft.run, draft.cases, draft.hits
        )
    except DataAccessError as exc:
        print(f"规则扫描失败：{exc}")
        return 1
    print(
        f"风险扫描：发现 {draft.run.cases_detected} 个案件，"
        f"新建 {created} 个，命中 {draft.run.rule_hits} 条规则。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
