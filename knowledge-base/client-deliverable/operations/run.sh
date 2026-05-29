#!/usr/bin/env bash
# =============================================================================
# run.sh -- stand up the DICOMweb-on-CUBE spike and run the smoke tests.
#
# Wraps `ansible-playbook` so the deploy uses the self-contained virtualenv and
# collection path created by tooling/bootstrap.sh, on any machine. It:
#   - uses operations/.venv as the Ansible runtime AND the module interpreter
#     (so the docker SDK installed there is the one community.docker imports --
#     this overrides the inventory's `auto_silent` discovery),
#   - points Ansible at the bundle-local collection path,
#   - enables the L2 DICOMweb overlay by default so QIDO/WADO/STOW are live and
#     get exercised by the verify role (set DICOMWEB_OVERLAY=false to skip).
#
# Usage:
#   operations/run.sh                       # full deploy + smoke test
#   operations/run.sh --tags verify         # just re-run the smoke tests
#   operations/run.sh --tags minichris      # just (re)bring-up the stack
#   DICOMWEB_OVERLAY=false operations/run.sh # deploy without the L2 overlay
#   operations/run.sh -e sample_data_mode=local_dir -e sample_data_local_dir=/data
#
# Any extra args are passed through to ansible-playbook verbatim.
# =============================================================================
set -euo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${OPS_DIR}/.venv"
COLLECTIONS_DIR="${OPS_DIR}/.ansible/collections"
ANSIBLE_DIR="${OPS_DIR}/ansible"
VENDOR_DIR="${OPS_DIR}/vendor/miniChRIS-docker"

DICOMWEB_OVERLAY="${DICOMWEB_OVERLAY:-true}"

die() { printf '\033[31m[run] %s\033[0m\n' "$*" >&2; exit 1; }

[ -x "${VENV_DIR}/bin/ansible-playbook" ] || \
  die "virtualenv not found. Run operations/tooling/bootstrap.sh first."
[ -f "${VENDOR_DIR}/docker-compose.yml" ] || \
  die "miniChRIS-docker not vendored. Run operations/tooling/bootstrap.sh first."

export ANSIBLE_COLLECTIONS_PATH="${COLLECTIONS_DIR}"
# Keep all Ansible runtime state inside the bundle (no ~/.ansible writes).
export ANSIBLE_HOME="${OPS_DIR}/.ansible"
export ANSIBLE_LOCAL_TEMP="${OPS_DIR}/.ansible/tmp"

cd "${ANSIBLE_DIR}"
exec "${VENV_DIR}/bin/ansible-playbook" \
  -i inventory.ini site.yml \
  -e "ansible_python_interpreter=${VENV_DIR}/bin/python" \
  -e "dicomweb_overlay_enabled=${DICOMWEB_OVERLAY}" \
  "$@"
