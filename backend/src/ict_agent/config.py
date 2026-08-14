"""应用配置读取与校验。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, SecretStr

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / ".env"
OFFICIAL_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class ConfigurationError(RuntimeError):
    """配置缺失或不受支持。"""


class Settings(BaseModel):
    """运行期不可变配置。"""

    model_config = ConfigDict(frozen=True)

    deepseek_api_key: SecretStr | None
    deepseek_base_url: str
    deepseek_model: str
    data_dir: Path
    database_path: Path
    case_database_path: Path
    simulated_data_dir: Path


def _resolve_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _merged_environment(environ: Mapping[str, str] | None) -> dict[str, str]:
    file_values = {
        key: value for key, value in dotenv_values(ENV_FILE).items() if value is not None
    }
    return {**file_values, **dict(os.environ if environ is None else environ)}


def load_frontend_dist_dir(environ: Mapping[str, str] | None = None) -> Path:
    """返回当前运行环境中的前端构建产物目录。"""

    values = _merged_environment(environ)
    return _resolve_path(values.get("ICT_FRONTEND_DIST_DIR", "frontend/dist"))


def load_settings(
    require_api_key: bool = True,
    *,
    require_data_dir: bool = True,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """从仓库 `.env` 和进程环境读取配置，进程环境优先。"""

    values = _merged_environment(environ)
    api_key = values.get("DEEPSEEK_API_KEY", "").strip()
    base_url = values.get("DEEPSEEK_BASE_URL", OFFICIAL_DEEPSEEK_BASE_URL).rstrip("/")
    model = values.get("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()
    data_dir = _resolve_path(values.get("ICT_DATA_DIR", "data/raw"))
    database_path = _resolve_path(
        values.get("ICT_DATABASE_PATH", "data/processed/ict_agent.duckdb")
    )
    case_database_path = _resolve_path(
        values.get("ICT_CASE_DATABASE_PATH", "data/processed/ict_agent_cases.duckdb")
    )
    simulated_data_dir = _resolve_path(values.get("ICT_SIMULATED_DATA_DIR", "data/simulated"))

    if require_api_key and not api_key:
        raise ConfigurationError("缺少 DEEPSEEK_API_KEY，请在项目 .env 中填写后重试。")
    if base_url != OFFICIAL_DEEPSEEK_BASE_URL:
        raise ConfigurationError("当前版本只支持 DeepSeek 官方地址 https://api.deepseek.com。")
    if not model:
        raise ConfigurationError("DEEPSEEK_MODEL 不能为空。")
    if require_data_dir and not data_dir.is_dir():
        raise ConfigurationError(
            f"数据目录不存在：{data_dir}。请设置 ICT_DATA_DIR 指向 7 张比赛 CSV。"
        )

    return Settings(
        deepseek_api_key=SecretStr(api_key) if api_key else None,
        deepseek_base_url=base_url,
        deepseek_model=model,
        data_dir=data_dir,
        database_path=database_path,
        case_database_path=case_database_path,
        simulated_data_dir=simulated_data_dir,
    )
