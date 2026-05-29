# Run Guide — DICOMweb-on-CUBE deployment

This guide stands up the full spike on a single Docker host and exercises the
QIDO-RS / WADO-RS / STOW-RS endpoints, starting from nothing. It is designed to
run on **any** machine via a self-contained virtualenv — you do not need Ansible,
the Docker Python SDK, or miniChRIS pre-installed; `bootstrap.sh` provisions all
of that locally.

> For *what* this deploys and the architecture behind it, see
> `ansible/README.md` and `../proposal/RESEARCH_TICKET_OUTPUT.md`.
> For the evidence that it works, see `VALIDATION_REPORT.md`.

---

## 1. Prerequisites (host)

These must already be on the control host (the machine you run the commands on);
`bootstrap.sh` does **not** install them, and the `prereqs` Ansible role fails
early with a clear message if any is missing:

| Requirement | Why | Check |
|---|---|---|
| **Docker Engine**, daemon running | runs CUBE/oxidicom/Orthanc | `docker info` |
| Your user can talk to Docker | no `sudo` needed in the play | `docker ps` (no error) |
| **`docker compose` v2.6+** plugin | miniChRIS needs Compose v2 | `docker compose version` |
| **python3 ≥ 3.9** with `venv` | builds the control venv | `python3 -m venv --help` |
| **git, curl, unzip** | vendoring + sample data + smoke tests | `git --version` etc. |

Everything else (Ansible, the `community.docker` collection, the Docker Python
SDK, and the pinned miniChRIS-docker stack) is provisioned locally by the
bootstrap step into `operations/.venv`, `operations/.ansible`, and
`operations/vendor/` — none of it touches your system Python or `~/.ansible`.

Disk: the first run pulls several GB of images (CUBE, oxidicom, Orthanc,
Postgres, RabbitMQ, NATS, pfdcm/pfcon/pman). Allow ~10 GB free.

---

## 2. Bootstrap (once)

From the bundle's `operations/` directory:

```bash
tooling/bootstrap.sh
```

This:
1. creates the virtualenv `operations/.venv` and installs the pinned
   control-host deps (`ansible-core`, the Docker SDK) from
   `tooling/requirements.txt`;
2. installs the `community.docker` collection into `operations/.ansible/collections`;
3. vendors `FNNDSC/miniChRIS-docker` (pinned commit `4d689ba`) into
   `operations/vendor/miniChRIS-docker`.

**Air-gapped / offline:** point bootstrap at a local copy of miniChRIS-docker
instead of cloning from GitHub:

```bash
MINICHRIS_SRC=/path/to/miniChRIS-docker tooling/bootstrap.sh
```

Bootstrap is idempotent — re-running it is safe and fast.

---

## 3. Deploy + smoke-test (one command)

```bash
./run.sh
```

`run.sh` runs the full playbook through the venv (pinning the module interpreter
so the Docker SDK resolves) with the **L2 DICOMweb overlay enabled by default**,
in six phases:

1. **prereqs** — assert Docker + Compose + the collection + the SDK.
2. **minichris** — bring up the vendored miniChRIS stack (`pacs` profile: CUBE +
   compute + oxidicom + NATS + bundled Orthanc + pfdcm), wait for CUBE health.
3. **orthanc** — run a separate `orthancteam/orthanc` test PACS (DicomWeb+REST)
   on non-colliding ports `8142`/`4342`.
4. **sample_data** — download a small public DICOM set, load it into the test
   Orthanc, and C-STORE-push it to oxidicom (AET `ChRIS`) so CUBE ingests it.
5. **dicomweb_app** — overlay the L2 QIDO/WADO/STOW code into the running
   `chris` + `worker` containers, install `pydicom`, wire `INSTALLED_APPS`+urls,
   migrate, and restart.
6. **verify** — run `ansible/scripts/smoke.sh` (PASS/FAIL/SKIP), then
   automatically run the deeper API suite `tooling/api_tests.sh` (QIDO/WADO/STOW
   + Basic & Token auth + negatives + full-volume data-integrity checks). Both
   gate the deploy: a hard failure in either fails the play.

The **first** run pulls images and migrates — allow several minutes. Subsequent
runs are fast and idempotent.

### Common variations

```bash
# Deploy WITHOUT the L2 overlay (DICOMweb checks report SKIP, not FAIL):
DICOMWEB_OVERLAY=false ./run.sh

# Re-run only the smoke tests:
./run.sh --tags verify

# (Re)bring-up just the stack:
./run.sh --tags minichris

# Reload sample data:
./run.sh --tags sample_data

# Use your own dataset instead of the downloaded sample:
./run.sh -e sample_data_mode=local_dir -e sample_data_local_dir=/path/to/dicoms
```

---

## 4. Verify by hand

