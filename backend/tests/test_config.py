"""配置读取测试。"""

from pathlib import Path

import pytest
from ict_agent.config import ConfigurationError, load_frontend_dist_dir, load_settings


def test_frontend_dist_dir_can_be_overridden_for_packaged_runtime(tmp_path: Path) -> None:
    dist_dir = tmp_path / "frontend" / "dist"

    assert load_frontend_dist_dir({"ICT_FRONTEND_DIST_DIR": str(dist_dir)}) == dist_dir.resolve()


def test_load_settings_resolves_paths(tmp_path: Path) -> None:
    settings = load_settings(
        environ={
            "DEEPSEEK_API_KEY": "super-private-value",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
            "DEEPSEEK_MODEL": "deepseek-v4-flash",
            "ICT_DATA_DIR": str(tmp_path),
            "ICT_DATABASE_PATH": str(tmp_path / "data.duckdb"),
            "ICT_CASE_DATABASE_PATH": str(tmp_path / "cases.duckdb"),
            "FEISHU_APP_ID": "",
            "FEISHU_APP_SECRET": "",
        }
    )

    assert settings.data_dir == tmp_path.resolve()
    assert settings.database_path == (tmp_path / "data.duckdb").resolve()
    assert settings.case_database_path == (tmp_path / "cases.duckdb").resolve()
    assert settings.feishu_app_id is None
    assert "super-private-value" not in repr(settings)


def test_load_settings_requires_complete_feishu_credentials(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="必须同时配置"):
        load_settings(
            require_api_key=False,
            environ={
                "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
                "ICT_DATA_DIR": str(tmp_path),
                "FEISHU_APP_ID": "cli_test",
                "FEISHU_APP_SECRET": "",
            },
        )


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
