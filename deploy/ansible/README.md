# DICOMweb-on-CUBE spike — Ansible deployment

An Ansible-driven, one-command deployment that **wraps** the canonical ChRIS
backend stack ([`FNNDSC/miniChRIS-docker`](https://github.com/FNNDSC/miniChRIS-docker))
and layers the DICOMweb (QIDO-RS / WADO-RS / STOW-RS) spike on top of it.

The playbook:

1. **Asserts prerequisites** (Docker daemon, Compose v2.6+, the
   `community.docker` collection, the Docker Python SDK).
2. **Brings up the vendored miniChRIS-docker** (a pinned git submodule at
   `deploy/vendor/miniChRIS-docker`, run in place) with the `pacs` profile (CUBE +
   compute + the DICOM ingest pipeline: `oxidicom` + `nats` + the bundled
   Orthanc + `pfdcm`), then waits for CUBE to answer on `/api/v1/`.
3. **Runs a separate test Orthanc** (`orthancteam/orthanc`) with the **DicomWeb
   + REST** plugins, on non-colliding host ports, as a sample-data source.
4. **Loads sample DICOM** into the test Orthanc and **C-STORE-pushes** it to
   `oxidicom` (AET `ChRIS`, TCP `11111`), so CUBE ingests it the normal way.
5. **Overlays the L2 DICOMweb code** (`../../implementation/dicomweb-l2/`) into
   the running CUBE container — **the integration seam** (disabled by default
   until that code exists; see *The integration seam* below).
6. **Smoke-tests** the core CUBE endpoints, the test Orthanc, and the
   QIDO/WADO/STOW endpoints, reporting `PASS` / `FAIL` / `SKIP`.

> ⚠️ This wraps the **demo-grade** miniChRIS stack, which upstream explicitly
> calls *"not suitable for production"* — it ships hard-coded secrets
> (`chris:chris1234`, `DJANGO_SECRET_KEY=secret`) and insecure defaults
> (<https://github.com/FNNDSC/miniChRIS-docker#readme>). This deployment is for
> the spike / demo only.

---

## What comes up

| Service | Source | Host port(s) | Notes |
|---|---|---|---|
| CUBE API + `/chris-admin/` | miniChRIS `chris` (`ghcr.io/fnndsc/cube:6.11.0`) | `8000` | DICOMweb L2 endpoints attach here |
| ChRIS_ui frontend | miniChRIS `chris_ui` | `8020` | |
| oxidicom C-STORE SCP | miniChRIS `oxidicom` (`3.0.0`) | `11111` | DICOM receiver, AET `ChRIS` |
| NATS | miniChRIS `nats` (`2.11.4-alpine`) | `4222` | oxidicom progress bus (LONK) |
| RabbitMQ | miniChRIS `queue` | `5672` | Celery + oxidicom jobs |
| Bundled Orthanc | miniChRIS `orthanc` (`jodogne/orthanc-plugins:1.12.7`) | `4242`, `8042` | started by the `pacs` profile |
| pfdcm | miniChRIS `pfdcm` | `4005` | |
| pfcon / pman | miniChRIS | `5005` / `5010` | compute plane |
| **Test Orthanc** | **this deploy** (`orthancteam/orthanc`) | **`8142` (HTTP/DICOMweb), `4342` (DIMSE)** | sample-data source, AET `SPIKEORTHANC` |

Ports/images for the wrapped stack are transcribed from upstream
`docker-compose.yml` and pinned in `group_vars/all.yml`.

---

## Prerequisites

- **Docker Engine** with the daemon running, and your user able to talk to it
  (member of the `docker` group, or run the playbook with `--become`).
- **Docker Compose v2.6+** (the `docker compose` plugin, not the legacy
  `docker-compose` binary). Required by miniChRIS-docker.
- **Ansible** (`ansible-core` 2.14+).
- **`community.docker`** Ansible collection (provides `docker_container`,
  `docker_network_info`):

  ```bash
  ansible-galaxy collection install -r requirements.yml
  ```

- **Docker SDK for Python** in the interpreter Ansible uses:

  ```bash
  pip install 'docker>=6'
  ```

- Standard CLI tools used by the smoke script: `curl` (and optionally `jq`).
- **DCMTK** (`storescu`) is *optional* — only needed if you want to C-STORE
  straight into oxidicom bypassing Orthanc.

The `prereqs` role asserts all of the above and fails early with a clear
message if something is missing.

---

## One-command run

miniChRIS-docker is a pinned git submodule, so initialize it once (only needed
if the repo was cloned without `--recurse-submodules`):

```bash
git submodule update --init deploy/vendor/miniChRIS-docker   # from repo root, once
```

Then, from this directory (`deploy/ansible/`):

```bash
ansible-galaxy collection install -r requirements.yml   # once
ansible-playbook -i inventory.ini site.yml
```

First run pulls a lot of images and runs CUBE migrations; allow a few minutes.
Subsequent runs are fast and idempotent (re-`up` is a no-op for running
services; uploads/pushes are safe to repeat).

Run a single phase with tags:

```bash
ansible-playbook -i inventory.ini site.yml --tags minichris   # just the stack
ansible-playbook -i inventory.ini site.yml --tags sample_data # reload data
ansible-playbook -i inventory.ini site.yml --tags verify      # just smoke tests
```

---

## How to verify

The `verify` role runs `scripts/smoke.sh`, which you can also run by hand:

```bash
CUBE_USER=chris CUBE_PASSWORD=chris1234 ./scripts/smoke.sh
```

It checks, with `PASS` / `FAIL` / `SKIP` per line:

- CUBE `/api/v1/` (basic auth) and `/api/v1/auth-token/` (token auth).
- CUBE `/api/v1/pacs/` and `/api/v1/pacs/series/` — and whether the sample data
  has been ingested (≥1 `SeriesInstanceUID`).
- Test Orthanc `/system`, DicomWeb plugin loaded, and Orthanc's own QIDO-RS
  (`/dicom-web/studies`) as a sanity check that the source PACS speaks DICOMweb.
- **CUBE QIDO-RS / WADO-RS / STOW-RS** under `/dicom-web/pacs/<id>/…`. These are
  reported `SKIP` until the L2 overlay is applied (`EXPECT_DICOMWEB=true`),
  then exercised:
  - QIDO-RS `GET /dicom-web/pacs/ChRIS/studies` → `200`/`204`
    (`Accept: application/dicom+json`).
  - WADO-RS `GET /dicom-web/pacs/ChRIS/studies/<uid>/metadata` → `200`.
  - STOW-RS `POST /dicom-web/pacs/ChRIS/studies` present (rejects an empty body
    with `400`/`415` rather than `404`).

Endpoint shapes follow DICOM PS3.18: QIDO-RS §10.6, WADO-RS §10.4, STOW-RS §10.5
(<https://dicom.nema.org/medical/dicom/current/output/html/part18.html>).
The `<id>` in the path is the CUBE `PACS.identifier`, which for oxidicom-ingested
data equals `OXIDICOM_SCP_AET` = `ChRIS`.

Manual spot checks:

```bash
# CUBE liveness
curl -u chris:chris1234 http://localhost:8000/api/v1/

# what CUBE ingested
curl -u chris:chris1234 -H 'Accept: application/json' \
  http://localhost:8000/api/v1/pacs/series/

# test Orthanc speaks DICOMweb
curl -H 'Accept: application/dicom+json' \
  http://localhost:8142/dicom-web/studies
```

---

## The integration seam (`roles/dicomweb_app`)

CUBE runs as the prebuilt image `ghcr.io/fnndsc/cube:6.11.0`; our L2 DICOMweb
code is **not** in that image. The `dicomweb_app` role is the explicit seam
where L2 code is overlaid into the **running** container so it can be exercised
without rebuilding the image:

1. `docker cp ../../implementation/dicomweb-l2/.` into the CUBE app dir
   (`/home/localuser/chris_backend/dicomweb/`) of both the `chris` and `worker`
   containers (the worker runs the Phase-A Celery indexer).
2. `docker compose exec chris python manage.py migrate --noinput` — creates the
   `PACSStudy` model, enables `pg_trgm`, applies the `dicomweb` app migrations.
3. Restart `chris` + `worker` (a handler) so Django re-imports `urls.py` /
   `models.py` / `tasks.py`, then re-wait for CUBE health.

This is a **dev overlay**, not a production deploy. Production would rebuild the
CUBE image with the `dicomweb` app baked in (or ship it as an installable
dependency). The role is **disabled by default** (`dicomweb_overlay_enabled:
false` in `group_vars/all.yml`) because `implementation/dicomweb-l2/` is Phase
B/C work that does not exist yet — when disabled, the role prints exactly what
it *would* do (so it is meeting-demonstrable), and the smoke test reports the
DICOMweb checks as `SKIP`. Flip the toggle once the L2 code is in place:

```bash
ansible-playbook -i inventory.ini site.yml \
  -e dicomweb_overlay_enabled=true
```

Why this seam matters architecturally: per the spike's L2 decision record
(`../../knowledge-base/08-l2-architecture-decisions.md`), the recommended
architecture (option **C**, hybrid) keeps the QIDO/WADO/STOW **endpoints in
Django/CUBE** (for the existing auth chain) while a NATS consumer indexes
oxidicom-parsed tags; the Celery indexer shipped in Phase A is the fallback for
non-oxidicom ingestion paths (including STOW-RS). All of that code lands at this
same seam — inside the `chris`/`worker` containers — which is why the overlay
targets both services.

---

## Sample data (configurable)

`group_vars/all.yml` → `sample_data_mode`:

| Mode | Behaviour |
|---|---|
| `download` (default) | Fetch a public DICOM zip (`sample_data_url`) into the workdir, unpack, upload all instances to the test Orthanc. |
| `local_dir` | Upload every file under `sample_data_local_dir` (point this at the **BCH dataset**). |
| `none` | Skip loading (assume Orthanc already populated). |

After loading, the role lists the studies in Orthanc and C-STORE-pushes each to
the `ChRIS` modality (= oxidicom), driving the normal CUBE ingest path
(`oxidicom` → `/data` → RabbitMQ → CUBE worker → `PACSSeries` rows). Disable the
push with `sample_data_push_to_cube: false`.

To use the BCH dataset:

```bash
ansible-playbook -i inventory.ini site.yml \
  -e sample_data_mode=local_dir \
  -e sample_data_local_dir=/path/to/bch/dicoms
```

---

## Teardown

```bash
./scripts/teardown.sh                 # remove test Orthanc + miniChRIS (down -v)
KEEP_VOLUMES=1 ./scripts/teardown.sh  # keep miniChRIS volumes
```

`teardown.sh` removes the separate test-Orthanc container, then invokes the
upstream `unmake.sh` (which reaps pman-launched plugin containers and does
`docker compose ... down -v` across all profiles — **destroying named volumes**,
consistent with the ephemeral design).

---

## Files

```
deploy/
├── vendor/miniChRIS-docker/  # pinned git submodule (the stack we wrap)
└── ansible/
    ├── README.md                 # this file
    ├── ansible.cfg               # inventory/roles paths, yaml-format stdout
    ├── requirements.yml          # community.docker collection
    ├── inventory.ini             # localhost, connection=local
    ├── group_vars/all.yml        # pinned versions, ports, URLs, paths, toggles
    ├── site.yml                  # orchestrates the roles
    ├── scripts/
    │   ├── smoke.sh              # QIDO/WADO/STOW + core verification (pass/fail/skip)
    │   └── teardown.sh           # remove test Orthanc + miniChRIS down -v
    └── roles/
        ├── prereqs/              # assert Docker + Compose + collection + SDK
        ├── minichris/            # bring up vendored submodule (pacs profile) + wait for CUBE
        ├── orthanc/              # run orthancteam/orthanc test PACS (DicomWeb+REST)
        ├── sample_data/          # load DICOM into Orthanc, push to oxidicom
        ├── dicomweb_app/         # overlay L2 code into running CUBE (the seam)
        └── verify/               # run scripts/smoke.sh
```

---

## Caveats (not fully validatable without running it)

These are honest limits of a spec written ahead of an actual run on the target
host; verify them live before the demo.

1. **Not executed in this environment.** These files were authored from the
   upstream `docker-compose.yml`/scripts and the spike knowledge base; the full
   `ansible-playbook` run has **not** been executed here. Expect to iterate on
   small details (image-tag availability, the exact sample dataset) on first run.

2. **`orthancteam/orthanc` image tag.** Pinned to `24.10.3` in
   `group_vars/all.yml`. The orthancteam image bundles the DicomWeb + REST
   plugins and reads JSON config from `/etc/orthanc/`. If that exact tag is
   unavailable, bump `test_orthanc_image`. (Docs:
   <https://orthanc.uclouvain.be/book/plugins/dicomweb.html>.)

3. **CUBE container app path.** The overlay assumes the Django project lives at
   `/home/localuser/chris_backend` inside `cube:6.11.0`. Confirm with
   `docker compose exec chris sh -c 'pwd; ls'` and adjust
   `dicomweb_container_app_dir` if the image layout differs.

4. **The test Orthanc joining `minichris-local`.** It attaches to the
   `minichris-local` network so it can resolve `oxidicom` by name for the
   C-STORE push. The network name assumes `COMPOSE_PROJECT_NAME=minichris` and
   the upstream `name: minichris-local` override. If you change the project
   name, update `minichris_local_network`.

5. **Sample dataset shape.** The default download is a small public structural
   MRI set; real `.dcm` filenames vary and some archives ship extensionless
   DICOM, so the role over-matches then lets Orthanc reject non-DICOM with a
   `400`. For the demo, prefer `sample_data_mode=local_dir` with a known-good
   directory.

6. **L2 endpoints are SKIP until the overlay is enabled.** With
   `dicomweb_overlay_enabled: false` (default), the QIDO/WADO/STOW smoke checks
   report `SKIP`, not `PASS` — the endpoints genuinely don't exist yet. This is
   expected: the L2 view layer (Phase C) is the work being scoped, not work this
   deployment ships.

7. **oxidicom `OXIDICOM_DEV_SLEEP=150ms`** is a demo throttle baked into the
   wrapped stack — ingest is intentionally slow so progress is visible in
   ChRIS_ui. Drop it for any throughput measurement on the BCH dataset.

8. **Volume ownership.** The wrapped stack relies on its own
   `*-nonroot-user-volume-fix` init containers to make `/data` group-writable
   for uid 1001; we do not manage that volume externally, so this is handled by
   the compose stack itself. Externalizing `chris_files` would require
   reproducing that `chmod g+rwx`.
```
