"""配置读取测试。"""

from pathlib import Path

import pytest
from ict_agent.config import ConfigurationError, load_settings


def test_load_settings_resolves_paths(tmp_path: Path) -> None:
    settings = load_settings(
        environ={
            "DEEPSEEK_API_KEY": "secret",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
            "DEEPSEEK_MODEL": "deepseek-v4-flash",
            "ICT_DATA_DIR": str(tmp_path),
            "ICT_DATABASE_PATH": str(tmp_path / "data.duckdb"),
            "ICT_CASE_DATABASE_PATH": str(tmp_path / "cases.duckdb"),
        }
    )

    assert settings.data_dir == tmp_path.resolve()
    assert settings.database_path == (tmp_path / "data.duckdb").resolve()
    assert settings.case_database_path == (tmp_path / "cases.duckdb").resolve()
    assert "secret" not in repr(settings)


def test_load_settings_requires_api_key(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="DEEPSEEK_API_KEY"):
        load_settings(
            environ={
                "DEEPSEEK_API_KEY": "",
                "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
                "DEEPSEEK_MODEL": "deepseek-v4-flash",
                "ICT_DATA_DIR": str(tmp_path),
                "ICT_DATABASE_PATH": str(tmp_path / "data.duckdb"),
                "ICT_CASE_DATABASE_PATH": str(tmp_path / "cases.duckdb"),
            }
        )
