#!/usr/bin/env bash
# =============================================================================
# cleanroom_kvm.sh -- prove the deployment "runs anywhere" on a FRESH Ubuntu VM.
#
# Provisions a clean Ubuntu cloud-image KVM (only the documented host prereqs:
# docker, the compose v2 plugin, python3-venv, git, curl, unzip -- NO Ansible,
# Docker SDK, or miniChRIS pre-installed), copies this deliverable into it, and
# runs the real entrypoints inside the guest:
#       operations/tooling/bootstrap.sh   &&   operations/run.sh
# i.e. exactly what a client would do. It then reports the smoke + API results.
#
#   operations/tooling/cleanroom_kvm.sh            # provision, deploy, validate, destroy
#   operations/tooling/cleanroom_kvm.sh --keep     # leave the VM running afterwards
#   operations/tooling/cleanroom_kvm.sh --destroy  # just tear down a previous run
#
# Requirements on the HOST (the hypervisor): KVM (/dev/kvm), libvirt + virt-install,
# cloud-image-utils (cloud-localds), qemu-img, and passwordless sudo for the
# libvirt image dir. Uses qemu:///system + the libvirt `default` NAT network.
# The guest pulls all container images fresh over NAT -- allow time + bandwidth.
# =============================================================================
set -euo pipefail

NAME="${VM_NAME:-dicomweb-cleanroom}"
RAM_MB="${VM_RAM_MB:-6144}"
VCPUS="${VM_VCPUS:-4}"
DISK_GB="${VM_DISK_GB:-40}"
RELEASE="${UBUNTU_RELEASE:-noble}"
IMG_DIR="/var/lib/libvirt/images"
BASE="${IMG_DIR}/${RELEASE}-cloudimg-amd64.img"
DISK="${IMG_DIR}/${NAME}.qcow2"
SEED="${IMG_DIR}/${NAME}-seed.iso"
NET="${LIBVIRT_NET:-default}"
WORK="$(mktemp -d /tmp/cleanroom.XXXXXX)"
TOOLING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_ROOT="$(cd "${TOOLING_DIR}/../.." && pwd)"   # .../client-deliverable
SSH_KEY="${WORK}/id_vm"
SSH="ssh -i ${SSH_KEY} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10"

log()  { printf '\033[36m[cleanroom]\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m[  ok    ]\033[0m %s\n' "$*"; }
die()  { printf '\033[31m[ fatal  ]\033[0m %s\n' "$*" >&2; exit 1; }

destroy() {
  log "destroying VM ${NAME} + disks"
  virsh destroy "${NAME}" >/dev/null 2>&1 || true
  virsh undefine "${NAME}" --nvram >/dev/null 2>&1 || true
  sudo rm -f "${DISK}" "${SEED}"
}

[ "${1:-}" = "--destroy" ] && { destroy; ok "destroyed."; exit 0; }
KEEP=0; [ "${1:-}" = "--keep" ] && KEEP=1

# --- preflight --------------------------------------------------------------
[ -e /dev/kvm ] || die "/dev/kvm missing -- this host has no KVM."
for t in virsh virt-install cloud-localds qemu-img ssh scp; do command -v "$t" >/dev/null || die "missing host tool: $t"; done
sudo -n true 2>/dev/null || die "passwordless sudo required for the libvirt image dir."
virsh net-info "${NET}" >/dev/null 2>&1 || die "libvirt network '${NET}' not found."

# --- clean any prior run ----------------------------------------------------
virsh dominfo "${NAME}" >/dev/null 2>&1 && destroy

# --- base image -------------------------------------------------------------
if ! sudo test -f "${BASE}"; then
  log "downloading ${RELEASE} cloud image -> ${BASE}"
  sudo curl -fSL -o "${BASE}" \
    "https://cloud-images.ubuntu.com/${RELEASE}/current/${RELEASE}-server-cloudimg-amd64.img"
fi
ok "base image: ${BASE}"

# --- ssh key + cloud-init ---------------------------------------------------
ssh-keygen -t ed25519 -N '' -f "${SSH_KEY}" -q
PUB="$(cat "${SSH_KEY}.pub")"
cat > "${WORK}/meta-data" <<EOF
instance-id: ${NAME}-01
local-hostname: ${NAME}
EOF
cat > "${WORK}/user-data" <<EOF
#cloud-config
hostname: ${NAME}
users:
  - name: ubuntu
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
      - ${PUB}
package_update: true
packages: [docker.io, docker-compose-v2, python3-venv, python3-pip, git, curl, unzip]
runcmd:
  - usermod -aG docker ubuntu
  - systemctl enable --now docker
  - touch /var/lib/cloud/instance/PREREQS_DONE
