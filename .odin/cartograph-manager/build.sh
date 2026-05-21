#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ARTIFACT="$(basename "${SCRIPT_DIR}")"
TARGET="${ROOT}/target/${ARTIFACT}"

echo "[cartograph-manager build] TARGET=${TARGET}"

rm -rf "${TARGET}"
mkdir -p "${TARGET}/src" "${TARGET}/config" "${TARGET}/logs" "${TARGET}/workspaces" "${TARGET}/.odin"

cp -R "${ROOT}/src/agent_management" \
      "${ROOT}/src/cartograph_mcp" \
      "${ROOT}/src/trigger_management" \
      "${ROOT}/src/shared" \
      "${TARGET}/src/"
cp "${ROOT}/src/main.py" "${ROOT}/src/mcp_servers.yaml" "${TARGET}/src/"
cp "${ROOT}/requirements.txt" "${ROOT}/pyproject.toml" "${TARGET}/"
cp "${ROOT}/settings.cartograph.json.template" "${TARGET}/config/"
cp -R "${ROOT}/.odin/${ARTIFACT}" "${TARGET}/.odin/"

echo "[cartograph-manager build] done"
