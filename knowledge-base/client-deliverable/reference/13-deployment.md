# 13 — The Ansible Deployment: standing up DICOMweb-on-CUBE

**Scope:** the `deploy/` tree — an Ansible-driven, one-command deployment that
**wraps** (never forks) the canonical ChRIS backend stack
[`FNNDSC/miniChRIS-docker`](https://github.com/FNNDSC/miniChRIS-docker) and
layers the L2 DICOMweb (QIDO-RS / WADO-RS / STOW-RS) spike on top of it. This
file is the operational companion to `03-minichris-docker.md` (the stack we
wrap) and `proposal/RESEARCH_TICKET_OUTPUT.md` (the architecture the overlay
realises). It documents the deploy as it actually runs, including six bugs found
and fixed by running it on a live host on 2026-05-28.

> [!CAUTION]
> This wraps the **demo-grade** miniChRIS stack, which upstream explicitly calls
> *"not suitable for production"* — hard-coded secrets (`chris:chris1234`,
> `DJANGO_SECRET_KEY=secret`) and insecure defaults
> (`deploy/ansible/README.md:25-29`). The deployment is for the spike / demo
> only. Production is a different path (Helm + a **baked** CUBE image; see §10).

---

## 1. The shape of the thing

```
deploy/
├── vendor/miniChRIS-docker/   # pinned git submodule (commit 4d689ba) -- the stack we wrap
└── ansible/
    ├── README.md              # operator guide (deploy/ansible/README.md)
    ├── ansible.cfg            # callbacks, roles_path, inventory
    ├── requirements.yml       # community.docker >= 3.4.0
    ├── inventory.ini          # localhost, connection=local
    ├── group_vars/all.yml     # SINGLE SOURCE OF TRUTH: pins, ports, URLs, toggles
    ├── site.yml               # orchestrates the six roles
    ├── compose-overrides/cube-port.yml   # remaps CUBE's host port (bug #4 fix)
    ├── scripts/{smoke.sh,teardown.sh}
    └── roles/
        ├── prereqs/      # assert Docker + Compose v2.6+ + collection + SDK
        ├── minichris/    # bring up the vendored submodule (pacs profile) + wait
        ├── orthanc/      # run a SEPARATE orthancteam/orthanc test PACS
        ├── sample_data/  # load DICOM into Orthanc, C-STORE-push to oxidicom
        ├── dicomweb_app/ # overlay the L2 code into the running CUBE (THE SEAM)
        └── verify/       # run scripts/smoke.sh (PASS/FAIL/SKIP)
```

`site.yml:23-45` runs the six roles in order against a single host `deploy_host`
(`inventory.ini:10-11`, `localhost ansible_connection=local`). Everything happens
on the local Docker host; no SSH is used (`ansible.cfg:16-18`).

The data plane that comes up (host ports):

| Service | Image | Host port(s) | Source |
|---|---|---|---|
| CUBE API + `/chris-admin/` | `ghcr.io/fnndsc/cube:6.11.0` | `8000` (remappable) | miniChRIS `chris` |
| ChRIS_ui | `ghcr.io/fnndsc/chris_ui:d65741e` | `8020` | miniChRIS |
| oxidicom C-STORE SCP | `ghcr.io/fnndsc/oxidicom:3.0.0` | `11111` | miniChRIS, AET `ChRIS` |
| NATS (LONK progress bus) | `nats:2.11.4-alpine` | `4222` | miniChRIS `pacs` |
| RabbitMQ | `rabbitmq:3` | `5672` | miniChRIS `queue` |
| Bundled Orthanc | `jodogne/orthanc-plugins:1.12.7` | `4242`, `8042` | miniChRIS `pacs` |
| pfdcm | `ghcr.io/fnndsc/pfdcm:3.1.2` | `4005` | miniChRIS `pacs` |
| pfcon / pman | — | `5005` / `5010` | miniChRIS (compute plane) |
| **Test Orthanc** | `orthancteam/orthanc:24.10.3` | **`8142`** (HTTP/DICOMweb), **`4342`** (DIMSE) | **this deploy**, AET `SPIKEORTHANC` |
| Postgres | `postgres:17` | *(internal — no host port)* | miniChRIS `db` |

Image pins are transcribed from `deploy/vendor/miniChRIS-docker/docker-compose.yml`
and re-stated in `group_vars/all.yml:45-50` for citation; the compose file is
authoritative.

---

## 2. The vendoring decision: pinned submodule, run in place

The single most important architectural choice in the deploy: miniChRIS-docker
is **vendored as a pinned git submodule** at `deploy/vendor/miniChRIS-docker`
(commit `4d689ba`, "Upgrade cube:6.11.0"), and the `minichris` role runs it **in
place** — there is **no runtime clone** (`group_vars/all.yml:20-21`,
`roles/minichris/tasks/main.yml:2-16`).

Why this matters:

- **Reproducible / offline.** The wrapped stack version is version-controlled by
  the gitlink. You bump it by checking out a new commit in the submodule and
  committing the gitlink, not by pulling `master` at deploy time
  (`group_vars/all.yml:30-33`).
- **Clean working tree.** `minichris.sh` writes only files covered by the
  submodule's own `.gitignore` (notably `.env`, which it populates with
  `HOSTNAME`; `minichris.sh:13`). Running in place leaves the submodule clean
  (`roles/minichris/tasks/main.yml:14-16`).
