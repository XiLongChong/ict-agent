#!/bin/sh
set -eu

if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
  echo "DEEPSEEK_API_KEY 未配置。请先复制 .env.example 为 .env 并填写密钥。" >&2
  exit 1
fi

mkdir -p "$(dirname "$ICT_DATABASE_PATH")" "$(dirname "$ICT_CASE_DATABASE_PATH")"

if [ ! -f "$ICT_DATABASE_PATH" ] || [ ! -f "$ICT_CASE_DATABASE_PATH" ]; then
  echo "未检测到完整数据库，正在从 $ICT_DATA_DIR 导入七张 CSV 并生成案件库。"
  python backend/scripts/import_data.py
fi

exec "$@"
