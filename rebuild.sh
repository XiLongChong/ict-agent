#!/bin/sh
# 强制重建数据库：删除旧的业务库与案件库，让容器在下次启动时
# 重新导入七张 CSV 并重新扫描生成案件。
# 注意：这会清空全部案件、调查报告、人工审核与模拟交易记录。
set -eu

SERVICE="${1:-}"
PROCESSED_DIR="${ICT_SERVER_PROCESSED_DIR:-./data/processed}"
COMPOSE="docker compose"
cd "$(dirname "$0")"

if [ -z "${SERVICE}" ]; then
  echo "用法：./rebuild.sh <服务名>   （本项目通常为 ict-agent）" >&2
  echo "" >&2
  echo "该命令会：停止容器 -> 删除两个 DuckDB 数据库和版本标记 -> 重新启动容器，" >&2
  echo "容器启动时自动重新导入数据并扫描生成案件。" >&2
  echo "警告：会清空全部案件、调查报告、人工审核与模拟交易记录。" >&2
  exit 1
fi

echo "==> 停止容器 ${SERVICE}"
${COMPOSE} stop "${SERVICE}"

echo "==> 删除数据库与版本标记（${PROCESSED_DIR}）"
rm -f "${PROCESSED_DIR}/ict_agent.duckdb" "${PROCESSED_DIR}/ict_agent.duckdb.wal" \
      "${PROCESSED_DIR}/ict_agent_cases.duckdb" "${PROCESSED_DIR}/ict_agent_cases.duckdb.wal" \
      "${PROCESSED_DIR}/.data_format_version"

echo "==> 重新启动容器，entrypoint 将自动重建数据库"
${COMPOSE} up -d "${SERVICE}"

echo "完成。可用 \"${COMPOSE} logs -f ${SERVICE}\" 查看导入进度。"