- **Wrap, never fork.** All spike-specific behaviour lives in the Ansible layer
  and the compose override; the upstream compose file is touched only via
  `COMPOSE_FILE` chaining (§4), never edited.

The `minichris` role asserts the submodule is present and fails with the exact
init command if it is missing (`roles/minichris/tasks/main.yml:24-35`):

```bash
git submodule update --init deploy/vendor/miniChRIS-docker   # from repo root, once
```

CUBE itself is the **prebuilt image** `ghcr.io/fnndsc/cube:6.11.0`
(`docker-compose.yml:33`) — it is *not* built from source in the wrap path.
That fact is the root of the overlay's fragility (§9–§10).

---

## 3. Role-by-role walkthrough

### 3.1 `prereqs` — fail early, fail clearly

`roles/prereqs/tasks/main.yml` asserts the host can actually run the stack
before anything is touched:

- `docker --version` and `docker info` (lines 4-20). `docker info` is the real
  gate — it only succeeds if the daemon is up **and** the current user can talk
  to it (docker group / root). The comment flags this as "the #1 setup gotcha"
  (line 15-16).
- `docker compose version --short` and an `assert` that it is `>= 2.6.0`
  (lines 22-37) — miniChRIS requires Compose v2.6+ (`prereqs/defaults/main.yml:3-4`).
- The `community.docker` collection is installed (lines 39-47); it provides
  `docker_container` and `docker_network_info` used by the `orthanc` role.
- The **Docker SDK for Python** is importable in Ansible's interpreter
  (lines 49-58) — `community.docker`'s modules `import docker` in-process.

### 3.2 `minichris` — bring up the wrapped stack (pacs profile)

`roles/minichris/tasks/main.yml`:

1. Ensure the workdir exists (`~/.cache/dicomweb-spike`,
   `group_vars/all.yml:17`); assert the submodule's `docker-compose.yml` exists
   (lines 18-35).
2. **Run `./minichris.sh`** (lines 47-57). This is the canonical bootstrap: it
   writes `HOSTNAME` to `.env`, does `docker compose up -d` for the **default**
   profile, then runs the one-shot `chrisomatic` registrar
   (`vendor/.../minichris.sh:13-22`). The role deliberately shells out to the
   upstream script rather than re-implementing it in `docker_compose_v2`, so the
   bootstrap never diverges from upstream (`tasks/main.yml:8-13`).
3. **Add the `pacs` profile** (lines 59-69): `docker compose --profile pacs up -d`.
   The bare default profile is CUBE + compute only; the DICOM ingest pipeline
   (`orthanc` + `pfdcm` + `oxidicom` + `nats`) is gated behind `pacs`
   (`group_vars/all.yml:38-42`, and see `docker-compose.yml:209-281` where each
   of those services carries `profiles: [pacs]`). **Without `pacs` there is no
   PACS receiver at all** — this is the two-step that mirrors the upstream README.
4. **Wait for CUBE health** (lines 86-103): poll `GET /api/v1/` with basic auth
   until `200`, up to `60 × 5s = 5 min` (`group_vars/all.yml:74-75`). First boot
   pulls images and runs migrations, so the full budget is allowed.
5. **Wait for oxidicom to be *running*** (lines 114-124) — via
   `docker compose ps --status running -q oxidicom`, **never a TCP probe**. See
   bug #3 (§8) — this is the load-bearing fix that keeps ingest alive.

