# Orthanc: a Test PACS / DICOMweb Reference Server

> **Why this matters for the demo.** We need (a) a known-good source of DICOM studies to push
> through the miniChRIS stack, and (b) a reference DICOMweb implementation to validate our own
> QIDO-RS / WADO-RS / STOW-RS endpoints against. Orthanc is the standard answer to both. It is a
> single small binary that can act as a C-STORE SCP/SCU **and** a full DICOMweb server, ships in a
> ready-to-run Docker image (`orthancteam/orthanc`), and has a permissive REST API for scripted
> upload of sample data. This document is the operational cheat-sheet for using it tomorrow.

Sources used throughout, cite-as-you-read:
- Orthanc Book (home): https://orthanc.uclouvain.be/book/
- REST API of the core: https://orthanc.uclouvain.be/book/users/rest.html
- DICOMweb plugin: https://orthanc.uclouvain.be/book/plugins/dicomweb.html
- `orthancteam/orthanc` Docker images: https://orthanc.uclouvain.be/book/users/docker-orthancteam.html
- `jodogne/orthanc` Docker images: https://orthanc.uclouvain.be/book/users/docker.html
- Core configuration: https://orthanc.uclouvain.be/book/users/configuration.html
- DICOM PS3.18 (Web Services / DICOMweb): https://dicom.nema.org/medical/dicom/current/output/html/part18.html
  (current published edition; DICOMweb QIDO-RS/WADO-RS/STOW-RS, media types, and the DICOM JSON Model
  Annex F are defined here). Standard index: https://www.dicomstandard.org/current

---

## 1. What Orthanc is

