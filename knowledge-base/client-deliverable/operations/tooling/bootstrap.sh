#!/usr/bin/env bash
# =============================================================================
# bootstrap.sh -- make the DICOMweb-on-CUBE deployment runnable on ANY machine.
#
# Idempotent. Run it once on the control host before ./run.sh:
#
#     operations/tooling/bootstrap.sh
#
# It sets up everything the deploy needs *on the control host* (the machine that
# drives the deploy); the CUBE/oxidicom/Orthanc services run in Docker.
#
#   1. Creates a self-contained Python virtualenv at operations/.venv and
#      installs the pinned control-host deps (ansible-core, docker SDK).
#   2. Installs the community.docker Ansible collection into a bundle-local
#      path (operations/.ansible/collections) so nothing leaks to ~/.ansible.
#   3. Vendors FNNDSC/miniChRIS-docker (pinned commit) into operations/vendor/
#      -- the stack the playbook wraps. Prefers $MINICHRIS_SRC (a local copy)
#      for offline/air-gapped installs, else clones from GitHub.
#
# Requirements assumed already present on the host (NOT installed here):
#   - Docker Engine with the daemon running + the `docker compose` v2 plugin
#   - python3 (>=3.9) with the venv module, git, curl, unzip
# The prereqs Ansible role re-checks Docker/Compose and fails early with a
# clear message if anything is missing.
# =============================================================================
set -euo pipefail

# --- locate ourselves (works regardless of cwd) ------------------------------
TOOLING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPS_DIR="$(cd "${TOOLING_DIR}/.." && pwd)"          # .../operations
VENV_DIR="${OPS_DIR}/.venv"
COLLECTIONS_DIR="${OPS_DIR}/.ansible/collections"
VENDOR_DIR="${OPS_DIR}/vendor/miniChRIS-docker"
REQUIREMENTS="${TOOLING_DIR}/requirements.txt"
GALAXY_REQS="${OPS_DIR}/ansible/requirements.yml"

# Pinned miniChRIS-docker version this deployment was validated against.
MINICHRIS_REPO="https://github.com/FNNDSC/miniChRIS-docker.git"
MINICHRIS_COMMIT="4d689ba7b221b55f0ed216ce8f2d3168974877c4"

log()  { printf '\033[36m[bootstrap]\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m[  ok    ]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[ warn   ]\033[0m %s\n' "$*"; }
die()  { printf '\033[31m[ fatal  ]\033[0m %s\n' "$*" >&2; exit 1; }

# --- 0. sanity: host tools ---------------------------------------------------
command -v python3 >/dev/null || die "python3 not found on PATH."
python3 -c 'import venv' 2>/dev/null || die "python3 venv module missing (apt install python3-venv)."
command -v git >/dev/null || die "git not found on PATH (needed to vendor miniChRIS-docker)."
command -v docker >/dev/null || warn "docker CLI not found -- the deploy will fail at the prereqs role until Docker is installed."

# --- 1. virtualenv + python deps ---------------------------------------------
if [ ! -x "${VENV_DIR}/bin/python" ]; then
  log "creating virtualenv at ${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
fi
log "installing control-host Python deps (ansible-core, docker SDK)"
"${VENV_DIR}/bin/python" -m pip install --quiet --upgrade pip
"${VENV_DIR}/bin/python" -m pip install --quiet -r "${REQUIREMENTS}"
ok "venv ready: $("${VENV_DIR}/bin/ansible" --version | head -1)"

# --- 2. ansible collection (bundle-local) ------------------------------------
log "installing community.docker collection into ${COLLECTIONS_DIR}"
ANSIBLE_COLLECTIONS_PATH="${COLLECTIONS_DIR}" \
  "${VENV_DIR}/bin/ansible-galaxy" collection install -r "${GALAXY_REQS}" \
  -p "${COLLECTIONS_DIR}" >/dev/null
ok "collection installed: $(ANSIBLE_COLLECTIONS_PATH=${COLLECTIONS_DIR} "${VENV_DIR}/bin/ansible-galaxy" collection list community.docker 2>/dev/null | awk '/community.docker/{print $2; exit}')"

# --- 3. vendor miniChRIS-docker (pinned) -------------------------------------
if [ -f "${VENDOR_DIR}/docker-compose.yml" ]; then
  ok "miniChRIS-docker already vendored at ${VENDOR_DIR}"
else
  mkdir -p "$(dirname "${VENDOR_DIR}")"
  if [ -n "${MINICHRIS_SRC:-}" ] && [ -f "${MINICHRIS_SRC}/docker-compose.yml" ]; then
    log "vendoring miniChRIS-docker from local source ${MINICHRIS_SRC}"
    # copy contents (including any .git so the pin is auditable), excluding runtime cruft
    cp -a "${MINICHRIS_SRC}/." "${VENDOR_DIR}/"
    if git -C "${VENDOR_DIR}" rev-parse --git-dir >/dev/null 2>&1; then
      git -C "${VENDOR_DIR}" checkout --quiet "${MINICHRIS_COMMIT}" 2>/dev/null || \
        warn "could not checkout pinned commit in copied tree (using as-is)"
    fi
    ok "vendored from local source"
  else
    log "cloning miniChRIS-docker @ ${MINICHRIS_COMMIT:0:7} from ${MINICHRIS_REPO}"
    git clone --quiet "${MINICHRIS_REPO}" "${VENDOR_DIR}"
    git -C "${VENDOR_DIR}" checkout --quiet "${MINICHRIS_COMMIT}"
    ok "cloned + pinned to ${MINICHRIS_COMMIT:0:7}"
  fi
fi
[ -f "${VENDOR_DIR}/docker-compose.yml" ] || die "vendoring failed: ${VENDOR_DIR}/docker-compose.yml missing."

cat <<EOF

$(ok "bootstrap complete.")
  venv         : ${VENV_DIR}
  collections  : ${COLLECTIONS_DIR}
  miniChRIS    : ${VENDOR_DIR} ($(git -C "${VENDOR_DIR}" rev-parse --short HEAD 2>/dev/null || echo 'no-git'))

Next: stand up the full stack and run the smoke tests with

    operations/run.sh

(add --check or pass through any ansible-playbook flags as needed).
EOF