### 3.3 `orthanc` — a SEPARATE test PACS as the sample-data source

`roles/orthanc/tasks/main.yml` runs **our own** `orthancteam/orthanc:24.10.3`
container (`group_vars/all.yml:98-103`), distinct from the bundled miniChRIS
Orthanc. Rationale (`tasks/main.yml:3-9`):

- We control the **DicomWeb + REST** plugin config and the AET.
- We bind to **non-colliding host ports** `8142` (→ container `8042`) and `4342`
  (→ container `4242`) so we never collide with the bundled Orthanc on `8042/4242`
  that the `pacs` profile starts (`tasks/main.yml:45-47`).
- It is registered with a downstream modality `ChRIS` pointing at `oxidicom`, so
  it can C-STORE-push studies into CUBE.

Config is rendered from `templates/orthanc.json.j2`: `DicomAet = SPIKEORTHANC`
(line 13), `DicomPort 4242`, `DicomAlwaysAllowStore/Echo`, the DICOMweb plugin
(`Root: /dicom-web/`, line 25-33), and the downstream modality
`"ChRIS": ["ChRIS", "oxidicom", 11111]` (line 20-22). Note the modality targets
`oxidicom` by **container name** on the **in-container** listener port `11111` —
which is why the test Orthanc must join the `minichris-local` network
(`tasks/main.yml:51-56`, `group_vars/all.yml:107-113`). The role confirms that
network exists (`docker_network_info`, lines 32-36) and is *informational* about
C-ECHO (oxidicom may not answer C-ECHO; `failed_when: false`, lines 84-93) — the
real test is the C-STORE in `sample_data`.

### 3.4 `sample_data` — load DICOM, then C-STORE-push to oxidicom

`roles/sample_data/tasks/main.yml`. Three source modes (`group_vars/all.yml:121-126`):

| Mode | Behaviour |
|---|---|
| `download` (default) | Fetch `sample_data_url` (a public DICOM zip), unpack, upload all instances. |
| `local_dir` | Upload every file under `sample_data_local_dir` — point this at the **BCH dataset**. |
| `none` | Skip (assume Orthanc already populated). `meta: end_play` (line 6-8). |

Flow: download + unpack (lines 17-30) → find `*.dcm/*.DCM/*.ima/*.IMA`, falling
back to *all files* if none match (lines 38-63; real archives ship extensionless
DICOM, so it over-matches and lets Orthanc reject non-DICOM) → `POST /instances`
to the test Orthanc tolerating `200/400/415` and counting `200`s (lines 76-108)
→ list studies and `POST /modalities/ChRIS/store` (Synchronous) to **C-STORE each
study to oxidicom** (lines 111-148) → 10s settle (lines 150-153).

That C-STORE push is the crux: it drives the **normal CUBE ingest path**
— `oxidicom` receives over DIMSE → writes to `/data` → fires a RabbitMQ
registration job → CUBE worker creates `PACSSeries` rows. Disable the push with
`sample_data_push_to_cube: false`.

Live run (2026-05-28): **384 sample instances** loaded and ingested end-to-end.

### 3.5 `dicomweb_app` — the integration seam (off by default)

The overlay role. Covered in depth in §9. Default `dicomweb_overlay_enabled:
false` (`group_vars/all.yml:142`). When off, it prints what it *would* do (two
debug blocks, demo-able) and the play **continues** to `verify`
(`roles/dicomweb_app/tasks/main.yml:25-42`).

### 3.6 `verify` — the smoke test

`roles/verify/tasks/main.yml` shells out to `scripts/smoke.sh`, passing all
config via env (one source of truth, lines 15-30). It fails the play only on a
hard `FAIL` (`failed_when: verify_run.rc != 0`); `smoke.sh` exits non-zero **iff
any required check fails** (`smoke.sh:155`). DICOMweb checks report `SKIP` (not
`FAIL`) unless `EXPECT_DICOMWEB=true` (§7).

---

## 4. The COMPOSE_FILE / port-override mechanism (bug #4 fix)

Upstream hardcodes `8000:8000` for the `chris` service
(`docker-compose.yml:34-35`). On any host already using `:8000` this collides and
CUBE never publishes. The deploy makes the host port configurable **without
editing the wrapped compose file** by chaining a tracked override through
`COMPOSE_FILE`.

