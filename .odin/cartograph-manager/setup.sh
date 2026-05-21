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

# Claude CLI for agent_manager (`claude -p`). Install to ~/.local/bin, then link
# into /usr/local/bin so systemd/start.sh always find it (not bake-user PATH).
if ! command -v curl &>/dev/null && command -v apt-get &>/dev/null; then
  $SUDO apt-get install -y curl
fi
if ! command -v claude &>/dev/null \
   && [[ ! -x "${HOME}/.local/bin/claude" ]] \
   && [[ ! -x /root/.local/bin/claude ]]; then
  echo "[cartograph-manager setup] Installing Claude Code CLI..."
  curl -fsSL https://claude.ai/install.sh | bash -s stable
fi
# Installer puts binary in ~/.local/bin (not on PATH during non-interactive bake).
CLAUDE_BIN="$(command -v claude 2>/dev/null || true)"
[[ -z "${CLAUDE_BIN}" && -x "${HOME}/.local/bin/claude" ]] && CLAUDE_BIN="${HOME}/.local/bin/claude"
[[ -z "${CLAUDE_BIN}" && -x /root/.local/bin/claude ]] && CLAUDE_BIN=/root/.local/bin/claude
if [[ -z "${CLAUDE_BIN}" ]]; then
  echo "ERROR: claude CLI not installed" >&2
  exit 1
fi
$SUDO install -d /usr/local/bin
$SUDO ln -sf "${CLAUDE_BIN}" /usr/local/bin/claude
echo "[cartograph-manager setup] $(/usr/local/bin/claude --version 2>/dev/null || echo "claude OK")"

if [[ ! -x "${APP_DIR}/venv/bin/python" ]]; then
  python3 -m venv "${APP_DIR}/venv"
  "${APP_DIR}/venv/bin/pip" install -q --upgrade pip
fi
"${APP_DIR}/venv/bin/pip" install -q -r "${APP_DIR}/requirements.txt"

mkdir -p "${APP_DIR}/workspaces" "${APP_DIR}/logs" "${APP_DIR}/config"

# Claude Code refuses --dangerously-skip-permissions as root; run the app as a service user.
CARTOGRAPH_RUN_USER="${CARTOGRAPH_RUN_USER:-cartograph}"
if [[ "$(id -u)" -eq 0 ]]; then
  if ! id "${CARTOGRAPH_RUN_USER}" &>/dev/null; then
    echo "[cartograph-manager setup] Creating service user ${CARTOGRAPH_RUN_USER}..."
    useradd -r -s /bin/bash "${CARTOGRAPH_RUN_USER}"
  fi
  chown -R "${CARTOGRAPH_RUN_USER}:${CARTOGRAPH_RUN_USER}" "${APP_DIR}"
  echo "[cartograph-manager setup] APP_DIR owned by ${CARTOGRAPH_RUN_USER}"
fi

echo "[cartograph-manager setup] done"
