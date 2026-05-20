#!/usr/bin/env bash
set -euo pipefail

echo "[cartograph-admin setup] APP_DIR=${APP_DIR:-}"
echo "[cartograph-admin setup] ODIN_DEPLOYMENT_TYPE=${ODIN_DEPLOYMENT_TYPE:-${DEPLOYMENT_TYPE:-}}"

if [[ -z "${APP_DIR:-}" ]]; then
  echo "ERROR: APP_DIR is required" >&2
  exit 1
fi

SUDO=""
[ "$(id -u)" -ne 0 ] && command -v sudo &>/dev/null && SUDO="sudo"
export PATH="/usr/local/bin:/usr/bin:${PATH:-}"

if ! command -v python3 &>/dev/null; then
  if command -v apt-get &>/dev/null; then
    $SUDO apt-get update -qq
    $SUDO apt-get install -y python3 python3-pip python3-venv python-is-python3
  else
    echo "ERROR: python3 required" >&2
    exit 1
  fi
fi

if [[ ! -x "${APP_DIR}/venv/bin/python" ]]; then
  python3 -m venv "${APP_DIR}/venv"
  "${APP_DIR}/venv/bin/pip" install -q --upgrade pip
fi
"${APP_DIR}/venv/bin/pip" install -q -r "${APP_DIR}/requirements.txt"

echo "[cartograph-admin setup] $(python3 --version)"
echo "[cartograph-admin setup] done"