`compose-overrides/cube-port.yml`:

```yaml
services:
  chris:
    ports: !override
      - "${CUBE_HOST_PORT:-8000}:8000"
```

- `!override` **replaces** the base `ports` list. The default Compose merge
  *appends*, which would keep publishing `8000` alongside the new port — so the
  YAML tag is load-bearing (`cube-port.yml:6-9`).
- `site.yml:29-31` sets, for **every** `docker compose` invocation in the play:

  ```yaml
  environment:
    COMPOSE_FILE: "{{ minichris_dir }}/docker-compose.yml:{{ playbook_dir }}/compose-overrides/cube-port.yml"
    CUBE_HOST_PORT: "{{ cube_host_port | string }}"
  ```

  Compose reads `COMPOSE_FILE` as a colon-separated list and merges them
  left-to-right, so the override layers onto the vendored base for the whole play
  (`up`, `ps`, `exec`, `restart`).
- The value comes from `cube_host_port` (`group_vars/all.yml:66`), which also
  drives `cube_port` → `cube_base_url` → `cube_api_url` (lines 67-69), so the
  health check, smoke test, and overlay re-wait all target the same port.

Change the port for the whole run with one flag:

```bash
ansible-playbook -i inventory.ini site.yml -e cube_host_port=18000
```

Verify the merged config by hand: `docker compose -f docker-compose.yml -f
cube-port.yml config` (`cube-port.yml:9`).

---

## 5. Networking topology

- **`minichris-local`** is the compose `local` network, explicitly named via
  `name: minichris-local` (`docker-compose.yml:439-440`). `oxidicom` sits on both
  `pacs` and `local` (`docker-compose.yml:252-254`).
- The **test Orthanc** attaches to `minichris-local` (`orthanc/tasks/main.yml:51-56`,
  with `networks_cli_compatible: true` + `comparisons: networks: allow_more_present`
  so it can also keep the default bridge for host port publishing). This lets it
  resolve `oxidicom` by name for the C-STORE push.
- The network name assumes `COMPOSE_PROJECT_NAME=minichris`
  (`group_vars/all.yml:34`) and the upstream `name:` override. If you change the
  project name, update `minichris_local_network` (`group_vars/all.yml:111`,
  README caveat 4).

---

## 6. Credentials, profiles, and the data path (cited)

- CUBE auth: `chris` / `chris1234` (`group_vars/all.yml:70-71`) — the public dev
  default from miniChRIS `secrets.env`. Override for anything real.
- oxidicom receiver: AET `ChRIS`, TCP `11111`
  (`docker-compose.yml:241,244`, `group_vars/all.yml:82-84`).
  `OXIDICOM_SCP_PROMISCUOUS=true` accepts any calling AE.
- The PACS identifier in CUBE DICOMweb URL paths equals `OXIDICOM_SCP_AET = ChRIS`
  (`group_vars/all.yml:156-157`, `verify_pacs_identifier: ChRIS`).
- `OXIDICOM_DEV_SLEEP=150ms` (`docker-compose.yml:247`) is a **demo throttle** —
  ingest is intentionally slow so progress is visible in ChRIS_ui. Drop it for any
  throughput measurement on the BCH dataset (README caveat 7).
- Volume ownership: the wrapped stack's own `*-nonroot-user-volume-fix` init
  containers `chmod g+rwx /data` for uid 1001 (`docker-compose.yml:422-435`); the
  deploy does not manage `chris_files` externally (README caveat 8).

---

## 7. The smoke test (`scripts/smoke.sh`)

`smoke.sh` curls everything and reports `PASS` / `FAIL` / `SKIP` per line,
exiting non-zero only on a `FAIL` (line 155). Checks:

- **Core CUBE** (lines 58-88): `GET /api/v1/` (basic auth, want 200);
  `POST /api/v1/auth-token/` (token auth); `GET /api/v1/pacs/`; and
  `GET /api/v1/pacs/series/` — counting `SeriesInstanceUID` occurrences to
  confirm the sample data ingested (≥1 ⇒ PASS; 0 ⇒ SKIP "may still be ingesting").
- **Test Orthanc** (lines 90-103): `/system`, `/plugins/dicom-web` (plugin
  loaded), and Orthanc's own QIDO-RS `/dicom-web/studies` — a sanity check that
  the *source* PACS speaks DICOMweb.
