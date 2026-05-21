#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ARTIFACT="$(basename "${SCRIPT_DIR}")"
TARGET="${ROOT}/target/${ARTIFACT}"

echo "[cartograph-admin build] TARGET=${TARGET}"

rm -rf "${TARGET}"
mkdir -p "${TARGET}/src" "${TARGET}/.odin"

cp -R "${ROOT}/src/admin_ui" \
      "${ROOT}/src/shared" \
      "${ROOT}/src/cartograph_mcp" \
      "${TARGET}/src/"
cp "${ROOT}/requirements.txt" "${ROOT}/pyproject.toml" "${TARGET}/"
cp -R "${ROOT}/.odin/${ARTIFACT}" "${TARGET}/.odin/"

echo "[cartograph-admin build] done"
