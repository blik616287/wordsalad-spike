#!/usr/bin/env bash
# =============================================================================
# teardown.sh -- tear down the spike deployment.
#
# Removes the separate test Orthanc container, then tears down the wrapped
# miniChRIS-docker stack (all profiles, with volumes) using the upstream
# unmake.sh semantics.
#
# Usage:
#   tooling/teardown.sh            # remove test orthanc + miniChRIS (down -v)
#   KEEP_VOLUMES=1 tooling/teardown.sh   # leave miniChRIS volumes in place
# =============================================================================
set -uo pipefail

WORKDIR="${DEPLOY_WORKDIR:-${HOME}/.cache/dicomweb-spike}"
# miniChRIS-docker is vendored at operations/vendor/miniChRIS-docker,
# i.e. ../vendor/miniChRIS-docker relative to this script (operations/tooling/).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
MINICHRIS_DIR="${MINICHRIS_DIR:-${SCRIPT_DIR}/../vendor/miniChRIS-docker}"
ORTHANC_NAME="${TEST_ORTHANC_CONTAINER_NAME:-dicomweb-spike-orthanc}"
PROJECT="${MINICHRIS_COMPOSE_PROJECT:-minichris}"

echo "-- removing test Orthanc container (${ORTHANC_NAME}) --"
docker rm -f "${ORTHANC_NAME}" 2>/dev/null || echo "   (not present)"

if [ -d "${MINICHRIS_DIR}" ]; then
  echo "-- tearing down miniChRIS-docker --"
  if [ -x "${MINICHRIS_DIR}/unmake.sh" ] && [ -z "${KEEP_VOLUMES:-}" ]; then
    # unmake.sh reaps pman-launched plugin containers AND does `down -v`.
    ( cd "${MINICHRIS_DIR}" && COMPOSE_PROJECT_NAME="${PROJECT}" ./unmake.sh )
  else
    down_args="--profile pacs --profile pflink --profile hasura down"
    [ -z "${KEEP_VOLUMES:-}" ] && down_args="${down_args} -v"
    ( cd "${MINICHRIS_DIR}" && COMPOSE_PROJECT_NAME="${PROJECT}" \
        docker compose ${down_args} --remove-orphans )
  fi
else
  echo "   miniChRIS dir not found at ${MINICHRIS_DIR}; nothing to tear down."
fi

echo "-- done --"