- **CUBE DICOMweb (L2)** (lines 105-148): gated on `EXPECT_DICOMWEB`. When
  `!= true`, all three (QIDO/WADO/STOW) report **SKIP** with "L2 overlay not
  applied" (lines 106-109). When enabled:
  - **QIDO-RS** `GET /dicom-web/pacs/ChRIS/studies` with
    `Accept: application/dicom+json` → 200/204.
  - **WADO-RS** — pulls a `StudyInstanceUID` (tag `0020000D`) out of the QIDO
    response, then `GET .../studies/<uid>/metadata` → 200.
  - **STOW-RS** — `POST .../studies` with an empty multipart body, asserting the
    endpoint **exists** (400/415/409/200 = present-but-empty; **404 = missing =
    FAIL**, lines 140-147). It deliberately does not push a real object.

Endpoint shapes follow DICOM PS3.18 (QIDO §10.6, WADO §10.4, STOW §10.5;
`smoke.sh:14-17`). Run by hand:

```bash
CUBE_USER=chris CUBE_PASSWORD=chris1234 ./scripts/smoke.sh
```

Live run (2026-05-28): CUBE healthy, Orthanc up, 384 instances loaded; DICOMweb
checks SKIP (overlay off).

---

## 8. What we hardened — the six bugs found by running it

These are the noteworthy ones. Each was found on a live host on
2026-05-28 and fixed in tree.

### Bug #1 — `stdout_callback=yaml` pointed at a removed plugin

The `yaml` stdout callback was the `community.general.yaml` plugin, **removed in
community.general 12 / ansible-core 2.20**. With it set, the playbook wouldn't
even start. **Fixed** to the built-in default callback rendering results as YAML
(`ansible.cfg:5-14`):

```ini
stdout_callback = default
callback_result_format = yaml
nocows = True
```

### Bug #2 — SDK check used `ansible_python_interpreter` (= `auto_silent`)

`inventory.ini:11` sets `ansible_python_interpreter=auto_silent` — a *discovery
keyword*, not a path. The prereqs SDK check originally interpolated that keyword
as if it were an interpreter path, so the `import docker` command failed. **Fixed**
to use the *discovered* interpreter (`roles/prereqs/tasks/main.yml:53-54`):

```yaml
{{ ansible_facts.get('discovered_interpreter_python', ansible_playbook_python) }}
-c "import docker; print(docker.__version__)"
```

### Bug #3 — a raw TCP probe CRASHED oxidicom's listener (the silent ingest killer)

The original `minichris` role used `wait_for port: 11111` — a **bare TCP connect**
against oxidicom's DIMSE port. oxidicom 3.0.0 treats a TCP connection that never
negotiates a DICOM A-ASSOCIATE as a *failed association* and **panics its
state-loop thread**:

```
thread 'main' panicked at src/association_series_state_loop.rs:112:
Unknown association ULID
```

The container stays `Up`, but the listener is dead — so **every subsequent
C-STORE silently fails and 0 instances ingest**. **Fixed** to gate on the
container being in the `running` state instead, never touching the DIMSE socket
(`roles/minichris/tasks/main.yml:105-124`):

```yaml
docker compose --profile pacs ps --status running -q oxidicom
```

**Rule of thumb to repeat in the meeting: never TCP-probe a DIMSE port.** A
health probe that opens a connection and disconnects without a full DICOM
association handshake can crash a fragile SCP.

### Bug #4 — CUBE host port hardcoded `8000:8000` collides

Covered in §4. **Fixed** via `compose-overrides/cube-port.yml` (`!override`)
chained through `COMPOSE_FILE` in `site.yml` and driven by `cube_host_port`.

### Bug #5 — the overlay made three wrong assumptions about the image

The original `dicomweb_app` role:

1. Targeted the wrong in-container path — `/home/localuser/chris_backend` (the
   stale value still visible in `roles/dicomweb_app/defaults/main.yml:4`). The
   real Django project root in `cube:6.11.0` is **`/opt/app-root/src`**.
   **Fixed** by overriding `dicomweb_container_app_dir: /opt/app-root/src` in
   `group_vars/all.yml:147` (group_vars wins over role defaults), matching
   `overlay_patch.py:9,24`.
