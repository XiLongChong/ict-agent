#!/bin/sh
set -eu

# 数据格式版本标记。代码部署升级时，若在 docker-compose.yml 中调高了
# ICT_DATA_FORMAT_VERSION，本脚本会在此处自动重建两个数据库并重新扫描案件。
MARKER_DIR="$(dirname "$ICT_DATABASE_PATH")"
FORMAT_VERSION="${ICT_DATA_FORMAT_VERSION:-0}"
MARKER_FILE="${MARKER_DIR}/.data_format_version"

if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
  echo "DEEPSEEK_API_KEY 未配置。请先复制 .env.example 为 .env 并填写密钥。" >&2
  exit 1
fi

mkdir -p "$MARKER_DIR"

saved_version="$(cat "$MARKER_FILE" 2>/dev/null || true)"

needs_rebuild=false
if [ "$saved_version" != "$FORMAT_VERSION" ]; then
  needs_rebuild=true
elif [ ! -f "$ICT_DATABASE_PATH" ] || [ ! -f "$ICT_CASE_DATABASE_PATH" ]; then
  needs_rebuild=true
fi

if [ "$needs_rebuild" = true ]; then
  echo "检测到数据格式版本变化（当前 $saved_version，需要 $FORMAT_VERSION）或数据库缺失，"
  echo "正在清理旧数据库并从 $ICT_DATA_DIR 重新导入七张 CSV、重新扫描生成案件库…"
  rm -f "$ICT_DATABASE_PATH" "$ICT_DATABASE_PATH.wal" \
        "$ICT_CASE_DATABASE_PATH" "$ICT_CASE_DATABASE_PATH.wal"
  python backend/scripts/import_data.py
  printf '%s' "$FORMAT_VERSION" > "$MARKER_FILE"
  echo "数据库重建完成。"
fi

exec "$@"