EOF
cloud-localds "${WORK}/seed.iso" "${WORK}/user-data" "${WORK}/meta-data"
sudo cp "${WORK}/seed.iso" "${SEED}"

# --- disk overlay + boot ----------------------------------------------------
log "creating ${DISK_GB}G overlay + importing VM (${VCPUS} vcpu, ${RAM_MB}MB)"
sudo qemu-img create -f qcow2 -F qcow2 -b "${BASE}" "${DISK}" "${DISK_GB}G" >/dev/null
virt-install --name "${NAME}" --memory "${RAM_MB}" --vcpus "${VCPUS}" \
  --disk path="${DISK}",format=qcow2,bus=virtio \
  --disk path="${SEED}",device=cdrom \
  --os-variant detect=on,require=off --import --network network="${NET}",model=virtio \
  --graphics none --noautoconsole >/dev/null
ok "VM ${NAME} defined + booting"

# --- wait for IP + ssh ------------------------------------------------------
log "waiting for guest IP (NAT lease)…"
IP=""; for i in $(seq 1 60); do
  IP="$(virsh -q domifaddr "${NAME}" 2>/dev/null | awk '/ipv4/{print $4}' | cut -d/ -f1 | head -1)"
  [ -n "$IP" ] && break; sleep 5
done
[ -n "$IP" ] || die "guest never got an IP."
ok "guest IP: ${IP}"
log "waiting for ssh + cloud-init (package install) to finish…"
for i in $(seq 1 60); do $SSH ubuntu@"$IP" true 2>/dev/null && break; sleep 5; done
$SSH ubuntu@"$IP" 'cloud-init status --wait' >/dev/null 2>&1 || true
$SSH ubuntu@"$IP" 'test -f /var/lib/cloud/instance/PREREQS_DONE' || die "cloud-init prereqs did not complete."
ok "guest prereqs installed: $($SSH ubuntu@"$IP" 'docker --version; docker compose version --short' 2>/dev/null | tr "\n" " ")"

# --- ship the deliverable ---------------------------------------------------
log "packaging deliverable (operations + implementation/dicomweb-l2 + proposal)"
TARBALL="${WORK}/deliverable.tgz"
tar -C "$(dirname "${BUNDLE_ROOT}")" \
  --exclude='operations/.venv' --exclude='operations/.ansible' \
  --exclude='operations/vendor' --exclude='operations/.logs' \
  --exclude='**/__pycache__' \
  -czf "${TARBALL}" "$(basename "${BUNDLE_ROOT}")"
$SSH ubuntu@"$IP" 'mkdir -p ~/deliverable'
scp -i "${SSH_KEY}" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  "${TARBALL}" ubuntu@"$IP":~/deliverable.tgz >/dev/null
$SSH ubuntu@"$IP" 'tar -xzf ~/deliverable.tgz -C ~/deliverable --strip-components=1'
ok "deliverable transferred + extracted"

# --- run the real entrypoints in the guest ---------------------------------
log "running bootstrap.sh in the guest (venv + collection + vendored miniChRIS)…"
$SSH ubuntu@"$IP" 'cd ~/deliverable/operations && tooling/bootstrap.sh' \
  || die "bootstrap failed in guest"
log "running run.sh in the guest (deploy + auto smoke + API validation)…"
set +e
$SSH ubuntu@"$IP" 'cd ~/deliverable/operations && sg docker -c ./run.sh'
RUN_RC=$?
set -e

echo "=============================================================================="
if [ "$RUN_RC" = 0 ]; then ok "CLEAN-ROOM DEPLOY + VALIDATION PASSED (exit 0) on a fresh ${RELEASE} VM"; else
  printf '\033[31m[ FAIL ]\033[0m run.sh exited %s in the guest\n' "$RUN_RC"; fi
echo " VM: ${NAME} (${IP})   disk: ${DISK}"
echo "=============================================================================="

if [ "$KEEP" = 1 ]; then
  # Persist the ssh key next to the disk so the kept VM stays reachable.
  KEPT_KEY="${HOME}/.cache/${NAME}-id_vm"; mkdir -p "$(dirname "${KEPT_KEY}")"; cp "${SSH_KEY}" "${KEPT_KEY}"; chmod 600 "${KEPT_KEY}"
  log "--keep set; VM left running at ${IP}. ssh: ssh -i ${KEPT_KEY} ubuntu@${IP}"
else
  destroy
fi
rm -rf "${WORK}"
exit "$RUN_RC"