2. Didn't install `pydicom` — the prebuilt image predates Phase A's requirement,
   and the STOW handler + indexer import it, so the app failed to import.
   **Fixed** by `pip install 'pydicom>=3.0,<4.0'` into both `chris` and `worker`
   (`roles/dicomweb_app/tasks/main.yml:94-106`).
3. Didn't wire `INSTALLED_APPS` / urls. **Fixed** by `overlay_patch.py`, which
   runs **as root** (`--user root`, `tasks/main.yml:111-115`) because the image's
   project files are not writable by the app user. It idempotently appends
   marker-fenced blocks: `'dicomweb'` into `INSTALLED_APPS` in
   `config/settings/common.py`, and a `urlpatterns +=` mount of
   `dicomweb.urls` under `dicom-web/pacs/<str:pacs_identifier>/` in
   `config/urls.py` (`overlay_patch.py:30-48`).

### Bug #6 — `meta: end_play` skipped `verify` when the overlay was off

The overlay role originally aborted with `meta: end_play` when disabled — which
ends the **whole play**, skipping the downstream `verify` role. **Fixed** to a
`when: dicomweb_overlay_enabled` *block* (`roles/dicomweb_app/tasks/main.yml:44-46`)
so the play always reaches the smoke test. (The README calls this out explicitly:
"it uses a `when:` guard, **not** `meta: end_play`, so `verify` always runs"
— `README.md:184`.)

> Note: two `meta: end_play` uses **remain** intentionally — `orthanc` when
> `test_orthanc_enabled` is false (`orthanc/tasks/main.yml:15-17`) and
> `sample_data` when mode is `none` (`sample_data/tasks/main.yml:6-8`). Those are
> the *last* roles whose work is conditional before `dicomweb_app`/`verify`, and
> short-circuiting there is benign for the default flow; the bug was specifically
> `dicomweb_app` aborting *before* `verify`.

---

## 9. The overlay seam in detail (`roles/dicomweb_app`)

CUBE runs as the prebuilt `cube:6.11.0`; the L2 code at
`implementation/dicomweb-l2/` (`apps.py`, `urls.py`, `qido_views.py`,
`wado_views.py`, `stow_views.py`, `models.py`, `tasks.py`, `migrations/`, …) is
**not** in that image. The `dicomweb_app` role is the explicit seam that overlays
it into the **running** container, exercised without rebuilding the image. Steps
when `dicomweb_overlay_enabled=true` (`tasks/main.yml:44-157`):

1. Assert `dicomweb_l2_src/urls.py` exists, else fail clearly (lines 47-57).
2. Resolve the running `chris` and `worker` container ids via
   `docker compose ps -q` (lines 59-77).
3. `docker cp implementation/dicomweb-l2/.` into
   `/opt/app-root/src/dicomweb` of **both** `chris` and `worker`, plus copy
   `overlay_patch.py` to `/tmp` in each (lines 81-89). The worker is targeted
   because it runs the **Phase-A Celery indexer** (`tasks.py`), which needs
   `dicomweb` in `INSTALLED_APPS` to load the models.
4. `pip install pydicom` into both (lines 94-106) — see bug #5.2.
5. Run `overlay_patch.py` as root in both (lines 111-124): wire `INSTALLED_APPS`
   + urls, idempotent via markers (`changed_when: "'UNCHANGED' not in stdout"`),
   notifying the restart handler.
6. `manage.py migrate --noinput` in `chris` (lines 126-137): creates the
   `PACSStudy` model and applies the `dicomweb` migrations.
7. `flush_handlers` → restart `chris` + `worker` (`handlers/main.yml`) so Django
   re-imports `settings` / `urls` / `models` / `tasks`; `db_migrate` is one-shot
   and not restarted (lines 143-144).
8. Re-wait for CUBE health on the configured port (lines 146-157).

Endpoints then mount under `/dicom-web/pacs/<id>/{studies,series,instances}`
(QIDO/WADO/STOW). Apply it with:

```bash
ansible-playbook -i inventory.ini site.yml -e dicomweb_overlay_enabled=true
```

---

## 10. KEY ARCHITECTURE POINT — overlay (dev) vs baked image (prod)

The source-onto-prebuilt-image overlay is **fragile by construction**, and the
reason is concrete: master CUBE refactored the `pacsfiles` status choices into a
new `pacsfiles/enums.py`, which `cube:6.11.0` **does not have**. So overlaying
*master* `pacsfiles`-dependent code onto the 6.11.0 image breaks at import. The
overlay is good enough to *demonstrate the seam* and exercise the L2 app modules,
but it is not the production path.

