"""DuckDB 原子导入和只读查询测试。"""

from pathlib import Path

import pytest
from ict_agent.data import TABLE_SPECS, DataAccessError, DuckDBStore, rebuild_database


def test_rebuild_imports_all_tables(raw_data_dir: Path, tmp_path: Path) -> None:
    database = tmp_path / "nested" / "ict.duckdb"
    summaries = rebuild_database(raw_data_dir, database)

    assert database.is_file()
    assert {item.table for item in summaries} == set(TABLE_SPECS)
    assert all(item.rows > 0 for item in summaries)
    DuckDBStore(database).ensure_ready()


def test_failed_rebuild_keeps_existing_database(
    raw_data_dir: Path,
    database_path: Path,
) -> None:
    original_size = database_path.stat().st_size
    (raw_data_dir / "销售流水.csv").unlink()

    with pytest.raises(DataAccessError, match="缺少比赛数据文件"):
        rebuild_database(raw_data_dir, database_path)

    assert database_path.stat().st_size == original_size
    DuckDBStore(database_path).ensure_ready()
