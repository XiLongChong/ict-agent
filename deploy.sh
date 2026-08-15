#!/bin/sh
# 一键部署：拉取代码、重建镜像、启动/更新容器，并等待健康检查通过。
# 用法：./deploy.sh [服务名]   服务名默认 ict-agent
set -eu

SERVICE="${1:-ict-agent}"
COMPOSE="docker compose"
cd "$(dirname "$0")"

echo "==> 拉取最新代码"
git pull

echo "==> 重新构建镜像（无缓存层变化则很快）"
${COMPOSE} build "${SERVICE}"

echo "==> 更新并启动容器"
${COMPOSE} up -d "${SERVICE}"

echo "==> 等待服务健康检查通过"
tries=0
while [ "${tries}" -lt 30 ]; do
  if docker inspect --format '{{.State.Health.Status}}' "$(${COMPOSE} ps -q "${SERVICE}")" 2>/dev/null | grep -q healthy; then
    echo "部署完成，服务已健康运行。"
    exit 0
  fi
  tries=$((tries + 1))
  sleep 10
done

echo "!! 服务 5 分钟内未通过健康检查，请查看日志：" >&2
echo "   ${COMPOSE} logs -f ${SERVICE}" >&2
exit 1