Orthanc is a free, open-source (GPLv3), lightweight **DICOM server** developed at UCLouvain
(Belgium). It is "a standalone DICOM server... designed to improve the DICOM flows in hospitals and
to support research about the automated analysis of medical images"
(https://orthanc.uclouvain.be/book/). For our purposes it is three things at once:

| Role | Protocol | How we use it in the demo |
|---|---|---|
| **DICOM node (PACS)** | DICOM upper-layer over TCP: C-STORE, C-FIND, C-MOVE, C-ECHO | Acts as a source that performs a **C-STORE push** into `oxidicom` (miniChRIS's C-STORE SCP). |
| **DICOMweb server** | QIDO-RS / WADO-RS / STOW-RS / WADO-URI (HTTP) | The **conformance reference**: our CUBE endpoints should behave like Orthanc's, and OHIF can point at either. Also a STOW-RS *target* and *source*. |
| **REST-managed archive** | Orthanc-native REST API over HTTP | Scripted upload of sample `.dcm` files, listing, and tag inspection. |

Internally Orthanc keeps an **index** (SQLite by default, optionally PostgreSQL/MySQL via plugin)
mapping the DICOM hierarchy — Patient → Study → Series → Instance — to opaque Orthanc IDs, and stores
the pixel data on a filesystem. Upstream the default `StorageDirectory` is `OrthancStorage`
(https://orthanc.uclouvain.be/book/users/configuration.html); the `orthancteam/orthanc` image
overrides it to `/var/lib/orthanc/db`
(https://orthanc.uclouvain.be/book/users/docker-orthancteam.html). Plugins extend it; **DICOMweb is a
plugin**, not core (https://orthanc.uclouvain.be/book/plugins/dicomweb.html).

Default identities and ports (out of the box):

| Setting | Default | Notes |
|---|---|---|
| HTTP REST API + web UI | TCP **8042** | `HttpPort`. Web UI = Orthanc Explorer (or Explorer 2). |
| DICOM SCP (C-STORE etc.) | TCP **4242** | `DicomPort`. |
| DICOM AE Title | **`ORTHANC`** | `DicomAet` (https://orthanc.uclouvain.be/book/users/configuration.html). |
| Web credentials (default jodogne img) | `orthanc` / `orthanc` | HTTP Basic (https://orthanc.uclouvain.be/book/users/docker.html). |
| DICOMweb base path | **`/dicom-web/`** | `DicomWeb.Root` (https://orthanc.uclouvain.be/book/plugins/dicomweb.html). |

> ASCII map of the moving parts:
>
> ```
>                         HTTP :8042
>        ┌───────────────────────────────────────────┐
>        │   Orthanc                                    │
>  curl ─┤   /instances        (native REST: upload)    │
>  OHIF ─┤   /studies          (native REST: list)      │
>        │   /dicom-web/...     (DICOMweb plugin)        │
>        │       /studies            QIDO-RS             │
>        │       /studies/.../        WADO-RS            │
>        │       /studies (POST)      STOW-RS            │
>        └───────────────┬─────────────────────────────┘
>                        │ DICOM C-STORE  :4242 (SCP)  /  SCU push out
>                        ▼
>                  remote DICOM nodes (e.g. oxidicom :11111)
> ```

---

## 2. Running Orthanc via Docker

Two image families exist. **Use `orthancteam/orthanc`** for the demo — it is the maintained,
ops-oriented image with env-var configuration and bundled plugins (the older `osimis/orthanc` was
renamed to `orthancteam/orthanc` on 2024-02-01)
(https://orthanc.uclouvain.be/book/users/docker-orthancteam.html). The `jodogne/orthanc` and
`jodogne/orthanc-plugins` images are the upstream author's; they are configured by mounting a JSON
file rather than env vars (https://orthanc.uclouvain.be/book/users/docker.html).

### 2.1 Quickest possible start (jodogne, plugins build)

```bash
# Bundles core + popular plugins (incl. DICOMweb). No DICOMweb config = plugin loaded but you
# still configure it. Default creds orthanc/orthanc.
docker run --rm -p 4242:4242 -p 8042:8042 jodogne/orthanc-plugins:1.12.11
```
(https://orthanc.uclouvain.be/book/users/docker.html). Browse http://localhost:8042/ and log in
`orthanc` / `orthanc`.

### 2.2 Recommended: `orthancteam/orthanc` configured by environment variables

The `orthancteam` image lets you set **any** key from Orthanc's JSON configuration through an
environment variable using the `ORTHANC__` prefix and double-underscore nesting. Conversion rule
(https://orthanc.uclouvain.be/book/users/docker-orthancteam.html):

1. Insert `_` before every capital letter inside a key:
   `DicomWeb.StudiesMetadata` → `Dicom_Web.Studies_Metadata`
2. Replace the `.` (JSON nesting) with `__`:
   `Dicom_Web__Studies_Metadata`
3. Uppercase everything and prepend `ORTHANC__`:
   **`ORTHANC__DICOM_WEB__STUDIES_METADATA`**

Worked examples (verbatim from the docs):

| JSON config | Environment variable |
|---|---|
| `StableAge` | `ORTHANC__STABLE_AGE` |
| `DicomWeb.Root` | `ORTHANC__DICOM_WEB__ROOT` |
| `DicomWeb.Enable` | `ORTHANC__DICOM_WEB__ENABLE` |
| `DicomWeb.Servers` | `ORTHANC__DICOM_WEB__SERVERS` |
| `RemoteAccessAllowed` | `ORTHANC__REMOTE_ACCESS_ALLOWED` |
| `AuthenticationEnabled` | `ORTHANC__AUTHENTICATION_ENABLED` |
| `DicomModalities` | `ORTHANC__DICOM_MODALITIES` |

**Defaults baked into the `orthancteam` image** (differ from upstream — note `RemoteAccessAllowed`
is already `true`) (https://orthanc.uclouvain.be/book/users/docker-orthancteam.html):

```json
{
  "StorageDirectory": "/var/lib/orthanc/db",
  "RemoteAccessAllowed": true,
  "AuthenticationEnabled": true,
  "Plugins": ["/usr/share/orthanc/plugins/"]
}
```

**Plugin activation:** a plugin turns on automatically as soon as you define any setting in its JSON
section, or by setting its dedicated `*_PLUGIN_ENABLED` env var to `true`. For DICOMweb either set
`ORTHANC__DICOM_WEB__ENABLE=true` (defines a setting in the `DicomWeb` section) or
`DICOM_WEB_PLUGIN_ENABLED=true` (https://orthanc.uclouvain.be/book/users/docker-orthancteam.html).

Configuration sources are merged in this order (later overrides earlier): JSON files in
`/etc/orthanc/`, Docker secrets in `/run/secrets`, environment variables, then secret environment
variables (https://orthanc.uclouvain.be/book/users/docker-orthancteam.html).

### 2.3 Standalone compose for the demo

```yaml
# orthanc-demo.yml — a reference PACS + DICOMweb server for the spike demo
services:
  orthanc:
    image: orthancteam/orthanc:24.10.1   # pin an explicit version tag (scheme like 24.10.1). The
                                         # "-full" variant only adds Azure/GCS/ODBC plugins (not needed
                                         # here); the default image suffices.
                                         # (https://orthanc.uclouvain.be/book/users/docker-orthancteam.html)
    ports:
      - "8042:8042"    # HTTP REST + DICOMweb + web UI
      - "4242:4242"    # DICOM C-STORE/C-FIND SCP
    environment:
      ORTHANC__NAME: "ISC demo PACS"
      # --- HTTP access ---
      ORTHANC__REMOTE_ACCESS_ALLOWED: "true"
      ORTHANC__AUTHENTICATION_ENABLED: "true"
      ORTHANC__REGISTERED_USERS: |
        {"demo": "demo"}                       # HTTP Basic creds: demo / demo
      # --- DICOMweb plugin ---
      ORTHANC__DICOM_WEB__ENABLE: "true"
      ORTHANC__DICOM_WEB__ROOT: "/dicom-web/"
      # --- DICOM (so Orthanc can C-STORE-push into oxidicom; see section 6) ---
      ORTHANC__DICOM_AET: "ORTHANC"
      ORTHANC__DICOM_MODALITIES: |
        {"oxidicom": ["ChRIS", "oxidicom", 11111]}
    volumes:
      - orthanc-db:/var/lib/orthanc/db
volumes:
  orthanc-db:
```

> `ORTHANC__REGISTERED_USERS` overrides the default credentials. If you set
> `ORTHANC__AUTHENTICATION_ENABLED: "false"` instead, the HTTP API is open with no Basic auth —
> convenient for a closed demo network, never for anything exposed. The `DicomModalities` entry is a
> `[AET, host, port]` triple; see section 6 for why it points at `oxidicom:11111`
> (https://orthanc.uclouvain.be/book/users/configuration.html).

Start it: `docker compose -f orthanc-demo.yml up -d`.

---

## 3. The Orthanc native REST API (uploading sample data)

Base URL `http://localhost:8042` (https://orthanc.uclouvain.be/book/users/rest.html). All examples
below add `-u demo:demo` when auth is enabled.

### 3.1 Upload a DICOM instance

POST the raw bytes of a `.dcm` to `/instances`:

```bash
curl -u demo:demo -X POST http://localhost:8042/instances \
     --data-binary @CT.X.1.2.276.0.7230010.dcm
```

For bulk speed, suppress the `Expect: 100-continue` header:

```bash
curl -u demo:demo -X POST -H "Expect:" http://localhost:8042/instances \
     --data-binary @CT.X.1.2.276.0.7230010.dcm
```

A **whole study as a ZIP** works too — Orthanc unpacks and indexes every `.dcm` inside:

```bash
curl -u demo:demo -X POST http://localhost:8042/instances \
     --data-binary @sample-study.zip
```
(https://orthanc.uclouvain.be/book/users/rest.html). The response JSON contains the new instance's
Orthanc `ID` plus its parent `ParentSeries` / `ParentStudy` / `ParentPatient` IDs.

To load a folder of `.dcm` files:

```bash
for f in ./dicom/*.dcm; do
  curl -s -u demo:demo -X POST -H "Expect:" \
       http://localhost:8042/instances --data-binary @"$f" > /dev/null
done
```

> **Where to get sample DICOM:** the Orthanc project ships sample datasets
> (https://orthanc.uclouvain.be/book/ → sample data), and pydicom bundles test files
> (`pydicom.data.get_testdata_file(...)`, https://pydicom.github.io/). Any de-identified study works.

### 3.2 List and inspect

```bash
curl -u demo:demo http://localhost:8042/patients     # -> ["<orthanc-id>", ...]
curl -u demo:demo http://localhost:8042/studies
curl -u demo:demo http://localhost:8042/series
curl -u demo:demo http://localhost:8042/instances

# detail of one study (main DICOM tags + child series IDs)
curl -u demo:demo http://localhost:8042/studies/<orthanc-study-id>

# simplified (keyword-keyed) tag dump of one instance
curl -u demo:demo http://localhost:8042/instances/<id>/simplified-tags

# one tag's raw value
curl -u demo:demo http://localhost:8042/instances/<id>/content/0010-0010   # PatientName
```
(https://orthanc.uclouvain.be/book/users/rest.html)

### 3.3 Native search (`/tools/find`)

Useful for scripting "which study did I just upload?" — note this is the **Orthanc-proprietary**
query, *not* QIDO-RS (that's section 4):

```bash
curl -u demo:demo -X POST http://localhost:8042/tools/find --data '{
  "Level": "Study",
  "Query": { "PatientID": "*", "StudyDate": "20180323-" },
  "Expand": true
}'
```
`"Expand": true` returns full resource objects instead of bare IDs
(https://orthanc.uclouvain.be/book/users/rest.html).

### 3.4 Download instances / previews

```bash
curl -u demo:demo http://localhost:8042/instances/<id>/file    > Instance.dcm  # raw DICOM
curl -u demo:demo http://localhost:8042/instances/<id>/preview > Preview.png   # rendered PNG
```
(https://orthanc.uclouvain.be/book/users/rest.html)

---

## 4. The DICOMweb plugin (our conformance reference)

When the plugin is enabled, Orthanc serves DICOMweb under `DicomWeb.Root` (default `/dicom-web/`).
It implements **QIDO-RS, WADO-RS, STOW-RS, and the legacy WADO-URI**, per DICOM PS3.18
(https://orthanc.uclouvain.be/book/plugins/dicomweb.html). This is exactly the surface CUBE must
grow (see `proposal-to-bch/CURRENT_API.md` and `QIDO_PLAN.md`), so Orthanc is the behavioural oracle.

### 4.1 Key DICOMweb config options

| Option | Default | Purpose |
|---|---|---|
| `DicomWeb.Enable` | `true` (once section present) | Master switch for the plugin. |
| `DicomWeb.Root` | `/dicom-web/` | Base URI prefix for the QIDO/WADO/STOW endpoints. |
| `DicomWeb.EnableWado` | `true` | Enables the legacy WADO-**URI** endpoint (`/wado`). |
| `DicomWeb.WadoRoot` | `/wado` | Path for WADO-URI. |
| `DicomWeb.QidoCaseSensitive` | `true` | Whether QIDO-RS string matching is case-sensitive. |
| `DicomWeb.StudiesMetadata` | `Full` | Metadata mode: `Full` / `MainDicomTags` / `Extrapolate` (perf vs. completeness). |
| `DicomWeb.SeriesMetadata` | `Full` | Same, at series level. |
| `DicomWeb.Servers` | — | Named **remote** DICOMweb servers Orthanc can act as a *client* to. |

(https://orthanc.uclouvain.be/book/plugins/dicomweb.html)

### 4.2 QIDO-RS — query (Orthanc as server)

Search the hierarchy; responses are **DICOM JSON Model** (`application/dicom+json`) — tag-hex-keyed
objects with `vr` + `Value`, the exact format CUBE must emit
(https://orthanc.uclouvain.be/book/plugins/dicomweb.html; format defined in DICOM PS3.18).

```bash
# all studies
curl -u demo:demo http://localhost:8042/dicom-web/studies

# studies for a patient name (keyword form)
curl -u demo:demo "http://localhost:8042/dicom-web/studies?PatientName=VIX"

# same, DICOM tag-hex form  (0010,0010 = PatientName)
curl -u demo:demo "http://localhost:8042/dicom-web/studies?00100010=VIX*"

# series within a study
curl -u demo:demo "http://localhost:8042/dicom-web/studies/<StudyInstanceUID>/series"

# instances within a series
curl -u demo:demo "http://localhost:8042/dicom-web/studies/<StudyUID>/series/<SeriesUID>/instances"

# ask for extra return attributes + paging
curl -u demo:demo "http://localhost:8042/dicom-web/studies?ModalitiesInStudy=CT&includefield=00081030&limit=25&offset=0"
```

The QIDO resource paths Orthanc exposes — `/studies`, `/studies/{uid}/series`,
`/studies/{uid}/series/{uid}/instances`, plus flat `/series` and `/instances` — are precisely the
paths the CUBE gap analysis says we must add (`proposal-to-bch/CURRENT_API.md`, "QIDO-RS gap").
Validate our JSON byte-for-byte against Orthanc's for an equivalent dataset.

### 4.3 WADO-RS — retrieve

```bash
# whole study as a multipart/related stream of application/dicom parts
curl -u demo:demo "http://localhost:8042/dicom-web/studies/<StudyUID>"

# study-level metadata only (DICOM JSON, no pixels)
curl -u demo:demo "http://localhost:8042/dicom-web/studies/<StudyUID>/metadata"

# one rendered frame as PNG
curl -u demo:demo \
  "http://localhost:8042/dicom-web/studies/<StudyUID>/series/<SeriesUID>/instances/<InstanceUID>/frames/1/rendered" \
  -H 'Accept: image/png'
```
WADO-RS returns `application/dicom` as a `multipart/related` stream for bulk retrieval, or rendered
formats (PNG/JPEG) for `/rendered` (https://orthanc.uclouvain.be/book/plugins/dicomweb.html). CUBE
today only serves a single `.dcm` as `application/octet-stream` via `/api/v1/pacs/files/{id}/.…`, so
the multipart packaging is net-new work (see `CURRENT_API.md`).

### 4.4 STOW-RS — store

STOW-RS uploads instances to a DICOMweb server with a `multipart/related; type="application/dicom"`
body. Against Orthanc:

```bash
# minimal STOW-RS push of one instance to Orthanc's own /studies
curl -u demo:demo -X POST "http://localhost:8042/dicom-web/studies" \
  -H 'Expect:' \
  -H 'Content-Type: multipart/related; type="application/dicom"; boundary=B' \
  --data-binary @stow-body.bin
```
Building the multipart body by hand is fiddly; for the demo prefer a client library. **pydicom +
the `dicomweb-client` package** is the clean path:

```python
from dicomweb_client.api import DICOMwebClient
from dicomweb_client.session_utils import create_session_from_user_pass
from pydicom import dcmread

# NB: DICOMwebClient does NOT take username=/password= kwargs. HTTP Basic auth is
# supplied through a requests Session built by the session_utils helper.
session = create_session_from_user_pass(username="demo", password="demo")
client = DICOMwebClient(
    url="http://localhost:8042/dicom-web",
    session=session,
)
ds = dcmread("CT.dcm")
client.store_instances(datasets=[ds])             # STOW-RS
studies = client.search_for_studies()             # QIDO-RS  (-> list of DICOM-JSON dicts)
inst   = client.retrieve_instance(                # WADO-RS  (-> pydicom Dataset)
    study_instance_uid=study_uid,
    series_instance_uid=series_uid,
    sop_instance_uid=sop_uid,
)
```
(dicomweb-client API: https://dicomweb-client.readthedocs.io/en/latest/usage.html and
https://dicomweb-client.readthedocs.io/en/latest/package.html ; pydicom: https://pydicom.github.io/ ;
DICOMweb semantics: PS3.18.) This same `dicomweb-client`
script is the natural smoke-test harness to run against **CUBE's** STOW/QIDO/WADO once implemented —
just change the `url`.

### 4.5 Orthanc as a DICOMweb *client* (handy for pulling into our stack)

Orthanc can also reach out to a remote DICOMweb server defined in `DicomWeb.Servers` and pull/push:

```bash
# list configured remote DICOMweb servers
curl -u demo:demo http://localhost:8042/dicom-web/servers/

# tell Orthanc to STOW some of its local resources to a remote server "name"
curl -u demo:demo -X POST http://localhost:8042/dicom-web/servers/<name>/stow \
     -H 'Content-Type: application/json' \
     -d '{ "Resources": ["<orthanc-study-or-instance-id>"] }'
```
(https://orthanc.uclouvain.be/book/plugins/dicomweb.html). Once CUBE's STOW-RS endpoint exists, we
can register CUBE as a `DicomWeb.Servers` entry and let Orthanc push sample studies straight into
CUBE over DICOMweb — a second ingestion demo alongside the C-STORE path.

---

## 5. WADO-URI (legacy, FYI only)

Older viewers use WADO-URI (single-frame retrieval by query params), enabled by `DicomWeb.EnableWado`
at `DicomWeb.WadoRoot` (default `/wado`):

```bash
# WADO-URI requires requestType=WADO plus studyUID/seriesUID/objectUID (and optionally contentType)
curl -u demo:demo "http://localhost:8042/wado?requestType=WADO\
&studyUID=<StudyInstanceUID>&seriesUID=<SeriesInstanceUID>&objectUID=<SOPInstanceUID>\
&contentType=application/dicom"
```
(https://orthanc.uclouvain.be/book/plugins/dicomweb.html ; WADO-URI parameters defined in DICOM
PS3.18). Not part of our QIDO/WADO-RS/STOW-RS
scope; mentioned so it isn't confused with WADO-**RS**.

---

## 6. Feeding sample DICOM into the miniChRIS stack

The decided demo deployment **wraps FNNDSC/miniChRIS-docker**. In that stack, DICOM enters CUBE via
**`oxidicom`** (image `ghcr.io/fnndsc/oxidicom:3.0.0`), the Rust C-STORE SCP that listens on TCP
**11111** (`OXIDICOM_LISTENER_PORT=11111`), writes received `.dcm` files under `/data` (CUBE storage,
`OXIDICOM_FILES_ROOT=/data`), and publishes ingest events on **NATS** (`OXIDICOM_NATS_ADDRESS=nats:4222`)
and RabbitMQ (`OXIDICOM_AMQP_ADDRESS=amqp://queue:5672`). Series registration into CUBE happens through
the internal `POST /api/v1/pacs/series/` callback, whose serializer waits for the `.dcm` files to land
and then bulk-creates the `PACSFile` rows (see `proposal-to-bch/CURRENT_API.md` lines 106 and 160-161,
and `RESEARCH_TICKET_OUTPUT.md`). Service names/ports/images above are read directly from the
miniChRIS-docker compose (https://github.com/FNNDSC/miniChRIS-docker — `docker-compose.yml`).

> **Heads-up — miniChRIS already ships its own Orthanc.** The miniChRIS compose includes an `orthanc`
> service (`docker.io/jodogne/orthanc-plugins:1.12.7`, ports **4242:4242** and **8042:8042**), typically
> behind a `pacs`/PACS compose profile. If you run our `orthanc-demo.yml` Orthanc on the host at the
> same 8042/4242, you will collide with miniChRIS's Orthanc whenever that profile is up. Either reuse
> the bundled Orthanc, remap host ports (e.g. `18042:8042`, `14242:4242`), or only start one of them.
> (https://github.com/FNNDSC/miniChRIS-docker)

Note also that miniChRIS's `oxidicom` runs with `OXIDICOM_SCP_AET=ChRIS` and
`OXIDICOM_SCP_PROMISCUOUS=true`, i.e. it accepts **any** calling AET, which simplifies the C-STORE
push below (https://github.com/FNNDSC/miniChRIS-docker).

There are two clean ways to use Orthanc to deliver studies into that pipeline.

### 6.1 Path A (primary) — Orthanc C-STORE push → oxidicom

Orthanc, holding sample studies (loaded via section 3.1), performs a DICOM **C-STORE** to oxidicom.
This exercises the real production ingest path.

```
   sample.dcm ──REST POST /instances──▶  Orthanc  ──C-STORE :11111──▶  oxidicom  ──writes /data + NATS──▶  CUBE
                                       (:8042/:4242)                  (miniChRIS)                       (PACSSeries row)
```

Steps:

1. Register oxidicom as a DICOM modality in Orthanc (already in the compose env of section 2.3):

   ```json
   "DicomModalities": { "oxidicom": ["ChRIS", "oxidicom", 11111] }
   ```
   The triple is `[remote-AET, host, port]`
   (https://orthanc.uclouvain.be/book/users/configuration.html). Use the Docker-network service name
   `oxidicom` as host if Orthanc runs in the same compose project; otherwise the host's IP.

   The first element of the triple is the **called/remote AET**. In miniChRIS, oxidicom's
   `OXIDICOM_SCP_AET=ChRIS`, so `"ChRIS"` is the correct called AET. Because miniChRIS also sets
   `OXIDICOM_SCP_PROMISCUOUS=true`, oxidicom accepts any **calling** AET, so Orthanc's own
   `DicomAet` (`ORTHANC`) needs no special handling. (oxidicom env vars verified from the
   miniChRIS compose: https://github.com/FNNDSC/miniChRIS-docker ; overview:
   https://chrisproject.org/docs/oxidicom. Re-check both before the demo in case the deployment
   pins a non-promiscuous AET.)

2. Verify connectivity (C-ECHO):

   ```bash
   curl -u demo:demo -X POST http://localhost:8042/modalities/oxidicom/echo
   ```

3. Push a study (by Orthanc resource ID) over C-STORE:

   ```bash
   # object/advanced form (lets you pass LocalAet, Synchronous, Timeout, …)
   curl -u demo:demo -X POST http://localhost:8042/modalities/oxidicom/store \
        -H 'Content-Type: application/json' \
        -d '{ "Resources": ["<orthanc-study-id>"] }'

   # simplest documented forms also work: a bare ID string, or a JSON array of IDs
   curl -u demo:demo -X POST http://localhost:8042/modalities/oxidicom/store \
        -d '["<orthanc-study-id>"]'
   ```
   The body for `POST /modalities/{name}/store` may be a single resource-ID string, a JSON array of
   IDs, or an object with a `"Resources"` key plus options (`LocalAet`, `Synchronous`, `Timeout`,
   `StorageCommitment`) — all three are documented (REST modality store:
   https://orthanc.uclouvain.be/book/users/rest.html ; modality config:
   https://orthanc.uclouvain.be/book/users/configuration.html).

4. Watch ingest land in CUBE: the miniChRIS `/api/v1/pacs/sse/` SSE stream and the new `PACSSeries`
   row confirm receipt (`proposal-to-bch/CURRENT_API.md`).

### 6.2 Path B — STOW-RS straight into CUBE (once CUBE STOW-RS exists)

After Phase C lands STOW-RS in CUBE, skip oxidicom and have Orthanc (or `dicomweb-client`) STOW the
study to CUBE's DICOMweb root. Either drive it from the Python client in section 4.4 (point `url` at
CUBE) or register CUBE under Orthanc's `DicomWeb.Servers` and use
`POST /dicom-web/servers/<cube>/stow` (section 4.5). This demonstrates the new STOW endpoint without
touching the C-STORE machinery.

### 6.3 Path C — Orthanc as the OHIF/viewer target for cross-checking

Point OHIF at Orthanc's `/dicom-web/` and at CUBE's future `/dicom-web/` and compare. If a study
renders from Orthanc but not from CUBE, the divergence is in CUBE's QIDO/WADO responses — fast way to
find conformance bugs during the demo.

---

## 7. Gotchas and demo-day checklist

- **`orthancteam` vs `jodogne` images differ.** Env-var config (`ORTHANC__...`) only works on
  `orthancteam/orthanc`. The `jodogne` images expect a mounted JSON config
  (https://orthanc.uclouvain.be/book/users/docker.html vs.
  https://orthanc.uclouvain.be/book/users/docker-orthancteam.html).
- **DICOMweb is a plugin.** If `/dicom-web/studies` 404s, the plugin isn't loaded — set
  `ORTHANC__DICOM_WEB__ENABLE=true` (or `DICOM_WEB_PLUGIN_ENABLED=true`) and confirm the image
  bundles the plugin (the plugins/full images do)
  (https://orthanc.uclouvain.be/book/users/docker-orthancteam.html).
- **Auth.** `orthancteam` ships `AuthenticationEnabled: true` and `RemoteAccessAllowed: true`. With
  no `RegisteredUsers`/credentials defined the HTTP API may reject you; set
  `ORTHANC__REGISTERED_USERS` or disable auth on a closed network
  (https://orthanc.uclouvain.be/book/users/docker-orthancteam.html).
- **`/tools/find` ≠ QIDO-RS.** `/tools/find` is Orthanc-proprietary; QIDO-RS lives under
  `/dicom-web/`. Don't validate our QIDO output against `/tools/find` JSON.
- **`StudiesMetadata: Full`** is correct but slower on big archives; fine for a small demo dataset
  (https://orthanc.uclouvain.be/book/plugins/dicomweb.html).
- **Network names.** In Docker Compose, Orthanc reaches oxidicom by its service name on the shared
  network; ensure both compose projects share a network (or run Orthanc inside the miniChRIS compose
  project) and that oxidicom's `:11111` is reachable.
- **AE-title match.** The single most common C-STORE failure is an AET mismatch; verify Orthanc's
  modality entry against oxidicom's accepted AET before the meeting.

---

## 8. One-paragraph talking point for the meeting

> "Orthanc is our reference DICOMweb server and our test data source. We run it from the maintained
> `orthancteam/orthanc` Docker image, configured entirely through `ORTHANC__`-prefixed environment
> variables, on the standard ports 8042 (HTTP/DICOMweb) and 4242 (DICOM). We script sample studies
> in via its native REST API (`POST /instances`), then either C-STORE-push them into miniChRIS's
> oxidicom receiver on port 11111 to exercise the real ingest path, or — once our STOW-RS endpoint
> lands — STOW them directly into CUBE. Critically, Orthanc's `/dicom-web/` endpoints (QIDO-RS,
> WADO-RS, STOW-RS) give us a byte-level conformance oracle: we point the same `dicomweb-client`
> test harness and the same OHIF viewer at both Orthanc and CUBE and diff the behaviour."

---

### Source index
- Orthanc Book home — https://orthanc.uclouvain.be/book/
- REST API of the core — https://orthanc.uclouvain.be/book/users/rest.html
- DICOMweb plugin — https://orthanc.uclouvain.be/book/plugins/dicomweb.html
- orthancteam/orthanc Docker images — https://orthanc.uclouvain.be/book/users/docker-orthancteam.html
- jodogne/orthanc Docker images — https://orthanc.uclouvain.be/book/users/docker.html
- Core configuration — https://orthanc.uclouvain.be/book/users/configuration.html
- pydicom — https://pydicom.github.io/
- oxidicom — https://chrisproject.org/docs/oxidicom (verify exact ports/AETs against miniChRIS compose)
- miniChRIS-docker — https://github.com/FNNDSC/miniChRIS-docker
- Prior spike artifacts — `proposal-to-bch/CURRENT_API.md`, `proposal-to-bch/RESEARCH_TICKET_OUTPUT.md`, `proposal-to-bch/QIDO_PLAN.md`
