#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "已生成 .env，请检查模型与显卡配置后重新运行。"
  exit 0
fi
docker compose --profile gpu up -d --build
docker compose ps
echo "PrivShield: http://服务器IP:${WEB_PORT:-8080}"