```bash
# CUBE liveness
curl -u chris:chris1234 http://localhost:8000/api/v1/

# what CUBE ingested (PACS series)
curl -u chris:chris1234 -H 'Accept: application/json' \
  http://localhost:8000/api/v1/pacs/series/

# CUBE QIDO-RS — list studies (DICOM JSON Model).
# NOTE: the PACS id is the *calling* AET of the pusher — here the test Orthanc's
# DicomAet = SPIKEORTHANC (NOT oxidicom's own SCP AET "ChRIS").
curl -u chris:chris1234 -H 'Accept: application/dicom+json' \
  http://localhost:8000/dicom-web/pacs/SPIKEORTHANC/studies

# CUBE WADO-RS — study metadata for a returned StudyInstanceUID
curl -u chris:chris1234 -H 'Accept: application/dicom+json' \
  http://localhost:8000/dicom-web/pacs/SPIKEORTHANC/studies/<StudyInstanceUID>/metadata

# Token-auth path (instead of Basic): get a token, then send it as a header
TOK=$(curl -s -X POST -H 'Content-Type: application/json' \
  --data '{"username":"chris","password":"chris1234"}' \
  http://localhost:8000/api/v1/auth-token/ | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -H "Authorization: Token $TOK" -H 'Accept: application/dicom+json' \
  http://localhost:8000/dicom-web/pacs/SPIKEORTHANC/studies

# test Orthanc speaks DICOMweb (source-PACS sanity)
curl -H 'Accept: application/dicom+json' http://localhost:8142/dicom-web/studies
```

Or re-run the scripted checks directly (the same ones the deploy runs):

```bash
# basic smoke (PASS/FAIL/SKIP)
CUBE_USER=chris CUBE_PASSWORD=chris1234 EXPECT_DICOMWEB=true \
  PACS_ID=SPIKEORTHANC ansible/scripts/smoke.sh

# full API suite: QIDO/WADO/STOW + Basic & Token auth + negatives +
# data-integrity (whole 3D volume: counts match the source, no missing slice)
PACS_ID=SPIKEORTHANC ORTHANC_BASE_URL=http://localhost:8142 \
  tooling/api_tests.sh
```

---

## 5. Service map

| Service | Host port(s) | Notes |
|---|---|---|
| CUBE API + `/dicom-web/…` | `8000` | DICOMweb L2 endpoints attach here |
| ChRIS_ui | `8020` | |
| oxidicom C-STORE SCP | `11111` | AET `ChRIS` |
| NATS / RabbitMQ | `4222` / `5672` | progress bus / Celery |
| Bundled Orthanc | `4242` / `8042` | started by the `pacs` profile |
| pfdcm / pfcon / pman | `4005` / `5005` / `5010` | |
| **Test Orthanc** | **`8142` / `4342`** | sample-data source, AET `SPIKEORTHANC` |

Credentials are the public miniChRIS dev defaults (`chris:chris1234`) — **demo
only**, not production.

---

## 6. Teardown

```bash
tooling/teardown.sh                 # remove test Orthanc + miniChRIS (down -v)
KEEP_VOLUMES=1 tooling/teardown.sh  # keep miniChRIS volumes
```

To also reclaim the control-host artifacts, delete `operations/.venv`,
`operations/.ansible`, and `operations/vendor/` (all regenerated by bootstrap).

---

## 7. Clean-room reproducibility test (fresh Ubuntu VM)

To prove the deployment runs on a machine with **nothing** pre-installed (no
Ansible, Docker SDK, or miniChRIS — only the documented host prereqs), the
deliverable ships a one-command clean-room harness that provisions a throwaway
Ubuntu 24.04 KVM, installs just the prereqs via cloud-init, copies this
deliverable in, and runs the real entrypoints (`bootstrap.sh` then `run.sh`)
inside the guest — exactly what a client would do:

```bash
operations/tooling/cleanroom_kvm.sh           # provision -> deploy -> validate -> destroy
operations/tooling/cleanroom_kvm.sh --keep    # leave the VM up afterwards (prints ssh cmd)
operations/tooling/cleanroom_kvm.sh --destroy # tear down a previous --keep run
```

**Host (hypervisor) requirements:** KVM (`/dev/kvm`), libvirt + `virt-install`,
`cloud-image-utils` (`cloud-localds`), `qemu-img`, and passwordless `sudo` for
the libvirt image dir; the libvirt `default` NAT network. Install on Ubuntu with:
`sudo apt install -y qemu-kvm libvirt-daemon-system virtinst cloud-image-utils`.

The guest pulls all container images fresh over NAT, so allow time + bandwidth.
The harness exits non-zero if the in-guest deploy or validation fails, so it
doubles as a CI-style portability gate. Tunables via env: `VM_NAME`, `VM_RAM_MB`
(default 6144), `VM_VCPUS` (4), `VM_DISK_GB` (40), `UBUNTU_RELEASE` (noble).

---

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `prereqs` fails on Docker daemon | Daemon down or your user not in the `docker` group. Start Docker / add yourself to the group, or run with `--become`. |
| `prereqs` fails on the Docker SDK | The venv wasn't used. Always launch via `./run.sh` (it pins the interpreter to `operations/.venv`); don't call `ansible-playbook` directly. |
| `minichris … submodule not found` | Bootstrap didn't vendor miniChRIS. Re-run `tooling/bootstrap.sh` (set `MINICHRIS_SRC` if offline). |
| CUBE health wait times out | First-boot image pulls + migrations can exceed the budget on a slow link. Re-run `./run.sh` — pulls are cached and it resumes. |
| 0 series ingested | The C-STORE push is async; re-run `./run.sh --tags verify` after a few seconds. Never TCP-probe oxidicom's `:11111` (it panics on a bare connect — use C-ECHO). |
| QIDO/WADO report `SKIP` | The overlay wasn't applied. Run the default `./run.sh` (overlay on) rather than `DICOMWEB_OVERLAY=false`. |
| Port already in use (`8000`) | Override the CUBE host port: `./run.sh -e cube_host_port=8001`. |
