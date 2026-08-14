"""DuckDB 原子导入和只读查询测试。"""

from pathlib import Path

import pytest
from ict_agent import data as data_module
from ict_agent.data import (
    TABLE_SPECS,
    DataAccessError,
    DuckDBStore,
    rebuild_database,
)


def test_rebuild_imports_all_tables(raw_data_dir: Path, tmp_path: Path) -> None:
    database = tmp_path / "nested" / "ict.duckdb"
    summaries = rebuild_database(raw_data_dir, database)

    assert database.is_file()
    assert {item.table for item in summaries} == set(TABLE_SPECS)
    assert all(item.rows > 0 for item in summaries)
    store = DuckDBStore(database)
    store.ensure_ready()
    snapshot = store.get_snapshot()
    assert len(snapshot.snapshot_id) == 24
    assert len(snapshot.sources) == 7
    assert all(len(item.sha256) == 64 for item in snapshot.sources)


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


def test_read_only_store_blocks_external_file_access(
    database_path: Path, raw_data_dir: Path
) -> None:
    store = DuckDBStore(database_path)

    with pytest.raises(DataAccessError, match="数据查询失败"):
        store.fetch("SELECT * FROM read_csv_auto(?)", [str(raw_data_dir / "销售流水.csv")])


def test_rebuild_rejects_source_changed_during_import(
    raw_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    actual_sha256 = data_module._file_sha256
    calls = 0

    def drifting_sha256(path: Path) -> str:
        nonlocal calls
        calls += 1
        digest = actual_sha256(path)
        return digest if calls <= len(TABLE_SPECS) else "0" * 64

    monkeypatch.setattr(data_module, "_file_sha256", drifting_sha256)

    with pytest.raises(DataAccessError, match="在导入期间发生变化"):
        rebuild_database(raw_data_dir, tmp_path / "drifting.duckdb")
