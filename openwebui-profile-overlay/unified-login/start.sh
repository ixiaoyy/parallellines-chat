#!/usr/bin/env bash
# 沿用原生 secret/启动初始化与 worker 配置，只禁止代理头将公网地址改写为 loopback。
set -euo pipefail
exec bash /app/backend/start.sh --no-proxy-headers \
  --workers "${UVICORN_WORKERS:-1}" \
  --ws-per-message-deflate "${UVICORN_WS_PER_MESSAGE_DEFLATE:-true}"