The **production-correct** path is a **baked image**: build `cube:dev` from
source with the `dicomweb` app and `pydicom` already in the image, so there is no
runtime patching, no `pip install` into a running container, no root edits of
project files, and no version-skew between the app code and the CUBE it depends
on. End-to-end was validated this way — by building `cube:dev` from the vendored
CUBE submodule (`implementation/ChRIS_ultron_backEnd`) and running CUBE's own dev
stack — which is the architecturally honest answer for BCH.

This mirrors `proposal/RESEARCH_TICKET_OUTPUT.md`: the recommended hybrid (option C)
keeps QIDO/WADO/STOW **endpoints in Django/CUBE** (reusing CUBE's auth chain)
while a NATS consumer indexes oxidicom-parsed tags, with the Celery indexer as the
fallback for non-oxidicom paths (including STOW-RS). All that code lands at this
same seam — inside `chris`/`worker` — which is why the overlay targets both.

---

## 11. Running it

One-time setup (from repo root, then `deploy/ansible/`):

```bash
git submodule update --init deploy/vendor/miniChRIS-docker   # once, if not --recurse-submodules'd
ansible-galaxy collection install -r requirements.yml        # once (community.docker >= 3.4.0)
ansible-playbook -i inventory.ini site.yml
```

First run pulls many images and runs CUBE migrations — allow a few minutes.
Subsequent runs are fast and idempotent (`up` is a no-op for running services;
uploads/pushes are safe to repeat).

Tags (run one phase alone, `site.yml:34-44`):

```bash
ansible-playbook -i inventory.ini site.yml --tags minichris     # just the stack
ansible-playbook -i inventory.ini site.yml --tags orthanc       # just the test PACS
ansible-playbook -i inventory.ini site.yml --tags sample_data   # reload data
ansible-playbook -i inventory.ini site.yml --tags verify        # just smoke tests
```

Common overrides:

```bash
# avoid an :8000 collision (drives health check + smoke + overlay re-wait too)
ansible-playbook -i inventory.ini site.yml -e cube_host_port=18000

# use the BCH dataset instead of the public download
ansible-playbook -i inventory.ini site.yml \
  -e sample_data_mode=local_dir -e sample_data_local_dir=/path/to/bch/dicoms

# apply the L2 overlay (turns the QIDO/WADO/STOW smoke checks from SKIP to PASS)
ansible-playbook -i inventory.ini site.yml -e dicomweb_overlay_enabled=true
```

If your user is not in the `docker` group, run with `--become`
(`inventory.ini:13-17`).

---

## 12. Teardown

`scripts/teardown.sh` removes the test Orthanc container, then tears down the
wrapped stack:

```bash
./scripts/teardown.sh                 # remove test Orthanc + miniChRIS (down -v)
KEEP_VOLUMES=1 ./scripts/teardown.sh  # keep miniChRIS volumes
```

It `docker rm -f dicomweb-spike-orthanc` (line 23-24), then invokes the upstream
`unmake.sh` (lines 28-30), which reaps pman-launched plugin containers (label
`org.chrisproject.miniChRIS=plugininstance`) and does
`docker compose --profile pacs --profile pflink --profile hasura down -v` —
**destroying named volumes** (`minichris-files`, `db_data`, `orthanc`, …),
consistent with the ephemeral design. With `KEEP_VOLUMES=1` it does a manual
`down` **without** `-v` (`teardown.sh:28-36`).

---

## 13. Caveats to verify live before the demo

From `README.md:267-320`, the honest limits:

1. The full `prereqs → minichris → orthanc → sample_data → verify` flow was run
   live (2026-05-28): CUBE healthy, Orthanc up, Orthanc → oxidicom C-STORE
   ingested end-to-end, 384 instances. The one path **not** yet exercised on a
   live stack is the overlay-**enabled** HTTP path (actual QIDO/WADO/STOW
   responses) — run with `-e dicomweb_overlay_enabled=true`.
2. `orthancteam/orthanc` pinned to `24.10.3`; bump `test_orthanc_image` if that
   tag is unavailable.
3. CUBE app path is `/opt/app-root/src` in `cube:6.11.0`; confirm with
   `docker compose exec chris sh -c 'pwd; ls'` if you bump the image.
4. The test Orthanc joining `minichris-local` assumes
   `COMPOSE_PROJECT_NAME=minichris`.
5. Sample dataset shape varies; prefer `sample_data_mode=local_dir` with a
   known-good directory for the demo.
6. L2 endpoints are **SKIP** until the overlay is enabled.
7. `OXIDICOM_DEV_SLEEP=150ms` is a demo throttle — drop it for throughput tests.
8. Volume ownership is handled by the stack's own init containers.

---

## 14. Q&A

**Q: Why does QIDO-RS need the overlay? Isn't CUBE already up?**
The running CUBE is the prebuilt `cube:6.11.0` image, which contains **no**
DICOMweb code. CUBE's native PACS API is `/api/v1/pacs/...` (DRF JSON), *not*
QIDO/WADO/STOW. The L2 `dicomweb` app — the QIDO/WADO/STOW views, the `PACSStudy`
model, the urls — lives in this repo at `implementation/dicomweb-l2/`, outside
the image. Until the `dicomweb_app` role copies it in, installs `pydicom`, wires
`INSTALLED_APPS` + urls, migrates, and restarts, the routes under
`/dicom-web/pacs/<id>/` simply don't exist — so the smoke test reports them
**SKIP**, and `GET .../studies` would 404. The overlay (or, properly, a baked
image) is what mounts them.

**Q: What happens if host port 8000 is already taken?**
Upstream hardcodes `8000:8000` (`docker-compose.yml:34-35`), so a raw wrap would
collide and CUBE would fail to publish. We fixed it: set
`-e cube_host_port=<free port>`. That drives the `compose-overrides/cube-port.yml`
`!override` (which *replaces* rather than appends the port list) via
`CUBE_HOST_PORT`, and the same var threads through `cube_base_url` so the health
check, smoke test, and overlay re-wait all target the new port. One flag, whole
play.

**Q: Overlay vs baked image — which is right?**
Overlay = dev convenience: exercise L2 against a running prebuilt CUBE without a
rebuild. It is **fragile** — e.g. master CUBE moved `pacsfiles` status choices
into `pacsfiles/enums.py`, which `cube:6.11.0` lacks, so overlaying master
`pacsfiles`-dependent code onto 6.11.0 breaks at import. The
**production-correct** path is a **baked image**: build `cube:dev` from source
with the `dicomweb` app + `pydicom` in the image (no runtime patching, no
version-skew). End-to-end was validated that way. For BCH: demo the seam with the
overlay if needed, but state plainly that production ships a baked image + Helm.

**Q: What crashed oxidicom, and how did you find it?**
A naive `wait_for port: 11111` TCP probe. oxidicom 3.0.0 treats a bare TCP
connect with no DICOM A-ASSOCIATE handshake as a failed association and **panics**
its state-loop thread (`Unknown association ULID`,
`src/association_series_state_loop.rs:112`). The container stays `Up` but the
listener is dead, so **every later C-STORE silently fails — 0 instances
ingested**, with no error in the deploy. Found by running the deploy and seeing
empty `/api/v1/pacs/series/` despite a "successful" run. Fixed by gating on
container `running` state via `docker compose ps`, never touching the DIMSE
socket. General rule: **don't TCP-probe DIMSE ports.**

**Q: Why a second Orthanc when miniChRIS already bundles one?**
Control and isolation. We own the test Orthanc's DicomWeb/REST plugin config, its
AET (`SPIKEORTHANC`), and its ports (`8142/4342`, deliberately off the bundled
`8042/4242`). It is the *sample-data source PACS*; it registers `oxidicom` as a
downstream modality and C-STORE-pushes studies into CUBE — driving the real
ingest path rather than poking CUBE's DB directly.

**Q: Is `verify` skipped when the overlay is off?**
No — that *was* bug #6. The overlay role uses a `when:` block, not
`meta: end_play`, so the play always reaches `verify`; with the overlay off the
DICOMweb checks just report SKIP while the core CUBE + Orthanc checks still run.

**Q: How do I point this at the BCH dataset?**
`-e sample_data_mode=local_dir -e sample_data_local_dir=/path/to/bch/dicoms`. The
role over-matches filenames (handles extensionless DICOM) and lets Orthanc reject
non-DICOM, then C-STORE-pushes everything to oxidicom. Drop `OXIDICOM_DEV_SLEEP`
first if you want real throughput numbers.
