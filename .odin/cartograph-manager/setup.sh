#!/usr/bin/env bash
set -euo pipefail

echo "[cartograph-manager setup] APP_DIR=${APP_DIR:-}"

if [[ -z "${APP_DIR:-}" ]]; then
  echo "ERROR: APP_DIR is required" >&2
  exit 1
fi

SUDO=""
[ "$(id -u)" -ne 0 ] && command -v sudo &>/dev/null && SUDO="sudo"

if ! command -v python3 &>/dev/null; then
  if command -v apt-get &>/dev/null; then
    $SUDO apt-get update -qq
    $SUDO apt-get install -y python3 python3-pip python3-venv python-is-python3 curl awscli
  else
    echo "ERROR: python3 required" >&2
    exit 1
  fi
fi

# Claude Code CLI (agent_manager spawns `claude -p`).
if ! command -v curl &>/dev/null && command -v apt-get &>/dev/null; then
  $SUDO apt-get install -y curl
fi

if ! command -v claude &>/dev/null; then
  echo "[cartograph-manager setup] Installing Claude Code CLI..."
  curl -fsSL https://claude.ai/install.sh | bash -s stable
fi
export PATH="${HOME}/.local/bin:/usr/local/bin:${PATH:-}"
if ! command -v claude &>/dev/null; then
  echo "ERROR: claude CLI not found after install (need ~/.local/bin or apt claude-code on PATH)" >&2
  exit 1
fi

echo "[cartograph-manager setup] $(claude --version 2>/dev/null || echo "claude at $(command -v claude)")"

if [[ ! -x "${APP_DIR}/venv/bin/python" ]]; then
  python3 -m venv "${APP_DIR}/venv"
  "${APP_DIR}/venv/bin/pip" install -q --upgrade pip
fi
"${APP_DIR}/venv/bin/pip" install -q -r "${APP_DIR}/requirements.txt"

mkdir -p "${APP_DIR}/workspaces" "${APP_DIR}/logs" "${APP_DIR}/config"
echo "[cartograph-manager setup] done"
