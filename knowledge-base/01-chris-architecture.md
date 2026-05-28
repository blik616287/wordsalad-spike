# ChRIS Platform Architecture

> Knowledge-base reference for the ARPA-H ATLAS DICOMweb spike on **ChRIS_ultron_backEnd (CUBE)**.
> Purpose: give an ISC engineer enough end-to-end grounding to be the technical voice in the BCH
> stakeholder meeting. Every non-obvious claim is cited inline.
>
> Primary sources: [chrisproject.org/docs/architecture](https://chrisproject.org/docs/architecture),
> the [FNNDSC GitHub org](https://github.com/FNNDSC), and the
> [miniChRIS-docker compose stack](https://github.com/FNNDSC/miniChRIS-docker). Internal grounding:
> `proposal-to-bch/CURRENT_API.md` and `proposal-to-bch/RESEARCH_TICKET_OUTPUT.md` in this repo.

---

## 1. What ChRIS is

**ChRIS** (the *ChRIS Research Integration Service*) is an open-source, container-native platform for
running medical-image analysis at scale. It was created at **FNNDSC** (the Fetal-Neonatal Neuroimaging
& Developmental Science Center at Boston Children's Hospital) and is developed in the open under the
[FNNDSC GitHub organization](https://github.com/FNNDSC).

The core idea: a researcher (or an automated clinical pipeline) takes medical data — typically DICOM
imaging pulled from a hospital PACS — and runs a tree of **containerized analysis plugins** against it.
ChRIS handles the bookkeeping (users, files, jobs), schedules each plugin as a container on whatever
compute backend is available (Docker, Podman, Swarm, Kubernetes, SLURM), moves the data in and out, and
records the provenance of every result. The platform is explicitly designed so the *same* plugin can run
unchanged on a laptop, an on-prem cluster, or a public cloud.

The unit of work that the user sees is a **Feed**: the root entity of an analysis, under which a tree of
**plugin instances** executes. (Feed/Note/Comment/Tag form the root tree in CUBE — see
`proposal-to-bch/CURRENT_API.md`.)

---

## 2. Component map (the 60-second version)

| Component | Repo | Language | Role |
|---|---|---|---|
| **CUBE** (ChRIS_ultron_backEnd) | `FNNDSC/ChRIS_ultron_backEnd` | Django / Python | The backend brain: REST API, the database of users, files and jobs, the plugin/feed/pipeline model, auth. "responsible for maintaining a database of users, files, and computational jobs" ([architecture](https://chrisproject.org/docs/architecture)). |
| **ChRIS_ui** | `FNNDSC/ChRIS_ui` | TypeScript / React | The web frontend; "makes HTTP requests to the backend API" ([architecture](https://chrisproject.org/docs/architecture)). For image display it bundles **Cornerstone3D** (`@cornerstonejs/core`, `@cornerstonejs/tools`, `@cornerstonejs/dicom-image-loader`) and **Niivue** (`@niivue/niivue`) — *not* OHIF (verified in [ChRIS_ui `package.json`](https://github.com/FNNDSC/ChRIS_ui/blob/main/package.json); no `@ohif/*` dependency exists). |
| **ChRIS_store** | `FNNDSC/ChRIS_store` | Django / Python | A separate registry of plugin descriptions (the "app store"). CUBE pulls plugin `description.json` from a store to register a plugin. |
| **pfcon** | `FNNDSC/pfcon` | Python (Flask) | "data and compute CONtroller" — the unified web API in front of a compute resource; brokers files + job lifecycle ([pfcon README](https://github.com/FNNDSC/pfcon)). |
| **pman** | `FNNDSC/pman` | Python | "a shim that translates a job request from CUBE to a call to the compute resource cluster" ([architecture](https://chrisproject.org/docs/architecture)). Sits behind pfcon and talks to Docker/Swarm/K8s/SLURM. |
| **oxidicom** | `FNNDSC/oxidicom` | Rust | "a high-performance DICOM receiver for the ChRIS backend (CUBE)" implementing "a DICOM C-STORE service class provider (SCP)" ([oxidicom docs](https://chrisproject.org/docs/oxidicom)). This is the DICOM front door. |
| **pfdcm** | `FNNDSC/pfdcm` | Python | DICOM C-FIND/C-MOVE bridge to *upstream* hospital PACS. CUBE POSTs a query directive to pfdcm to pull studies in. |
| **NATS** | (upstream `nats`) | — | Lightweight message broker between oxidicom and CUBE for DICOM-reception progress events ([oxidicom architecture](https://chrisproject.org/docs/oxidicom/architecture)). |
| **RabbitMQ + Celery** | (upstream) | — | CUBE's async task queue. Plugin-instance scheduling, PACS registration, DICOM indexing all run as Celery tasks. |
| **PostgreSQL** | (upstream) | — | CUBE's relational store. |
| **Object storage** | Swift / S3 / POSIX fslink | — | The unified file tree where all data (uploads, PACS DICOMs, plugin outputs) physically lives. Abstracted in CUBE behind `core.storage`. |
| **Orthanc** | (upstream `jodogne/orthanc`) | — | In miniChRIS, a stand-in upstream PACS used to test C-STORE/C-FIND end-to-end ([miniChRIS-docker](https://github.com/FNNDSC/miniChRIS-docker)). |

> Note: `pfdcm`, `oxidicom`, and `serie` are the components that "integrate ChRIS with specialized
> features in the hospital environment" ([architecture](https://chrisproject.org/docs/architecture)).
> **serie** is an automation service that watches for newly-received series and auto-launches a pipeline
> for clinical analysis — relevant context but not on the DICOMweb critical path.

---

## 3. ASCII component diagram

```
                                  ┌──────────────────────────────────────────┐
                                  │                BROWSER                     │
                                  │  ChRIS_ui (React) + Cornerstone3D/Niivue   │
                                  └───────────────┬────────────────────────────┘
                                                  │  HTTPS  (REST + collection+json / JSON)
                                                  │  [QIDO/WADO/STOW would ride here too]
                                                  ▼
   ┌──────────────────────────────────────────────────────────────────────────────────────┐
   │                              CUBE  (ChRIS_ultron_backEnd, Django)                       │
   │   :8000  REST API  •  auth (Token/Basic/Session/LDAP)  •  feed/plugin/pipeline model    │
   │                                                                                          │
   │   ┌──────────────┐   ┌───────────────┐   ┌──────────────────┐   ┌────────────────────┐  │
   │   │ Django views │   │ Celery workers│   │ pacsfiles app    │   │ dicomweb app       │  │
   │   │  + DRF       │   │ (main1/main2/ │   │ (PACS models,    │   │ (PACSInstance +    │  │
   │   │              │   │  periodic)    │   │  oxidicom hook)  │   │  index task) <-NEW │  │
   │   └──────┬───────┘   └──────┬────────┘   └────────┬─────────┘   └─────────┬──────────┘  │
   └──────────┼──────────────────┼─────────────────────┼───────────────────────┼────────────┘
              │                  │                      │                       │
   ┌──────────▼──┐   ┌───────────▼────────┐   ┌─────────▼──────────┐   ┌────────▼─────────┐
   │ PostgreSQL  │   │ RabbitMQ (:5672)   │   │  NATS  (:4222)     │   │ Object storage   │
   │  (users,    │   │  + Celery queues   │   │  LONK progress     │   │  Swift / S3 /    │
   │  files,jobs)│   │                    │   │  subjects          │   │  POSIX fslink    │
   └─────────────┘   └────────────────────┘   └─────────▲──────────┘   │  unified /data   │
                                                         │              │  file tree       │
              ┌──────────────────┐                       │              └────────▲─────────┘
              │ COMPUTE SIDE      │                       │                       │ writes .dcm
              │                   │                       │                       │ under
   CUBE ─────▶│ pfcon (:5005) ───▶│ pman (:5010) ───▶ Docker/Swarm/K8s/SLURM      │ SERVICES/PACS/
   submit job │  (file broker)    │  (scheduler shim)  runs plugin container      │
              └───────────────────┘                                     ┌────────┴─────────┐
                                                                        │ oxidicom (Rust)  │
   ┌─────────────┐    C-MOVE / C-FIND      ┌───────────────┐  C-STORE   │ DICOM SCP :11111 │
   │ Hospital    │◀───────────────────────▶│ pfdcm (:4005) │───────────▶│ writes files +   │
   │ PACS        │                          │ pull bridge   │  (push)    │ publishes LONK   │
   │ (Orthanc in │                          └───────────────┘            │ events to NATS   │
   │  miniChRIS) │                                                        └──────────────────┘
   └─────────────┘
```

Ports above are the host mappings from
[miniChRIS-docker's docker-compose.yml](https://raw.githubusercontent.com/FNNDSC/miniChRIS-docker/master/docker-compose.yml)
(see §7 table).

---

## 4. The plugin & pipeline compute model

### 4.1 What a plugin is

A **ChRIS plugin** is a containerized command-line program plus a machine-readable description
(`description.json`) declaring its parameters and I/O contract. Plugins come in types:

- **`fs` (feed-source)** — takes *no* input directory; generates data into its output (e.g. `pl-dircopy`,
  `pl-pacscopy` pull data into a new feed). An `fs` plugin is what creates a **top-level feed**
  (see [CUBE wiki: FS plugin workflow](https://github.com/FNNDSC/ChRIS_ultron_backEnd/wiki/1.2-ChRIS-FS-plugin-workflow:-upload-files-to-CUBE-and-create-a-new-top-level-feed-(pl-pacscopy))).
- **`ds` (data-source)** — takes an input directory (the output of a parent plugin instance) and writes a
  new output directory. This is the workhorse type; chaining `ds` plugins builds the analysis tree.
- **`ts` (topology-source)** — joins/reshapes multiple parent branches.

Plugin descriptions are registered into CUBE from a **ChRIS_store** (or uploaded directly via
`POST /chris-admin/api/v1/...` with a `description.json`, per `proposal-to-bch/CURRENT_API.md`).

### 4.2 Feeds and plugin instances

Running an `fs` plugin creates a **Feed** (the analysis root). Each subsequent run is a **plugin
instance** whose parent is another plugin instance, forming a DAG/tree under the feed. CUBE persists
every instance, its parameter values, its status, and its output directory — the full provenance of the
analysis. A **pipeline** is a saved template of pipings (plugin → plugin edges with default parameters); a
**workflow** is one execution of a pipeline (see the `pipelines` domain in `proposal-to-bch/CURRENT_API.md`).

### 4.3 How a plugin instance actually runs (the lifecycle)

```
1. Client POSTs to create a plugin instance (params + parent) ──▶ CUBE writes a PluginInstance row (status=started)
2. CUBE's Celery worker (queue "main1") picks up run_plugin_instance_job
3. CUBE asks pfcon (:5005) to run the job, handing over the input files
        • IN-NETWORK pfcon: reads input objects straight from shared CUBE storage (Swift/POSIX)
        • OUT-OF-NETWORK pfcon: CUBE POSTs a multipart zip of all input files to pfcon
4. pfcon → pman (:5010): pman translates the job into a concrete scheduler call
   (Docker / Podman / Swarm / Kubernetes / SLURM) and launches the plugin container
5. The container runs:  plugin_exec  <input_dir>  <output_dir>
6. CUBE polls pfcon for status (check_plugin_instance_job_exec_status, also "main1")
7. On completion:
        • IN-NETWORK: outputs already land in shared storage
        • OUT-OF-NETWORK: CUBE downloads the output zip back from pfcon
8. CUBE registers output files in the DB under the instance's output folder; status=finishedSuccessfully
```

The two pfcon transfer modes are the key file-movement design point
([pfcon README](https://github.com/FNNDSC/pfcon) /
[CUBE↔pfcon discussion](https://github.com/FNNDSC/CHRIS_docs/discussions/43)):

- **In-network** — pfcon "has direct access to the shared ChRIS's storage environment (currently either
  Swift object storage or a POSIX filesystem)." pfcon receives the *path* of the instance's input
  directory and reads objects directly. Faster, no double-copy.
- **Out-of-network** — pfcon "can accept a zip file (as part of a multipart POST request) containing all
  the input files for the plugin job ... the output data ... can then be downloaded back as a zip file
  after the job is finished." Note the documented memory cost: the zip path buffers in memory at ~150% of
  data size.

> **pman is the scheduler abstraction.** pfcon is the *file + API* layer; pman is the *"shim that
> translates a job request from CUBE to a call to the compute resource cluster"*
> ([architecture](https://chrisproject.org/docs/architecture)). Swapping Docker→Kubernetes→SLURM is a
> pman concern; CUBE and pfcon are unchanged.

### 4.4 Celery queue layout (relevant to DICOMweb work)

CUBE routes async work across named queues (`chris_backend/core/celery.py`):

- **`main1`** — latency-sensitive plugin-instance state machine (`run_plugin_instance_job`,
  `check_plugin_instance_job_exec_status`).
- **`main2`** — side-effect tasks: PACS series deletion, PACS query send, series registration, and the
  **new DICOM-instance indexing task** (`dicomweb.tasks.index_pacs_instance`). Per the Phase A writeup,
  indexing was deliberately put on `main2` so bursty per-file work does not starve `main1` (see
  `proposal-to-bch/PHASE_A_IMPLEMENTATION.md`).
- **`periodic`** — the Celery-beat cron schedule.

---

## 5. The data path: where DICOM lands

There are **two distinct ways** DICOM enters CUBE. This distinction is the crux of the architectural
question in the spike (see `proposal-to-bch/RESEARCH_TICKET_OUTPUT.md`).

### 5.1 Push path — oxidicom (the DICOM front door)

`oxidicom` is a Rust **C-STORE SCP**: it listens on TCP **port 11111** and receives DICOM objects pushed
to it ([oxidicom docs](https://chrisproject.org/docs/oxidicom);
[miniChRIS compose](https://raw.githubusercontent.com/FNNDSC/miniChRIS-docker/master/docker-compose.yml)).
Its documented end-to-end flow ([oxidicom architecture](https://chrisproject.org/docs/oxidicom/architecture)):

1. "oxidicom receives DICOM data from PACS"
2. "oxidicom stores the file in CUBE's storage" — files land under the unified storage tree at
   `SERVICES/PACS/<pacs_name>/...` (the folder convention CUBE's `pacsfiles` app expects; see
   `proposal-to-bch/CURRENT_API.md`). The storage root in the container is `OXIDICOM_FILES_ROOT: /data`.
3. "oxidicom sends messages about DICOM study reception progress to NATS"
4. "oxidicom sends a task to Celery" — concretely, a **`register_pacs_series`** Celery task published over
   AMQP/RabbitMQ when the association completes (verified in
   [`src/celery_publisher.rs`](https://github.com/FNNDSC/oxidicom/blob/master/src/celery_publisher.rs):
   `celery_task_name = "register_pacs_series"`). This is a *per-series* registration, not per-file.
5. "CUBE's celery worker registers received DICOM data to the database"

Key oxidicom environment variables (from the miniChRIS compose):

```yaml
OXIDICOM_FILES_ROOT:       /data            # storage root it writes .dcm files into
OXIDICOM_AMQP_ADDRESS:     amqp://queue:5672 # RabbitMQ — to hand the registration task to CUBE's Celery
OXIDICOM_SCP_AET:          ChRIS            # the AE Title it answers C-STORE as
OXIDICOM_LISTENER_PORT:    11111            # DICOM C-STORE TCP port
OXIDICOM_PROGRESS_INTERVAL: 100ms           # how often it emits progress
```

**LONK** — the NATS progress protocol oxidicom speaks
([oxidicom LONK docs](https://chrisproject.org/docs/oxidicom/lonk)). "LONK" = *Light Oxidicom
NotifiKations encoding*. Subjects are published as:

```
oxidicom.{pacs_name}.{SeriesInstanceUID}      # see sanitization note below
```

The leading `oxidicom` is the configurable **root subject** (`OXIDICOM_ROOT_SUBJECT`), whose default
value is the literal string `"oxidicom"` (verified in
[`src/settings.rs` `default_root_subject()`](https://github.com/FNNDSC/oxidicom/blob/master/src/settings.rs)).
The `pacs_name` and `SeriesInstanceUID` parts are sanitized for NATS by replacing each of the characters
space, `.`, `*`, and `>` with `_` (so the dots in a UID become underscores, but it is not *only* dots) —
verified in [`src/lonk.rs` `subject_of()` / `sanitize_subject_part()`](https://github.com/FNNDSC/oxidicom/blob/master/src/lonk.rs).
There is **no** `.ndicom` suffix on the subject.

Message payloads are single-magic-byte framed:

| Magic byte | Meaning | Payload |
|---|---|---|
| `0x01` | **progress** (`ndicom`) | little-endian `u32` = count of DICOM files received so far |
| `0x00` | **done** | (none) — no further messages for that series |
| `0x02` | **error** | UTF-8 error string |

(Magic-byte constants verified in [`src/lonk.rs`](https://github.com/FNNDSC/oxidicom/blob/master/src/lonk.rs):
`MESSAGE_NDICOM = 0x01`, `DONE_MESSAGE = [0x00]`, `MESSAGE_ERROR = 0x02`; progress payload is
`ndicom.to_le_bytes()`.)

CUBE relays these to clients over the `GET /api/v1/pacs/sse/` Server-Sent-Events stream, which subscribes
to the NATS subjects (`?pacs_name=...&series_uids=...`) — see `proposal-to-bch/CURRENT_API.md`. The DB
registration itself happens via the internal `POST /api/v1/pacs/series/` callback / Celery task, which
bulk-creates the `PACSFile` rows once the `.dcm` files have landed.

### 5.2 Pull path — pfdcm (querying an upstream hospital PACS)

`pfdcm` (host port **4005**) is the bridge to a real upstream PACS for **C-FIND** (query) and **C-MOVE**
(retrieve). CUBE's PACS queries do *not* talk to the remote PACS directly: CUBE POSTs a `PACSdirective`
to pfdcm (`/api/v1/PACS/sync/pypx/`) and stores the compressed JSON result on `PACSQuery.result`; a
retrieve triggers a C-MOVE that ultimately lands DICOMs back through the oxidicom C-STORE path above
(see `proposal-to-bch/CURRENT_API.md`, "Auxiliary moving parts"). In miniChRIS, **Orthanc** (ports
4242 DICOM / 8042 HTTP) plays the role of that upstream PACS for testing.

### 5.3 What CUBE stores about DICOM today

The `pacsfiles` app collapses Patient/Study/Series tags onto a single **`PACSSeries`** row (unique on
`(pacs, SeriesInstanceUID)`); the raw `.dcm` files hang off a `ChrisFolder` via the generic `PACSFile`
model. **There is no instance-level row today** — per-`.dcm` tags live only inside the files on disk
(`proposal-to-bch/CURRENT_API.md`). Phase A of this spike already added a **`dicomweb`** Django app with a
**`PACSInstance`** model (one row per `.dcm`, FK to `PACSSeries`, one-to-one to `PACSFile`) populated by
the `index_pacs_instance` Celery task reading headers with `pydicom`
(`proposal-to-bch/PHASE_A_IMPLEMENTATION.md`).

---

## 6. Frontend: ChRIS_ui and its image viewers

**ChRIS_ui** is the React single-page app (host port **8020**) that drives CUBE entirely over its REST
API ([architecture](https://chrisproject.org/docs/architecture)). For image display it bundles
**Cornerstone3D** and **Niivue** directly (deps `@cornerstonejs/core`, `@cornerstonejs/tools`,
`@cornerstonejs/dicom-image-loader`, and `@niivue/niivue` in
[ChRIS_ui `package.json`](https://github.com/FNNDSC/ChRIS_ui/blob/main/package.json)) — **it does not embed
the OHIF viewer**, and there is no `@ohif/*` dependency.

This matters for the spike, and actually *strengthens* the DICOMweb case: Cornerstone3D's
`@cornerstonejs/dicom-image-loader` ships in-tree `wado-rs` and `wado-uri` image loaders — i.e. it is
already a DICOMweb-capable client that can pull DICOM P10 instances from a **WADO-RS** server
([Cornerstone DICOM Image Loader docs](https://www.cornerstonejs.org/docs/concepts/cornerstone-core/imageloader/)).
Today ChRIS_ui consumes the bespoke `/api/v1/pacs/...` endpoints; standing up a WADO-RS (and QIDO-RS)
surface on CUBE is what would let the *existing* Cornerstone loader — and any third-party DICOMweb client
(OHIF, 3D Slicer, Weasis) — talk to CUBE through the standard protocol instead of CUBE-specific routes.

> Caveat to state precisely tomorrow: the value is "CUBE gains a standard DICOMweb interface that the
> existing Cornerstone3D loader and external tools can consume," **not** "OHIF is already wired up and just
> needs an endpoint." No OHIF is present.

---

## 7. Deployment topology (miniChRIS-docker) — what we wrap

The decided deployment for this spike is to **wrap the existing FNNDSC/miniChRIS-docker compose stack**.
Services and host port mappings, from
[miniChRIS-docker's docker-compose.yml](https://raw.githubusercontent.com/FNNDSC/miniChRIS-docker/master/docker-compose.yml):

| Service | Image | Host:Container ports | Role |
|---|---|---|---|
| `chris` (CUBE) | `ghcr.io/fnndsc/cube:6.11.0` | `8000:8000` | Backend API |
| `chris_ui` | `ghcr.io/fnndsc/chris_ui:d65741e` | `8020:8020` | Frontend (Cornerstone3D + Niivue) |
| `pfcon` | `ghcr.io/fnndsc/pfcon:5.2.3` | `5005:5005` | Compute/file controller |
| `pman` | `ghcr.io/fnndsc/pman:6.2.0` | `5010:5010` | Scheduler shim |
| `db` | `postgres:17` | (internal) | PostgreSQL |
| `queue` | `rabbitmq:3` | `5672:5672` | Celery broker |
| `nats` | `nats:2.11.4-alpine` | `4222:4222` | LONK progress bus |
| `oxidicom` | `ghcr.io/fnndsc/oxidicom:3.0.0` | `11111:11111` | DICOM C-STORE SCP |
| `pfdcm` | `ghcr.io/fnndsc/pfdcm:3.1.2` | `4005:4005` | Upstream PACS pull bridge |
| `orthanc` | `jodogne/orthanc-plugins:1.12.7` | `4242:4242`, `8042:8042` | Test upstream PACS |
| `worker` / `worker_periodic` / `scheduler` | (cube image) | (internal) | Celery workers + beat |
| `db_migrate` | (cube image) | (internal) | One-shot migrations |
| `chrisomatic` | `ghcr.io/fnndsc/chrisomatic:1.0.0` | (internal) | Plugin install/setup |
| `pfbridge` | `fnndsc/pfbridge:3.7.2` | `33333:33333` | Bridge service (CUBE↔pflink) |
| `pflink` | `fnndsc/pflink:settings-39e91ed` | `4010:4010` | Workflow-orchestration service over pfdcm/CUBE |
| `pflink-db` | `mongo` | (internal) | pflink's MongoDB |
| `graphql-engine` | `hasura/graphql-engine:v2.41.0` | `8090:8080` | Hasura GraphQL over CUBE's DB |
| `data-connector-agent` | `hasura/graphql-data-connector:v2.40.0` | `8081:8081` | Hasura data-connector agent |
| `hasura-db` | `postgres:15` | (internal) | Hasura metadata DB |
| `hasura-cli` | `ghcr.io/fnndsc/hasura-cli:2.41.0` | (internal) | Hasura migration/setup |

Default superuser: **`chris:chris1234`**; admin dashboard at `http://localhost:8000/chris-admin/`. The
repo warns it "is not suitable for production. It contains hard-coded secrets and insecure defaults"
([miniChRIS-docker](https://github.com/FNNDSC/miniChRIS-docker)).

> **No dedicated Swift/MinIO container is declared in this compose file** — miniChRIS uses a
> POSIX/filesystem storage backend for CUBE, so our wrapper must not assume a separate object-store
> container exists. There is also no separate viewer container: the in-browser DICOM viewer
> (Cornerstone3D + Niivue) is part of the `chris_ui` SPA bundle, not a standalone service.
>
> Note the full compose also brings up several services *not* on the DICOMweb critical path — `pfbridge`,
> `pflink` (+ `pflink-db` Mongo), and a Hasura GraphQL stack (`graphql-engine`, `data-connector-agent`,
> `hasura-db`, `hasura-cli`). Our wrapper should leave these running but does not depend on them.

---

## 8. Where the DICOMweb endpoints fit

DICOMweb is the standard HTTP face of DICOM (PS3.18): **QIDO-RS** (query), **WADO-RS** (retrieve),
**STOW-RS** (store). Mapping the three onto this architecture:

- **QIDO-RS** (search) → a *read* surface over the CUBE database. It queries the
  Patient→Study→Series→Instance hierarchy and returns the DICOM JSON Model
  (`application/dicom+json`). This naturally lives **inside CUBE (Django)**, querying `PACSSeries` +
  the new `PACSInstance` (and a planned `PACSStudy`) tables. CUBE is also where auth already lives.
- **WADO-RS** (retrieve) → a *read* surface that streams `application/dicom` (multipart/related) and
  derivatives (`/metadata`, `/frames`, `/rendered`, `/thumbnail`). The bytes come straight out of
  `core.storage` (Swift/S3/POSIX), located via `PACSInstance.pacs_file.fname`. Also a CUBE concern.
- **STOW-RS** (store) → a *write/ingest* surface. This is the one that overlaps the oxidicom push path:
  STOW-RS is an *alternative ingestion route* alongside C-STORE. Whether it lands in CUBE (Django writes
  files to storage + indexes) or is fronted differently is the open architecture question.

The spike's recommended placement (from `proposal-to-bch/RESEARCH_TICKET_OUTPUT.md`):

> **Variant C (hybrid):** QIDO/WADO/STOW endpoints stay in Django (CUBE) because that is where auth and
> the API surface already live; the *indexing* of received DICOM is driven primarily by a **NATS
> consumer** subscribing to oxidicom's LONK events (oxidicom already parses the tags during C-STORE, so
> Python should not re-read every file), with the Phase A `pydicom` Celery task as the fallback path for
> any non-oxidicom-sourced files (STOW-RS uploads, plugin outputs, bulk S3 import). Fall back to
> **Variant B** (endpoints in oxidicom/Rust) only if oxidicom is confirmed as the *single* ingestion path.

Why CUBE and not oxidicom for the HTTP surface: CUBE's auth chain (Token / Basic / Session / LDAP-backed)
is non-trivial to reproduce in Rust, and a single auth-aware DICOMweb endpoint per site is the cleaner
seam for the grant's planned ATLAS DICOMweb federation gateway
(`proposal-to-bch/RESEARCH_TICKET_OUTPUT.md`).

**Open factual question to raise tomorrow:** *Is oxidicom the only intended DICOM ingestion path going
forward, or are STOW-RS / plugin-output / bulk-S3 paths also in scope?* The answer decides B vs C.

---

## 9. Quick-reference curl examples

Authenticate and read the current (non-DICOMweb) PACS surface against a wrapped miniChRIS stack:

```sh
# Get an auth token (or just use Basic chris:chris1234 in dev)
curl -s -u chris:chris1234 \
  -H 'Content-Type: application/vnd.collection+json' \
  http://localhost:8000/api/v1/auth-token/ \
  -d '{"username":"chris","password":"chris1234"}'

# List registered PACS sources (side effect: reconciles against pfdcm)
curl -s -u chris:chris1234 -H 'Accept: application/json' \
  http://localhost:8000/api/v1/pacs/

# Search series by DICOM tag (today: keyword-only, exact/icontains)
curl -s -u chris:chris1234 -H 'Accept: application/json' \
  'http://localhost:8000/api/v1/pacs/series/search/?Modality=CT&PatientName=DOE'

# Download a single .dcm (today: octet-stream, NOT WADO-RS multipart)
curl -s -u chris:chris1234 \
  'http://localhost:8000/api/v1/pacs/files/42/.dcm?download_token=<tok>' -o slice.dcm
```

What the *future* DICOMweb routes would look like (shape, not yet implemented past Phase A):

```sh
# QIDO-RS study search  → application/dicom+json
curl -s -H 'Accept: application/dicom+json' \
  'http://localhost:8000/dicom-web/studies?00080060=CT&PatientName=DOE*'

# WADO-RS instance retrieve → multipart/related; type="application/dicom"
curl -s -H 'Accept: multipart/related; type="application/dicom"' \
  'http://localhost:8000/dicom-web/studies/<StudyUID>/series/<SeriesUID>/instances/<SOPUID>'

# STOW-RS store a study → multipart/related; type="application/dicom"
curl -s -X POST -H 'Content-Type: multipart/related; type="application/dicom"' \
  --data-binary @study.multipart \
  'http://localhost:8000/dicom-web/studies'
```

---

## 10. Talking points for the meeting

1. **ChRIS is a container-orchestration platform for medical imaging**: CUBE (Django) is the brain;
   pfcon+pman run plugin containers on any scheduler; ChRIS_ui (React, with Cornerstone3D + Niivue
   viewers) is the face.
2. **DICOM enters two ways**: pushed via **oxidicom** (Rust C-STORE SCP, port 11111, writes to
   `SERVICES/PACS/`, emits LONK progress on NATS) or pulled via **pfdcm** (C-FIND/C-MOVE bridge to an
   upstream PACS / Orthanc).
3. **All files live in one abstracted storage tree** (`core.storage` → Swift / S3 / POSIX). WADO-RS just
   streams from there.
4. **DICOMweb belongs in CUBE** because that is where auth and the API surface already are, and because
   the frontend's existing **Cornerstone3D** loader is already a WADO-RS/WADO-URI DICOMweb client (no
   OHIF involved). Phase A already added the `PACSInstance` index the QIDO/WADO layer needs.
5. **The one decision we need from BCH**: is oxidicom the sole ingestion path? That picks hybrid-NATS
   (variant C) vs. Rust-endpoints (variant B).
6. **Deployment wraps miniChRIS-docker** — no separate object-store or viewer container; the Cornerstone3D
   + Niivue viewers ship inside the `chris_ui` SPA bundle, and storage is POSIX in dev. (The full compose
   also runs pfbridge/pflink and a Hasura GraphQL stack, which we leave alone.)
