#!/usr/bin/env bash
# MCP (:8100) + trigger manager + agent manager. Env vars must be exported by Odin before this runs.
set -euo pipefail

echo "[cartograph-manager start] APP_DIR=${APP_DIR:-}"
echo "[cartograph-manager start] ODIN_DEPLOYMENT_TYPE=${ODIN_DEPLOYMENT_TYPE:-${DEPLOYMENT_TYPE:-}}"

# Bedrock Claude Code settings from S3 (optional; skip if CARTOGRAPH_AGENT_SETTINGS_PATH already set).
if [[ -n "${CARTOGRAPH_SETTINGS_S3_URI:-}" ]] && [[ -z "${CARTOGRAPH_AGENT_SETTINGS_PATH:-}" ]]; then
  mkdir -p "${APP_DIR}/config"
  aws s3 cp "${CARTOGRAPH_SETTINGS_S3_URI}" "${APP_DIR}/config/settings.cartograph.json"
  chmod 600 "${APP_DIR}/config/settings.cartograph.json"
  export CARTOGRAPH_AGENT_SETTINGS_PATH="${APP_DIR}/config/settings.cartograph.json"
  echo "[cartograph-manager start] Bedrock settings from S3 → ${CARTOGRAPH_AGENT_SETTINGS_PATH}"
fi

if [[ ! -x "${APP_DIR}/venv/bin/python" ]]; then
  echo "ERROR: ${APP_DIR}/venv missing — setup.sh must create it and install requirements.txt" >&2
  exit 1
fi

export PYTHONPATH="${APP_DIR}/src:${PYTHONPATH:-}"
PYTHON="${APP_DIR}/venv/bin/python"
LOG_DIR="${CARTOGRAPH_LOG_DIR:-${APP_DIR}/logs}"
mkdir -p "${LOG_DIR}" "${APP_DIR}/workspaces"

MCP_HOST="${CARTOGRAPH_MCP_HOST:-127.0.0.1}"
MCP_PORT="${CARTOGRAPH_MCP_PORT:-8100}"
cat > "${APP_DIR}/src/mcp_servers.yaml" <<EOF
cartograph-db:
  url: http://${MCP_HOST}:${MCP_PORT}
last9-reader:
  url: http://127.0.0.1:8101/mcp
EOF

if [[ ! -e "${APP_DIR}/src/workspaces" ]]; then
  ln -sfn "${APP_DIR}/workspaces" "${APP_DIR}/src/workspaces"
fi

_stop_bg() {
  for pidfile in "${LOG_DIR}/mcp.pid" "${LOG_DIR}/triggers.pid"; do
    [[ -f "${pidfile}" ]] && kill "$(cat "${pidfile}")" 2>/dev/null || true
    rm -f "${pidfile}"
  done
}

cd "${APP_DIR}/src"
trap _stop_bg EXIT TERM INT

echo "[cartograph-manager start] MCP → ${LOG_DIR}/mcp.log"
nohup "$PYTHON" -u cartograph_mcp/server.py </dev/null >>"${LOG_DIR}/mcp.log" 2>&1 &
echo $! >"${LOG_DIR}/mcp.pid"

sleep "${CARTOGRAPH_MCP_START_DELAY_SEC:-3}"

echo "[cartograph-manager start] triggers → ${LOG_DIR}/triggers.log"
nohup "$PYTHON" -u trigger_management/main.py </dev/null >>"${LOG_DIR}/triggers.log" 2>&1 &
echo $! >"${LOG_DIR}/triggers.pid"

if [[ "${ODIN_DEPLOYMENT_TYPE:-${DEPLOYMENT_TYPE:-}}" == "container" ]]; then
  echo "[cartograph-manager start] agent manager (foreground) → ${LOG_DIR}/agents.log"
  "$PYTHON" -u main.py 2>&1 | tee -a "${LOG_DIR}/agents.log"
else
  echo "[cartograph-manager start] agent manager (background) → ${LOG_DIR}/agents.log"
  nohup "$PYTHON" -u main.py </dev/null >>"${LOG_DIR}/agents.log" 2>&1 &
  echo $! >"${PID_PATH:-${APP_DIR}/.app.pid}"
  trap - EXIT TERM INT
fi
