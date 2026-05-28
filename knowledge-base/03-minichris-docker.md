# miniChRIS-docker: the Deployable Stack

**Source of record:** [`FNNDSC/miniChRIS-docker`](https://github.com/FNNDSC/miniChRIS-docker) (`master`, fetched 2026-05-28). Service names, image tags, ports, env vars, and credentials below are transcribed verbatim from that repo's `docker-compose.yml`, `secrets.env`, `chrisomatic.yml`, and `orthanc.json`. The shell scripts in §7–§8 are reproduced faithfully but lightly condensed (a `set -x`/CI guard here, a progress-bar wrapper there) — where that matters it is called out. This file is the factual basis for the Ansible deployment that must **wrap** this compose stack, so the exact strings matter.

> [!CAUTION]
> Upstream README is explicit: *"miniChRIS is not suitable for production. It contains hard-coded secrets and insecure defaults."* (<https://github.com/FNNDSC/miniChRIS-docker#readme>). Treat every credential here as a known-public default to be overridden in any real deployment.

---

## 1. What miniChRIS-docker is

`miniChRIS-docker` is a single `docker-compose.yml` that stands up an **ephemeral, demo-grade** instance of the entire ChRIS backend for local development and end-to-end (E2E) CI testing (<https://github.com/FNNDSC/miniChRIS-docker#readme>). It is not the production deployment path — production is the `fnndsc/charts` Helm repo (<https://chrisproject.org/docs/run/helm>). For the ATLAS DICOMweb spike it is the right thing to wrap, because it is the canonical, version-pinned arrangement of CUBE + its PACS ingest pipeline (oxidicom + Orthanc + pfdcm + NATS).

The compose file (`docker-compose.yml`, header comment) warns: **`/var/run/docker.sock` is mounted into some services (notably `pman`)** — `pman` launches plugin containers as siblings on the host Docker daemon. This matters for the Ansible host's security posture.

Requirement: **docker-compose v2.6+** (README §System Requirements). `minichris.sh` start time is 30–60 s on a laptop, 2–3 min in GitHub Actions (README §Performance).

---

## 2. Compose profiles — the stack is sliced

Not everything starts by default. miniChRIS uses [Docker Compose profiles](https://docs.docker.com/compose/profiles/). Services with **no** `profiles:` key start on a bare `docker compose up`. Services with a profile only start when that profile is named.

| Profile | Services gated behind it | Why it matters for DICOMweb |
|---|---|---|
| *(none / default)* | `db_migrate`, `chris`, `worker`, `worker_periodic`, `scheduler`, `db`, `queue`, `pfcon`, `pman`, `chris_ui`, plus the two `*-nonroot-user-volume-fix` init containers | Core CUBE + compute. **Does NOT include the PACS pipeline.** |
| `pacs` | `orthanc`, `pfdcm`, `oxidicom`, `nats` | **This is the DICOM ingest pipeline.** You must `--profile pacs` to get a PACS at all. |
| `orthanc` | `orthanc` (alias so Orthanc can be started alone) | — |
| `tools` | `chrisomatic` | One-shot plugin/compute-resource registration. |
| `pflink` | `pfbridge`, `pflink`, `pflink-db` | Legacy pfdcm-driven pull orchestration. Not needed for the spike. |
| `hasura` | `graphql-engine`, `data-connector-agent`, `hasura-db`, `hasura-cli` | Optional GraphQL layer over CUBE's DB. Not needed for the spike. |

Bring up the PACS pipeline explicitly:

```bash
docker compose --profile pacs up -d        # README §Quick Start
```

The teardown script tears down **every** profile (see §7).

---

## 3. Service-by-service reference

All images, commands, ports, volumes, and dependencies below are from `docker-compose.yml`. Format of ports is `host:container`.

### 3.1 Core CUBE services (default profile)

| Service | Image | Host:Container ports | Role |
|---|---|---|---|
| `chris` | `ghcr.io/fnndsc/cube:6.11.0` | `8000:8000` | The CUBE Django API server (CUBE = ChRIS_ultron_backEnd). This is where the DICOMweb (QIDO/WADO/STOW) endpoints will live under variant A/C of the spike. |
| `db_migrate` | `ghcr.io/fnndsc/cube:6.11.0` | — | One-shot: `python manage.py migrate --noinput`. Gates `chris`/workers via `service_completed_successfully`. **Our DICOMweb migrations run here.** |
| `worker` | `ghcr.io/fnndsc/cube:6.11.0` | — | Celery worker: `celery -A core worker -c 4 -l info -Q main1,main2`. Runs the plugin-instance jobs **and** the Phase-A `index_pacs_instance` indexing task. |
| `worker_periodic` | `ghcr.io/fnndsc/cube:6.11.0` | — | Celery worker on the `periodic` queue (`-c 2`). |
| `scheduler` | `ghcr.io/fnndsc/cube:6.11.0` | — | Celery beat scheduler (`django_celery_beat.schedulers:DatabaseScheduler`). |
| `db` | `docker.io/library/postgres:17` | — (internal 5432) | CUBE's Postgres. `pg_isready` healthcheck. **Where `PACSSeries`/`PACSInstance`/(future)`PACSStudy` rows live, and where `pg_trgm` would be added.** |
| `queue` | `docker.io/library/rabbitmq:3` | `5672:5672` | AMQP broker. Carries Celery tasks **and** is the AMQP target oxidicom posts series-registration jobs to. |

`chris` carries labels `org.chrisproject.role: "ChRIS_ultron_backEnd"` and `org.chrisproject.miniChRIS: "miniChRIS"`. All CUBE services share the `chris_files` named volume (`minichris-files`) mounted at `/data` (= `MEDIA_ROOT`), and read `secrets.env`.

### 3.2 Compute plane (default profile)

| Service | Image | Host:Container ports | Role |
|---|---|---|---|
| `pfcon` | `ghcr.io/fnndsc/pfcon:5.2.3` | `5005:5005` | Compute controller. Network alias `pfcon.host` on the `local` net (matches `chrisomatic.yml` compute-resource URL `http://pfcon.host:5005/api/v1/`). Runs as user `1001`. `STORAGE_ENV: fslink`, `STOREBASE_MOUNT: /var/local/storeBase` (the `chris_files` volume). |
| `pman` | `ghcr.io/fnndsc/pman:6.2.0` | `5010:5010` | Process manager. Mounts `/var/run/docker.sock` and launches plugin containers as siblings. `CONTAINER_ENV: docker`, `JOB_LABELS: org.chrisproject.miniChRIS=plugininstance` (the label `unmake.sh` uses to reap jobs), `userns_mode: host`. |
| `chrisomatic` | `ghcr.io/fnndsc/chrisomatic:1.0.0` | — | `tools` profile. One-shot declarative registrar; mounts `chrisomatic.yml` + docker.sock. Registers the compute resource and seed plugins, then exits. |
| `chris_ui` | `ghcr.io/fnndsc/chris_ui:d65741e` | `8020:8020` | The React frontend. Env points it at CUBE (`:8000`), pfdcm (`:4005`), and **OHIF at `http://<host>:8042/ohif/`** — i.e. OHIF is served by Orthanc's plugin, not by ChRIS. Relevant to the WADO-RS viewer story. |

### 3.3 PACS ingest pipeline (`pacs` profile) — the part that matters most

This is the existing DICOM receive path the spike must understand. ASCII overview:

```
  External DICOM         C-STORE          writes .dcm to /data            registration job (AMQP)
  modality / Orthanc  ───────────────►  oxidicom  ─────────────────────────────────────────►  CUBE (chris)
   (SCU, push)        TCP :11111        (C-STORE SCP)        progress events (NATS) ──► nats   creates PACSSeries rows
                                              │                                                via Celery worker
                                              └── writes into chris_files volume (/data) ──► same volume CUBE reads
```

| Service | Image | Host:Container ports | Role |
|---|---|---|---|
| `orthanc` | `docker.io/jodogne/orthanc-plugins:1.12.7` | `4242:4242` (DICOM), `8042:8042` (HTTP/DICOMweb/OHIF) | Test PACS. DICOM C-STORE SCP/SCU + REST API + OHIF viewer plugin. Config bind-mounted from `./orthanc.json`; DB in `orthanc` named volume. Profiles: `pacs`, `orthanc`. On the `pacs` network only. |
| `pfdcm` | `ghcr.io/fnndsc/pfdcm:3.1.2` | `4005:4005` | DICOM query/retrieve orchestrator (the older pull path). Runs as user `1001`. Mounts `pfdcm` volume at `/home/dicom`, the bind-mount `./pfdcm-services` (read-only PACS config), and `chris_files` at `/chris_files`. `pacs` profile. |
| `oxidicom` | `ghcr.io/fnndsc/oxidicom:3.0.0` | `11111:11111` | **The DICOM C-STORE SCP receiver.** Listens on TCP 11111, writes received `.dcm` into `/data` (the shared `chris_files` volume), posts series-registration jobs to RabbitMQ and progress events to NATS. On **both** `pacs` and `local` networks. User `1001:0`, `stop_signal: SIGKILL`. `pacs` profile. |
| `nats` | `docker.io/library/nats:2.11.4-alpine` | `4222:4222` | NATS message bus. oxidicom publishes per-instance ingest-progress events here; CUBE subscribes (`NATS_ADDRESS=nats://nats:4222`). The hybrid variant-C indexer would also subscribe here. Alpine variant chosen so `wget` works in the healthcheck (`/healthz` on 8222). `pacs` profile, `local` network. |

**oxidicom environment (verbatim from compose), with meaning** (env-var semantics per <https://chrisproject.org/docs/oxidicom/deployment>):

| Env var | Value in miniChRIS | Meaning |
|---|---|---|
| `OXIDICOM_FILES_ROOT` | `/data` | Where CUBE storage is mounted; oxidicom writes received `.dcm` here. |
| `OXIDICOM_AMQP_ADDRESS` | `amqp://queue:5672` | Broker for series-registration (Celery) jobs → consumed by CUBE workers. |
| `OXIDICOM_NATS_ADDRESS` | `nats:4222` | NATS endpoint for progress events. |
| `OXIDICOM_SCP_AET` | `ChRIS` | The Application Entity Title this receiver answers to. **A pushing PACS must target AET `ChRIS`.** |
| `OXIDICOM_SCP_PROMISCUOUS` | `"true"` | Accept unknown abstract/transfer syntaxes from any peer. |
| `OXIDICOM_LISTENER_THREADS` | `32` | Concurrent DICOM associations. |
| `OXIDICOM_LISTENER_PORT` | `11111` | TCP listen port (matches published port). |
| `OXIDICOM_PROGRESS_INTERVAL` | `100ms` | Min interval between NATS progress messages. |
| `OXIDICOM_DEV_SLEEP` | `150ms` | **Demo-only throttle** so ingest is visibly slow in ChRIS_ui. Remove for realistic throughput. |
| `RUST_LOG` | `oxidicom=info` | Log level. |

> oxidicom architecture (per the docs): three async stages — **listener** (receives), **writer** (persists to `/data`), **notifier** (NATS/AMQP). It does *not* expose any HTTP/QIDO/WADO surface today; it is purely a C-STORE SCP. That is exactly why the spike's QIDO/WADO/STOW endpoints are proposed in CUBE (variant A/C) rather than inside oxidicom (variant B).

### 3.4 Optional layers (not needed for the spike, listed for completeness)

- **`pflink` profile:** `pfbridge` (`docker.io/fnndsc/pfbridge:3.7.2`, `33333:33333`), `pflink` (`docker.io/fnndsc/pflink:settings-39e91ed`, `4010:4010`), `pflink-db` (`mongo`). Orchestrates pfdcm-based pulls. `pfbridge` env wires `PACSNAME: orthanc`, `CUBEANDSWIFTKEY: local`.
- **`hasura` profile:** `graphql-engine` (`hasura/graphql-engine:v2.41.0`, `8090:8080`), `data-connector-agent` (`8081:8081`), `hasura-db` (`postgres:15`, creds `hasura:hasura1234`), `hasura-cli` (`ghcr.io/fnndsc/hasura-cli:2.41.0`, one-shot `hasura metadata apply`).

### 3.5 Init / workaround containers (default profile)

| Service | Image | Command | Purpose |
|---|---|---|---|
| `cube-nonroot-user-volume-fix` | `alpine:latest` | `chmod g+rwx /data` | Makes the shared `chris_files` volume group-writable so the non-root CUBE/oxidicom/pfcon users (uid 1001) can write. Gates the CUBE services + oxidicom via `service_completed_successfully`. |
| `pfdcm-nonroot-user-volume-fix` | `alpine:latest` | `chown 1001 /home/dicom` | Fixes ownership of pfdcm's volume. |

These init containers are a common Ansible-wrapping gotcha: any externally-managed volume must reproduce the same group-write / uid-1001 ownership or CUBE and oxidicom fail to write to `/data`.

---

## 4. Networks and volumes

**Networks** (declared at bottom of `docker-compose.yml`):

| Network | `name:` override | Members (by profile) |
|---|---|---|
| `local` | `minichris-local` | All CUBE services, `pfcon` (also `remote`), `oxidicom`, `nats`, `pfbridge`, `pflink`, hasura services, `chrisomatic` |
| `remote` | *(default-named)* | `pfcon` (alias `pfcon.host`), `pman` |
| `pacs` | *(default-named)* | `orthanc`, `pfdcm`, `oxidicom`, `nats` is on `local` (NOT `pacs`) |
| `monitoring` | declared, unused by services in this file | — |
| `pflink` | *(default-named)* | `pfbridge`, `pflink`, `pflink-db` |

Note the deliberate split: **oxidicom straddles `pacs` and `local`** so it can hear DICOM from Orthanc (`pacs`) and post jobs to RabbitMQ/NATS (`local`). Orthanc is on `pacs` only — it reaches oxidicom by the `pacs`-network hostname `oxidicom`.

**Named volumes:**

| Volume | `name:` override | Mounted by | Holds |
|---|---|---|---|
| `chris_files` | `minichris-files` | `chris`, workers, `scheduler`, `db_migrate`, `pfcon` (`/var/local/storeBase`), `pfdcm` (`/chris_files`), `oxidicom` (`/data`), both volume-fix init containers | **The DICOM/object store.** Received `.dcm` land under `SERVICES/PACS/...` here (see `clear_pacsfiles.sh`). |
| `db_data` | — | `db` | Postgres data. |
| `orthanc` | — | `orthanc` | Orthanc's own DICOM DB. |
| `pfdcm` | — | `pfdcm` | pfdcm working dir `/home/dicom`. |
| `pflink-db-data`, `hasura-db-data`, `grafana_data`, `openobserve_data` | — | optional services | (last two unused by services in this file) |

---

## 5. Credentials and config defaults (all public, all to be overridden)

From `secrets.env` (read by every CUBE service via `env_file`):

```ini
CHRIS_SUPERUSER_PASSWORD=chris1234
DJANGO_SETTINGS_MODULE=config.settings.production
DJANGO_ALLOWED_HOSTS=*
DJANGO_SECRET_KEY=secret
DJANGO_CORS_ALLOW_ALL_ORIGINS=true
POSTGRES_DB=chris
POSTGRES_USER=chris
POSTGRES_PASSWORD=chris1234
DATABASE_HOST=db
DATABASE_PORT=5432
STORAGE_ENV=fslink
MEDIA_ROOT=/data
CELERY_BROKER_URL=amqp://queue:5672
PFDCM_ADDRESS=http://pfdcm:4005
NATS_ADDRESS=nats://nats:4222
AUTH_LDAP=False
DISABLE_USER_ACCOUNT_CREATION=false
```

| What | Username | Password | Where set |
|---|---|---|---|
| ChRIS superuser (API + `/chris-admin/`) | `chris` | `chris1234` | `CHRIS_SUPERUSER_PASSWORD` + README §Default Logins |
| Postgres | `chris` | `chris1234` | `secrets.env` |
| pfcon (in-network) | `pfcon` | `pfcon1234` | compose `PFCON_USER`/`PFCON_PASSWORD` |
| Orthanc HTTP | `orthanc` | `orthanc` | used by scripts; see note below |
| Hasura DB | `hasura` | `hasura1234` | compose env |
| pflink | `pflink` | `pflink1234` | `pfbridge` env |

> **Orthanc auth nuance:** in `orthanc.json`, `RemoteAccessAllowed: true` but `AuthenticationEnabled: false`, and the `RegisteredUsers` block is **commented out**. So Orthanc's HTTP API on `:8042` accepts requests with **no** auth. The helper scripts still send `-u orthanc:orthanc` for forward-compat, but the credentials are effectively ignored in this config (`orthanc.json` lines 232–241; <https://orthanc.uclouvain.be/book/faq/security.html>).

**Public web endpoints** (README §Usage):

| UI | URL |
|---|---|
| ChRIS_ui | <http://localhost:8020/> |
| ChRIS admin (Django) | <http://localhost:8000/chris-admin/> |
| CUBE API root | <http://localhost:8000/api/v1/> |
| Orthanc (REST + Explorer + OHIF at `/ohif/`) | <http://localhost:8042/> |

---

## 6. How oxidicom and the PACS receiver are wired

The single most important wiring fact for the spike is in **`orthanc.json`**:

```jsonc
"DicomModalities" : {
   "ChRIS" : ["ChRIS", "oxidicom", 11111]   // [AET, host, port]
}
```

This registers **oxidicom as a downstream DICOM modality named `ChRIS`** inside Orthanc: AET `ChRIS`, host `oxidicom` (the compose service name, reachable on the `pacs` network), TCP port `11111` (`orthanc.json` line 325). So Orthanc can **C-STORE-push** any study it holds to oxidicom on demand. oxidicom's `OXIDICOM_SCP_AET=ChRIS` matches the AET, and `OXIDICOM_SCP_PROMISCUOUS=true` means it accepts whatever syntaxes Orthanc offers.

Other relevant Orthanc settings (`orthanc.json`): `Name: "miniChRIS Orthanc"`, `DicomAet: MINICHRISORTHANC`, `DicomPort: 4242`, `HttpPort: 8042`, `DicomServerEnabled: true`, `DicomCheckModalityHost: false`, `OverwriteInstances: false`, `DicomAlwaysAllowStore: true`, `DicomAlwaysAllowEcho: true`.

End-to-end ingest sequence:

```
1. DICOMs land in Orthanc      (HTTP STOW-like POST /instances, or C-STORE to AET MINICHRISORTHANC:4242)
2. Operator triggers a push    Orthanc → modality "ChRIS"  (C-STORE to oxidicom:11111, AET ChRIS)
3. oxidicom (C-STORE SCP)       receives, writes .dcm into /data  (chris_files volume, under SERVICES/PACS/...)
4. oxidicom → RabbitMQ          posts a series-registration job (amqp://queue:5672)
   oxidicom → NATS              streams per-instance progress (nats:4222)
5. CUBE Celery worker           consumes the job → creates PACSSeries rows (and Phase-A PACSInstance rows)
6. CUBE API / ChRIS_ui          /api/v1/pacs/... now lists the series; ChRIS_ui shows ingest progress live
```

The legacy pull path (`pfdcm`, `pflink`) is a separate orchestrator that can query a remote PACS and tell it to push into this same oxidicom receiver; `pfdcm-services/pacs.json` names that remote service `MINICHRISORTHANC` with `aet: ChRIS`, `aet_listener: ChRIS`, `serverIP: orthanc`, `serverPort: 4242`. For the spike, the relevant and sufficient path is Orthanc → oxidicom direct.

---

## 7. Bring it up / tear it down

### Up

`./minichris.sh` (the canonical bootstrap):

```bash
#!/usr/bin/env bash
cd "$(dirname "$(readlink -f "$0")")"
hn="$(hostname)" && echo "HOSTNAME=$hn" > .env   # sets HOSTNAME for chris_ui URLs
docker compose up -d "$@"                         # default-profile services + any args
# if chris is running, run the one-shot registrar:
docker compose run --rm chrisomatic               # registers compute resource + seed plugins
```

So `./minichris.sh` brings up the **default profile only**, then runs `chrisomatic`. The PACS pipeline is **not** started by the bare script — you must add the profile:

```bash
./minichris.sh                          # core CUBE + compute + chrisomatic
docker compose --profile pacs up -d     # add orthanc + pfdcm + oxidicom + nats
```

`chrisomatic.yml` registers compute resource `host` → `http://pfcon.host:5005/api/v1/` (user `pfcon`/`pfcon1234`) and plugins `pl-dircopy 3.0.0`, `pl-tsdircopy 2.0.0`, `pl-topologicalcopy 2.0.0`, `pl-simpledsapp 2.1.5`, `pl-unstack-folders 1.0.0`.

### Down

`./unmake.sh`:

```bash
#!/usr/bin/env bash
cd "$(dirname "$(readlink -f "$0")")"
set -ex
# reap plugin-instance containers pman launched on the host daemon:
pls=$(docker ps -q -a -f 'label=org.chrisproject.miniChRIS=plugininstance')
[ -z "$pls" ] || docker rm -fv $pls
# stop+remove EVERYTHING, all profiles, and named volumes (-v):
docker compose --profile pacs --profile pflink --profile hasura down -v
```

`down -v` **destroys the named volumes** (`chris_files`/`minichris-files`, `db_data`, `orthanc`, ...). It is a full wipe, consistent with "ephemeral". README troubleshooting fallback: `docker compose down -v --remove-orphans`.

### CI / GitHub Action usage

The repo doubles as a composite GitHub Action (`action.yml`, `wrapper.js`): `uses: FNNDSC/miniChRIS-docker@master` runs `minichris.sh` as the `main` step and `unmake.sh` as the `post` step. Inputs: `plugins` (whitespace list, appended to `chrisomatic.yml`) and `services` (explicit list passed to `minichris.sh`, e.g. `services: chris oxidicom` to skip workers/pfcon and start faster) (README §Github Actions / §Optimization).

---

## 8. How to push DICOM into the stack

Two practical paths, both ending at oxidicom → CUBE.

### A. Load DICOMs into Orthanc, then push to oxidicom

Upload a directory of `.dcm` to Orthanc via its REST API (this is `scripts/upload2orthanc.sh`):

```bash
# scripts/upload2orthanc.sh <dir-of-dcm>  — parallel POST each .dcm to Orthanc
fd -L --no-ignore-vcs --ignore-case --type f -e '.dcm' -j 4 \
  -x curl -sSfX POST -u orthanc:orthanc http://localhost:8042/instances \
       -H 'Expect:' -H 'Content-Type: application/dicom' -T '{}' \; \
  . <dir-of-dcm>
```

A single instance, plain `curl`:

```bash
curl -sSf -u orthanc:orthanc -X POST http://localhost:8042/instances \
  -H 'Content-Type: application/dicom' --data-binary @file.dcm
```

Then tell Orthanc to C-STORE-push the study to the `ChRIS` modality (= oxidicom). List patients / find the study, then:

```bash
# push a stored study (by Orthanc study UUID) to modality "ChRIS" (oxidicom:11111):
# simplest form — POST the bare Orthanc resource id as the body:
curl -sSf -u orthanc:orthanc -X POST \
  http://localhost:8042/modalities/ChRIS/store \
  --data '<orthanc-study-uuid>'

# or the explicit JSON-object form (lets you set Synchronous, batch multiple ids):
curl -sSf -u orthanc:orthanc -X POST \
  http://localhost:8042/modalities/ChRIS/store \
  -H 'Content-Type: application/json' \
  --data '{"Resources": ["<orthanc-study-uuid>"], "Synchronous": false}'
```

Orthanc `/modalities/{id}/store` is a **C-STORE SCU** call: Orthanc connects out to the named modality and pushes the resource. The body is the Orthanc resource identifier (patient/study/series/instance) — either as a bare id, a JSON array of ids, or a `{"Resources": [...]}` object (<https://orthanc.uclouvain.be/book/users/rest.html#sending-resources-to-remote-modalities>). The modality id `ChRIS` is the one defined in `orthanc.json` §6.

`scripts/list_mrns.sh` enumerates the Patient MRNs currently in Orthanc:

```bash
curl -sfu orthanc:orthanc http://localhost:8042/patients \
  | jq -r '.[]' \
  | xargs -I _ curl -sfu orthanc:orthanc http://localhost:8042/patients/_ \
  | jq -r '.MainDicomTags.PatientID'
```

### B. C-STORE straight into oxidicom (bypass Orthanc)

Because oxidicom is a promiscuous C-STORE SCP on `:11111` with AET `ChRIS`, any DICOM SCU can push directly. With DCMTK's `storescu`:

```bash
storescu -aec ChRIS localhost 11111 +sd +r /path/to/dicom/dir
```

Either way, files land in the `chris_files` volume under `SERVICES/PACS/...`, oxidicom posts a registration job to RabbitMQ, a CUBE worker creates `PACSSeries` rows, and the data becomes queryable at `GET /api/v1/pacs/`.

### Clearing PACS state without a full teardown

`scripts/clear_pacsfiles.sh` wipes pfdcm series logs and deletes all `PACSSeries` rows + the `SERVICES/PACS` objects from storage, via a `manage.py shell` snippet — useful to re-run ingest tests without `down -v`. The real script first does `docker compose exec chris pip install tqdm` and wraps both loops in a `tqdm(...)` progress bar; condensed here to the load-bearing logic:

```bash
docker compose exec pfdcm sh -c 'rm -rf /home/dicom/log/seriesData/*'
docker compose exec chris pip install tqdm   # the script installs tqdm into the running container first
docker compose exec chris python manage.py shell -c '
from django.conf import settings
from core.storage import connect_storage
from pacsfiles.models import PACSSeries
for s in PACSSeries.objects.all(): s.delete()
storage = connect_storage(settings)
for f in storage.ls("SERVICES/PACS"): storage.delete_obj(f)
'
```

### Sanity-checking CUBE itself

The repo's `test.sh` is a full smoke test (token auth → upload a file → run `pl-dircopy` → verify output). The minimal liveness check:

```bash
curl -u chris:chris1234 http://localhost:8000/api/v1/        # README §Github Actions
# token auth (used by test.sh):
curl -s http://localhost:8000/api/v1/auth-token/ \
  -H 'Content-Type:application/json' \
  --data '{"username":"chris","password":"chris1234"}' | jq -r .token
```

---

## 9. Implications for the Ansible wrapper (spike-specific)

1. **Wrap, don't fork.** The Ansible play should `docker compose up -d` this exact file (plus the `pacs` profile) and layer the DICOMweb work on top, so version pins (`cube:6.11.0`, `oxidicom:3.0.0`, `orthanc-plugins:1.12.7`, `nats:2.11.4-alpine`) stay authoritative.
2. **DICOMweb endpoints attach to the `chris` service** (`:8000`, `/api/v1/`) under variant A/C — no new published port needed; reuse CUBE's auth chain (token `chris:chris1234` in dev).
3. **Migrations run via the `db_migrate` one-shot** (`manage.py migrate`). Phase-A `dicomweb` app + `PACSInstance`, and future `PACSStudy` + `pg_trgm`, all migrate here against `db` (postgres:17).
4. **The variant-C hybrid indexer subscribes to `nats:4222`** — already on the `local` network, already carrying oxidicom progress events. No new bus.
5. **Volume ownership is load-bearing.** Any externally-provisioned `chris_files` mount must reproduce the `chmod g+rwx /data` / uid-1001 ownership that `cube-nonroot-user-volume-fix` establishes, or CUBE/oxidicom can't write.
6. **`OXIDICOM_DEV_SLEEP=150ms` is a demo throttle** — drop it for any throughput measurement on the BCH dataset.
7. **Secrets must be overridden.** `DJANGO_SECRET_KEY=secret`, `chris1234`, `pfcon1234`, etc. are public defaults.

---

## 10. Quick reference — published host ports

| Port | Service | Protocol / purpose |
|---|---|---|
| 8000 | `chris` | CUBE API + `/chris-admin/` (DICOMweb endpoints target here) |
| 8020 | `chris_ui` | React frontend |
| 5005 | `pfcon` | compute controller API |
| 5010 | `pman` | process manager API |
| 5672 | `queue` (RabbitMQ) | AMQP (Celery + oxidicom jobs) |
| 4222 | `nats` | NATS (oxidicom progress events) |
| 4242 | `orthanc` | DICOM C-STORE/Q-R (AET `MINICHRISORTHANC`) |
| 8042 | `orthanc` | HTTP REST + Orthanc Explorer + OHIF (`/ohif/`) + DICOMweb |
| 4005 | `pfdcm` | DICOM Q/R orchestrator API |
| 11111 | `oxidicom` | **DICOM C-STORE SCP receiver (AET `ChRIS`)** |
| 8090 | `graphql-engine` | Hasura console (`hasura` profile) |
| 8081 | `data-connector-agent` | Hasura connector (`hasura` profile) |
| 4010 | `pflink` | pflink API (`pflink` profile) |
| 33333 | `pfbridge` | pfbridge API (`pflink` profile) |

---

### Sources

- `FNNDSC/miniChRIS-docker` repo files (transcribed verbatim): `docker-compose.yml`, `secrets.env`, `chrisomatic.yml`, `orthanc.json`, `minichris.sh`, `unmake.sh`, `test.sh`, `action.yml`, `wrapper.js`, `scripts/*`, `pfdcm-services/*` — <https://github.com/FNNDSC/miniChRIS-docker>
- oxidicom env-var semantics — <https://chrisproject.org/docs/oxidicom/deployment>
- ChRIS architecture — <https://chrisproject.org/docs/architecture>
- Orthanc REST API (instances upload, modality store) — <https://orthanc.uclouvain.be/book/users/rest.html>
- Orthanc security/auth defaults — <https://orthanc.uclouvain.be/book/faq/security.html>
- Docker Compose profiles — <https://docs.docker.com/compose/profiles/>
