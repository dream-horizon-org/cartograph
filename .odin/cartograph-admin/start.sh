#!/usr/bin/env bash
set -euo pipefail

echo "[cartograph-admin start] APP_DIR=${APP_DIR:-}"
echo "[cartograph-admin start] ODIN_DEPLOYMENT_TYPE=${ODIN_DEPLOYMENT_TYPE:-${DEPLOYMENT_TYPE:-}}"

export PYTHONPATH="${APP_DIR}/src:${PYTHONPATH:-}"
PYTHON="${APP_DIR}/venv/bin/python"
cd "${APP_DIR}/src"

if [[ "${ODIN_DEPLOYMENT_TYPE:-${DEPLOYMENT_TYPE:-}}" == "container" ]]; then
  echo "[cartograph-admin start] exec admin_ui.server (port 8200)"
  exec "$PYTHON" -u admin_ui/server.py
else
  echo "[cartograph-admin start] background admin_ui.server (port 8200)"
  nohup "$PYTHON" -u admin_ui/server.py \
    </dev/null >>"${APP_DIR}/admin-ui.log" 2>&1 &
  echo $! >"${PID_PATH:-${APP_DIR}/.app.pid}"
fi
