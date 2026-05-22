#!/usr/bin/env bash
# Bump .odin/<artifact>/application-spec.yaml after a concrete release publish.
# Expects ARTIFACT_NAME and RELEASE_VERSION (without -SNAPSHOT) in the environment.
set -euo pipefail

ARTIFACT_NAME="${ARTIFACT_NAME:?ARTIFACT_NAME is required}"
RELEASE_VERSION="${RELEASE_VERSION:?RELEASE_VERSION is required}"
BRANCH="${RELEASE_BRANCH:-master}"

APPLICATION_SPEC=".odin/${ARTIFACT_NAME}/application-spec.yaml"
if [[ ! -f "${APPLICATION_SPEC}" ]]; then
  echo "ERROR: missing ${APPLICATION_SPEC}" >&2
  exit 1
fi

CURRENT="$(grep '^version:' "${APPLICATION_SPEC}" | sed 's/version:[[:space:]]*//')"
if [[ "${CURRENT}" == "${RELEASE_VERSION}" ]]; then
  echo "ERROR: expected SNAPSHOT version in ${APPLICATION_SPEC}, got ${CURRENT}" >&2
  exit 1
fi

# Match Maven nextSnapshot semantics: 0.1.0-SNAPSHOT → release 0.1.0 → 0.1.1-SNAPSHOT
IFS=. read -r major minor patch _ <<< "${RELEASE_VERSION}.0.0"
patch=$((patch + 1))
BUMP_VERSION="${major}.${minor}.${patch}-SNAPSHOT"

printf 'version: %s\n' "${BUMP_VERSION}" >"${APPLICATION_SPEC}"
git add "${APPLICATION_SPEC}"
git commit -m "chore: release version ${RELEASE_VERSION} and bump version to ${BUMP_VERSION}"

git fetch origin "${BRANCH}"
git rebase "origin/${BRANCH}"
git push origin "HEAD:${BRANCH}"
