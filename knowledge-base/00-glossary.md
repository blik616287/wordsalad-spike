# 00 — Glossary

> One-sentence definitions for every term an outsider or stakeholder might trip over in this spike.
> Definitions are pulled from the KB files so they stay consistent; the parenthetical points to where
> the term is developed. Use with [`00-INDEX.md`](00-INDEX.md) and the meeting brief
> ([`../MEETING_BRIEF.md`](../MEETING_BRIEF.md)). Entries are alphabetical (acronyms by their letters).

---

- **AE Title (AET)** — Application Entity Title, the (≤16-char) name of a DICOM network node; oxidicom answers C-STORE as the *called* AET `ChRIS`, while the *calling* AET of the pushing SCU becomes the CUBE PACS identifier. ([04](04-dicom-standard.md) §4; [11](11-oxidicom-and-ingestion.md) §3)

- **AMQP** — the messaging protocol RabbitMQ speaks (port 5672); it carries CUBE's Celery tasks and the series-registration job oxidicom posts. ([10](10-cube-internals.md) §6; [03](03-minichris-docker.md) §3.3)

- **Anonymization / de-identification** — stripping PHI from DICOM (direct tags + private tags + sequence-nested data, with consistent UID remapping); out of scope for this spike (grant §2.7.5 is its own workstream). ([06](06-pydicom.md) §8.1; [15](15-engagement-and-scoping.md) §8)

- **ARPA-H / ATLAS** — ARPA-H is the U.S. Advanced Research Projects Agency for Health; **ATLAS** (*Adaptive Technology for Large-scale Academic Sciences*) is the ~$60M+ ARPA-H grant program funding this work (prime Gradient Health; partners BCH + University of Utah), whose §2.6.1.6 sub-task makes QIDO+WADO+STOW a joint Month-12 deliverable. ([15](15-engagement-and-scoping.md) §1; [08](08-l2-architecture-decisions.md) D3, D5)

- **ASGI / uvicorn** — CUBE runs as an asynchronous (ASGI) app under uvicorn (not plain WSGI), which is what makes the SSE/WebSocket DICOM-progress channels possible. ([10](10-cube-internals.md) §0)

- **`auto_silent`** — an Ansible *interpreter-discovery keyword* (not a path) set in the deploy inventory; deploy bug #2 was a prereq check that mistakenly used it as a literal interpreter path. ([13](13-deployment.md) §8; [14](14-testing-and-validation.md) §6.1)

- **baked image** — the production-correct alternative to the dev overlay: build `cube:dev` from source with the `dicomweb` app + `pydicom` already inside, so there's no runtime patching or version skew. ([13](13-deployment.md) §10)

- **BCH** — Boston Children's Hospital (home of FNNDSC and the ChRIS platform); the client in the room and the primary technical execution partner on ATLAS. ([15](15-engagement-and-scoping.md) §1)

- **BulkDataURI** — in the DICOM JSON Model, a URL (instead of an inline `Value`) where a large/binary attribute can be fetched via WADO-RS; CUBE's WADO `/metadata` emits `PixelData` as a `BulkDataURI` to its `/frames/1`. ([05](05-dicomweb-qido-wado-stow.md) §1.1; [12](12-l2-dicomweb-implementation.md) §5.2)

- **C-ECHO** — the DICOM "verification" message (a fully negotiated association); it is the *safe* way to health-check oxidicom, unlike a raw TCP probe which crashes it. ([11](11-oxidicom-and-ingestion.md) §7.4)

- **C-FIND / C-MOVE** — the classic DIMSE query (C-FIND) and retrieve (C-MOVE) messages CUBE issues against an upstream PACS via pfdcm — the DICOM *pull* path. ([01](01-chris-architecture.md) §5.2; [02](02-cube-and-pacs-data-model.md) §3.5)

- **C-STORE** — the DIMSE message that pushes a DICOM instance to a receiver; oxidicom is a C-STORE SCP, and STOW-RS is C-STORE's HTTP analog. ([04](04-dicom-standard.md) §0; [11](11-oxidicom-and-ingestion.md) §2)

- **Celery** — CUBE's async task framework, run by worker processes against named queues; the indexer and PACS registration run here. ([10](10-cube-internals.md) §6)

- **Channels** — Django Channels, the WebSocket layer in CUBE's ASGI stack that powers the LONK WebSocket consumer. ([10](10-cube-internals.md) §0, §9)

- **ChRIS** — the *ChRIS Research Integration Service*, an open-source, container-native platform (FNNDSC / Boston Children's Hospital) for running trees of containerized analysis plugins against medical imaging at scale. ([01](01-chris-architecture.md) §1)

- **`ChrisFile`** — the generic CUBE model for one stored file (bytes in object storage, metadata in Postgres); `PACSFile` and `UserFile` are path-prefix-filtered proxies of it. ([10](10-cube-internals.md) §2.3)

- **`ChrisFolder`** — the CUBE directory-node model; the folder tree is materialized by the `path` string (parents auto-created on save), and a PACS file finds its owning series by folder ancestry, not an FK. ([10](10-cube-internals.md) §2.2)

- **`ChrisInstance`** — a singleton CUBE model (forced `id=1`) holding the deployment's identity and the `job_id_prefix` used to name remote compute jobs. ([10](10-cube-internals.md) §2.7)

- **`ChrisLinkFile`** — CUBE's "symlink": a stored `*.chrislink` text file whose contents are a target path, used to share/expose data (`SHARED/`, `PUBLIC/`) without copying bytes. ([10](10-cube-internals.md) §2.4)

- **ChRIS_store** — a separate registry of plugin descriptions (the ChRIS "app store") that CUBE pulls `description.json` from to register plugins. ([01](01-chris-architecture.md) §2)

- **ChRIS_ui** — the React single-page frontend (host port 8020) that drives CUBE over its REST API and bundles the Cornerstone3D + Niivue in-browser DICOM viewers (it does **not** embed OHIF). ([01](01-chris-architecture.md) §6)

- **chrisomatic** — a one-shot declarative registrar that installs the compute resource and seed plugins into a fresh miniChRIS stack. ([03](03-minichris-docker.md) §3.2)

- **collection+json** — the hypermedia format (`application/vnd.collection+json`) that is CUBE's default DRF renderer, wrapping data in `links`/`items`/`queries`/`template` envelopes — exactly what DICOMweb deliberately breaks from (it speaks `application/dicom+json` on a separate URL mount). ([09](09-cube-rest-api.md) §3; [02](02-cube-and-pacs-data-model.md) §2)

- **`COMPOSE_FILE` / `!override`** — the Compose mechanism the deploy uses to remap CUBE's host port without editing the wrapped compose file: a tracked override (`cube-port.yml`) chained via `COMPOSE_FILE`, with `!override` *replacing* (not appending) the ports list. ([13](13-deployment.md) §4)

- **`connect_storage`** — the single CUBE chokepoint that returns the configured `StorageManager` (swift / s3 / fslink) based on the `STORAGE_ENV` setting. ([10](10-cube-internals.md) §3.3)

- **`consume_dicomweb_index`** — the L2 NATS-subscriber management command that consumes the (future) oxidicom tag event on subject `oxidicom-meta.>` and feeds `index_from_metadata` (the variant-C consumer). ([12](12-l2-dicomweb-implementation.md) §7.2)

- **Cornerstone3D** — the in-browser medical-image rendering library ChRIS_ui bundles (`@cornerstonejs/*`), whose `dicom-image-loader` is already a WADO-RS/WADO-URI DICOMweb client. ([01](01-chris-architecture.md) §6)

- **CSRF (`@csrf_exempt`)** — Django cross-site-request-forgery protection; L2 bug #10 was STOW POST returning 403 because `csrf_exempt` must sit on the *outer* dispatcher View, not the inner DRF view. ([12](12-l2-dicomweb-implementation.md) §8.3; [14](14-testing-and-validation.md) §6.2)

- **CUBE (ChRIS_ultron_backEnd)** — the Django + Django REST Framework backend that is ChRIS's "brain": the REST API, the DB of users/files/jobs, the feed/plugin/pipeline model, and the auth chain. ([02](02-cube-and-pacs-data-model.md) §1)

- **`/dicom-web/pacs/<id>/`** — the per-PACS URL root under which the L2 QIDO/WADO/STOW endpoints mount (e.g. `<id>` = `BCH` or `ChRIS`), separate from CUBE's `/api/v1/` router. ([12](12-l2-dicomweb-implementation.md) §0, §8.1)

- **dcm4che / dcm4chee** — a reference enterprise DICOM toolkit/archive whose DICOMweb behavior (and STOW failure-reason codes) is used as a cross-check alongside Orthanc and Azure; its `stowrs` CLI is a STOW test client. ([05](05-dicomweb-qido-wado-stow.md) §4)

- **`dcmread`** — pydicom's reader; CUBE calls it `dcmread(..., stop_before_pixels=True, force=True)` to parse only the `.dcm` header (fast, preamble-tolerant). ([06](06-pydicom.md) §1)

- **DICM** — the 4-byte magic at offset 128 in a PS3.10 `.dcm` file that confirms it is DICOM Part-10; `force=True` reads files lacking it. ([04](04-dicom-standard.md) §6)

- **DICOM** — *Digital Imaging and Communications in Medicine*, the medical-imaging standard that is *both* a file format (`.dcm`) and a network-protocol family, maintained by NEMA as the multi-part PS3.x standard. ([04](04-dicom-standard.md) §0)

- **DICOM JSON Model** — the PS3.18 Annex F response format (`application/dicom+json`): a JSON object keyed by 8-hex-digit tags, each value carrying a `"vr"` plus at most one of `Value` / `BulkDataURI` / `InlineBinary` (note PN serializes as `[{"Alphabetic": "DOE^JANE"}]`, not a bare string). ([05](05-dicomweb-qido-wado-stow.md) §1; [04](04-dicom-standard.md) §11)

- **DICOMweb** — the HTTP/REST face of DICOM defined in PS3.18, comprising QIDO-RS (query), WADO-RS (retrieve), and STOW-RS (store); adding these three to CUBE is the entire ATLAS deliverable. ([05](05-dicomweb-qido-wado-stow.md) §0)

- **`dicomweb-client`** — the Python DICOMweb client library (`DICOMwebClient.store_instances(...)`, `search_for_studies(...)`) used as the smoke-test harness against both Orthanc and CUBE. ([07](07-orthanc.md) §4.4)

- **`dicomweb` app** — the new Django app (Phase A + L2) holding the `PACSInstance`/`PACSStudy` models, the indexer, the DICOM-JSON renderer, the query parser, and the QIDO/WADO/STOW views, deliberately isolated from `pacsfiles`. ([02](02-cube-and-pacs-data-model.md) §6.1; [12](12-l2-dicomweb-implementation.md) §0)

- **DIMSE** — the classic DICOM message service over TCP/IP (C-STORE, C-FIND, C-MOVE, C-ECHO), as opposed to the HTTP-based DICOMweb; never TCP-probe a DIMSE port. ([04](04-dicom-standard.md) §13; [13](13-deployment.md) §8)

- **download token** — a short-lived single-use JWT CUBE mints (`POST /downloadtokens/`) and accepts as `?download_token=` so file bytes can be fetched from contexts (an `<img>` tag) that can't set an `Authorization` header. ([09](09-cube-rest-api.md) §4.3)

- **DRF (Django REST Framework)** — the toolkit CUBE's API is built on; its `DEFAULT_AUTHENTICATION_CLASSES` give DICOMweb its Token/Basic/Session auth for free. ([09](09-cube-rest-api.md) §2)

- **drf-spectacular** — the OpenAPI 3.0.3 schema generator CUBE uses; a post-processing hook strips the collection+json envelope so the spec describes the flat JSON shape, and bare streaming/DICOMweb views need explicit annotations. ([09](09-cube-rest-api.md) §9)

- **`ds` plugin** — a "data-source" plugin type that takes an input directory and writes a new output directory (the workhorse for chaining an analysis tree). ([01](01-chris-architecture.md) §4.1)

- **`enforce_file_format`** — the pydicom 3.x `dcmwrite`/`save_as` argument (replaces 2.x `write_like_original`) that writes a conformant Part-10 stream and requires a `TransferSyntaxUID`. ([06](06-pydicom.md) §5.1)

- **FailedSOPSequence (0008,1198)** — the STOW response sequence listing instances that failed to store, each carrying a numeric `FailureReason` (emitted as decimal in the JSON body, e.g. `0xA901` → 43265). ([05](05-dicomweb-qido-wado-stow.md) §4.4; [12](12-l2-dicomweb-implementation.md) §6.4)

- **Feed** — the root entity of an analysis in ChRIS, under which a tree of plugin instances executes. ([01](01-chris-architecture.md) §4.2)

- **`FileDataset`** — the pydicom object `dcmread` returns: a `Dataset` that also carries `file_meta`, `preamble`, and the source filename. ([06](06-pydicom.md) §1.2)

- **Flux GitRepository / HelmRelease** — GitOps custom resources used by ChRIS's *production* Helm path (`fnndsc/charts`); out of scope for this spike, which uses miniChRIS + Ansible. ([03](03-minichris-docker.md) §1)

- **`force=True`** — the pydicom flag that reads `.dcm` files lacking the preamble/`DICM` magic; the indexer uses it (legacy tolerance), STOW uses `force=False` (reject non-conformant uploads). ([06](06-pydicom.md) §1.1)

- **`fs` plugin** — a "feed-source" plugin type that takes no input directory and generates data, creating a new top-level feed (e.g. `pl-dircopy`, `pl-pacscopy`). ([01](01-chris-architecture.md) §4.1)

- **fslink** — CUBE's plain-POSIX-filesystem storage backend (`FilesystemManager` rooted at `MEDIA_ROOT`), the fastest single-node mode and the one miniChRIS uses. ([10](10-cube-internals.md) §3.4)

- **fuzzymatching** — the optional QIDO query parameter (`fuzzymatching=true`) for approximate `PN` matching; in L2 it emits `__trigram_similar` and needs both the `pg_trgm` extension *and* a registered trigram lookup. ([12](12-l2-dicomweb-implementation.md) §4.5; [08](08-l2-architecture-decisions.md) D4)

- **Gradient Health (GH)** — the prime contractor on the ATLAS grant; ISC's DICOMweb work is the BCH portion, and GH's "ATLAS DICOMweb gateway" is the Phase-2 federation layer above per-TD endpoints. ([15](15-engagement-and-scoping.md) §1, §11)

- **HATEOAS / hypermedia** — the "follow `links`/`queries`/`template`" API style CUBE adopted via collection+json so the backend can move URLs without breaking the generic `@fnndsc/chrisapi` client. ([09](09-cube-rest-api.md) §1, §3.5)

- **Hasura** — an optional GraphQL layer over CUBE's DB included in miniChRIS (`hasura` profile); not needed for the spike. ([03](03-minichris-docker.md) §3.4)

- **`includefield`** — the QIDO parameter requesting extra return attributes; in L2 it is a conformant no-op because the serializers always emit the full indexed superset (only un-indexed tags can't be surfaced). ([05](05-dicomweb-qido-wado-stow.md) §2.3; [12](12-l2-dicomweb-implementation.md) §3)

- **`index_from_metadata`** — the L2 variant-C indexer that upserts `PACSInstance`/`PACSStudy` from a tags message with **no** storage read and **no** pydicom parse. ([12](12-l2-dicomweb-implementation.md) §7.2)

- **`index_pacs_instance`** — the Phase A Celery indexer that re-reads a `.dcm` header with pydicom and upserts the `PACSInstance` row (routed to queue `main2`, fanned out on `post_save(PACSFile)`). ([02](02-cube-and-pacs-data-model.md) §6.4; [12](12-l2-dicomweb-implementation.md) §7.1)

- **indexing** — parsing DICOM tags out of files *once*, at ingest, into queryable Postgres rows so QIDO never re-reads the `.dcm`; "move QIDO to oxidicom" is about *where endpoints serve*, not where the index lives. ([08](08-l2-architecture-decisions.md) §0)

- **InlineBinary** — the DICOM JSON Model option that base64-embeds binary directly in the response (vs `BulkDataURI`); CUBE does not inline pixels. ([05](05-dicomweb-qido-wado-stow.md) §1.1)

- **IOD** — Information Object Definition, the attribute schema a given SOP Class must satisfy. ([04](04-dicom-standard.md) §13)

- **ISC** — Insight Softmax Consulting, the firm doing the spike (Alex Scammon, Tommy "Aldo" Sonin, Adam McArthur, Marty). ([15](15-engagement-and-scoping.md) §1)

- **L1 / L2 / L3 / L4** — the engagement layering: L1 research spike (done), L2 MVP implementation (endpoints on one CUBE + the BCH dataset), L3 grant §2.6.1.6 (operational within TD constraints + regression suite), L4 broader pipeline (aspirational). ([08](08-l2-architecture-decisions.md) D5; [15](15-engagement-and-scoping.md) §3)

- **LDAP / `CustomLDAPBackend`** — the optional directory-auth backend CUBE wires before Django's `ModelBackend`; the DICOMweb endpoints inherit it for free by staying in Django. ([10](10-cube-internals.md) §8.2; [02](02-cube-and-pacs-data-model.md) §2)

- **LONK** — "Light Oxidicom NotifiKations," oxidicom's NATS progress protocol carrying exactly three message kinds — `progress` (`0x01` + a little-endian file count), `done` (`0x00`), `error` (`0x02`) — on subject `oxidicom.<pacs>.<SeriesInstanceUID>`, with **no metadata/tags**. ([11](11-oxidicom-and-ingestion.md) §5; [01](01-chris-architecture.md) §5.1)

- **`main1`** — the latency-sensitive Celery queue carrying the plugin-instance job-launch task (`run_plugin_instance_job`). ([10](10-cube-internals.md) §6.1)

- **`main2`** — the Celery queue for secondary work — status checks, deletions, pfdcm queries, `register_pacs_series`, and the DICOMweb `index_pacs_instance` task — kept off `main1` so bookkeeping never blocks job launch. ([10](10-cube-internals.md) §6.1; [02](02-cube-and-pacs-data-model.md) §6.5)

- **MEDIA_ROOT** — the storage root path (`/data` in miniChRIS) the `fslink` backend and oxidicom (`OXIDICOM_FILES_ROOT`) write into. ([10](10-cube-internals.md) §3.4; [03](03-minichris-docker.md) §3.3)

- **miniChRIS-docker** — the single-compose, demo-grade distribution of the whole ChRIS backend that the spike's deployment **wraps** (never forks); its DICOM ingest pipeline is gated behind the `pacs` compose profile. ([03](03-minichris-docker.md) §1–§2)

- **MOC (Mass Open Cloud)** — the cloud where an ATLAS ChRIS instance may run; whether one is already up is an open question for BCH. ([15](15-engagement-and-scoping.md) §6)

- **Modality** — the `(0008,0060)` code string naming the acquisition type, fixed per series (e.g. `CT`, `MR`, `US`, `PT`, `SR`). ([04](04-dicom-standard.md) §9)

- **ModalitiesInStudy (0008,0061)** — the study-level list of distinct modality codes across a study's series, computed/denormalized (in L2, stored backslash-joined on `PACSStudy`). ([04](04-dicom-standard.md) §9; [12](12-l2-dicomweb-implementation.md) §1.2)

- **multipart/related** — the RFC 2387 MIME body (NOT `multipart/form-data`) that WADO-RS returns and STOW-RS accepts, one DICOM part per instance; CUBE hand-rolls a parser/streamer for it. ([05](05-dicomweb-qido-wado-stow.md) §3.3, §4.2; [12](12-l2-dicomweb-implementation.md) §5.1, §6.1)

- **NATS** — a lightweight message broker (port 4222) used **only** for oxidicom→CUBE DICOM-reception *progress* events today; it carries no DICOM tags (variant C would add a new tag-bearing subject `oxidicom-meta.>`). ([01](01-chris-architecture.md) §5.1; [11](11-oxidicom-and-ingestion.md) §5)

- **Niivue** — a WebGL neuroimaging viewer (`@niivue/niivue`) ChRIS_ui bundles alongside Cornerstone3D. ([01](01-chris-architecture.md) §6)

- **OHIF** — the open-source Open Health Imaging Foundation web DICOM viewer; in this stack OHIF is **only** served by the bundled Orthanc plugin (`/ohif/` on :8042), **not** by ChRIS_ui — but a standard WADO/QIDO surface on CUBE is what any OHIF/3D-Slicer/Weasis client would consume. ([01](01-chris-architecture.md) §6; [03](03-minichris-docker.md) §3.2)

- **`/opt/app-root/src`** — the Django project root inside the prebuilt `cube:6.11.0` UBI image; the overlay copies the L2 app there (deploy bug #5 was targeting the wrong path). ([10](10-cube-internals.md) §0; [13](13-deployment.md) §8)

- **OpenAPI / `schema.yaml`** — CUBE's machine-readable API spec (141 paths, 220 operations, OpenAPI 3.0.3) dumped via drf-spectacular. ([09](09-cube-rest-api.md) §9; [02](02-cube-and-pacs-data-model.md) §2)

- **Orthanc** — a lightweight open-source DICOM server used in this spike as both the test data source (C-STORE push into oxidicom) and the DICOMweb conformance oracle (its `/dicom-web/` QIDO/WADO/STOW is the behavioral reference to diff CUBE against). ([07](07-orthanc.md) §1, §4)

- **`orthancteam/orthanc` vs `jodogne/orthanc`** — the two Orthanc Docker image families: `orthancteam` is env-var-configurable (`ORTHANC__*`) and used by the deploy's test PACS; `jodogne` (in miniChRIS) is JSON-file-configured. ([07](07-orthanc.md) §2)

- **overlay** — the dev-convenience deploy seam that `docker cp`s the L2 app into a *running* CUBE container, installs pydicom, wires `INSTALLED_APPS`/urls, migrates, and restarts — fragile by construction; production uses a baked image instead. ([13](13-deployment.md) §9–§10)

- **oxidicom** — the Rust **C-STORE SCP** that is CUBE's DICOM "front door": it listens on TCP 11111 (AET `ChRIS`), writes received `.dcm` into `SERVICES/PACS/`, publishes LONK progress on NATS, and enqueues a `register_pacs_series` Celery task; it parses tags in Rust but emits none on NATS today. ([11](11-oxidicom-and-ingestion.md) §1–§8; [01](01-chris-architecture.md) §5.1)

- **`OXIDICOM_DEV_SLEEP`** — a demo-only oxidicom throttle (150ms in miniChRIS) that slows ingest so progress is visible in ChRIS_ui; drop it for throughput tests. ([03](03-minichris-docker.md) §3.3; [13](13-deployment.md) §6)

- **`OXIDICOM_SCP_AET` / `OXIDICOM_SCP_PROMISCUOUS`** — the *called* AET oxidicom answers as (`ChRIS`) and the flag that makes it accept any calling AET / syntax. ([11](11-oxidicom-and-ingestion.md) §9)

- **PACS** — Picture Archiving and Communication System, a hospital's medical-image store/network node; in CUBE a `PACS` row is one registered upstream DICOM source (e.g. `BCH`, `MINICHRISORTHANC`), and its identifier becomes the per-PACS DICOMweb root. ([02](02-cube-and-pacs-data-model.md) §3.2)

- **`pacs` profile** — the miniChRIS Docker Compose profile that starts the DICOM ingest pipeline (`orthanc` + `pfdcm` + `oxidicom` + `nats`); without it there is no PACS receiver. ([03](03-minichris-docker.md) §2)

- **`PACSFile`** — a Django proxy over `ChrisFile` scoped to `fname__startswith='SERVICES/PACS/'`; there is no FK from it to `PACSSeries` (the link is folder ancestry). ([02](02-cube-and-pacs-data-model.md) §3.4)

- **`PACSInstance`** — the Phase A model (one row per `.dcm`, FK to `PACSSeries`, 1-to-1 with `PACSFile`) that adds the missing instance level needed by QIDO/WADO. ([02](02-cube-and-pacs-data-model.md) §6.2)

- **`PACSQuery` / `PACSRetrieve`** — CUBE's *outbound* C-FIND/C-MOVE pull mechanism via pfdcm (CUBE-as-client); they stay in place under the DICOMweb work, which replaces the *consumer* side. ([02](02-cube-and-pacs-data-model.md) §3.5)

- **`PACSSeries`** — CUBE's central, finest-grain metadata row before this spike (one per DICOM series, unique on `(pacs, SeriesInstanceUID)`), with Patient and Study tags denormalized onto it. ([02](02-cube-and-pacs-data-model.md) §3.3)

- **`PACSStudy`** — the L2 explicit Study-level model (one row per `(pacs, StudyInstanceUID)`, carrying Patient/Study tags + denormalized counts), replacing query-time GROUP-BY rollups (decision D2). ([12](12-l2-dicomweb-implementation.md) §1.2; [08](08-l2-architecture-decisions.md) D2)

- **`pacs_users`** — the well-known Django group whose membership grants read access to PACS/DICOMweb data; `chris` (the superuser) gets write. ([09](09-cube-rest-api.md) §4.4; [02](02-cube-and-pacs-data-model.md) §3.6)

- **Patient → Study → Series → Instance** — DICOM's four-level information hierarchy: a person, an exam, one acquisition (one modality), one image/object; keyed respectively by PatientID, StudyInstanceUID, SeriesInstanceUID, SOPInstanceUID. ([04](04-dicom-standard.md) §1)

- **pfbridge / pflink** — optional miniChRIS workflow-orchestration services over pfdcm/CUBE (`pflink` profile); not on the DICOMweb critical path. ([03](03-minichris-docker.md) §3.4)

- **pfcon** — the "data and compute CONtroller," a Python/Flask web API in front of a compute resource that brokers a plugin job's input/output files and lifecycle (port 5005). ([01](01-chris-architecture.md) §4)

- **pfdcm** — the Python bridge (port 4005) that drives C-FIND (query) and C-MOVE (retrieve) against an *upstream* hospital PACS, i.e. the DICOM *pull* path into CUBE. ([01](01-chris-architecture.md) §5.2)

- **pg_trgm** — the PostgreSQL trigram-index extension enabling substring-wildcard (`*DOE*`) and fuzzy `PN` matching for QIDO via GIN indexes; enabling it is a one-line `TrigramExtension()` migration, but the `TrigramSimilar` lookup must also be registered. ([08](08-l2-architecture-decisions.md) D4; [12](12-l2-dicomweb-implementation.md) §4.5)

- **PHI** — Protected Health Information; can hide inside DICOM sequences, so anonymization must recurse — but anonymization is out of scope here. ([06](06-pydicom.md) §3, §8.1)

- **pman** — the "process manager" shim behind pfcon that translates a CUBE job request into a concrete call to the compute backend (Docker / Podman / Swarm / Kubernetes / SLURM); port 5010. ([01](01-chris-architecture.md) §4)

- **Pipeline** — a saved template of plugin-to-plugin edges with default parameters; one execution of a pipeline is a *workflow*. ([01](01-chris-architecture.md) §4.2)

- **Plugin** — a containerized command-line program plus a `description.json` I/O contract; types are `fs` (feed-source), `ds` (data-source), and `ts` (topology-source). ([01](01-chris-architecture.md) §4.1)

- **PluginInstance** — one execution of one `Plugin` inside a `Feed`, forming a DAG via a self-FK and tracking its own state machine. ([10](10-cube-internals.md) §4.3, §4.5)

- **PN encoding** — the DICOM JSON quirk that a Person-Name value is an array of *objects* (`[{"Alphabetic": "DOE^JANE"}]`), with up to three `=`-separated groups (Alphabetic/Ideographic/Phonetic), not a bare string — the most common hand-rolled-renderer bug. ([05](05-dicomweb-qido-wado-stow.md) §1.2; [12](12-l2-dicomweb-implementation.md) §2.3)

- **preamble** — the 128 zero bytes at the start of a PS3.10 `.dcm` file before the `DICM` magic. ([04](04-dicom-standard.md) §6)

- **PS3.18** — the part of the DICOM standard (Web Services) that defines QIDO-RS (§10.6), WADO-RS (§10.4), STOW-RS (§10.5), and the DICOM JSON Model (Annex F). ([05](05-dicomweb-qido-wado-stow.md) §0)

- **pydicom** — the Python library CUBE uses to read `.dcm` headers (`dcmread(..., stop_before_pixels=True, force=True)`) for indexing and STOW validation; pinned `>=3.0,<4.0`. ([06](06-pydicom.md) §0–§1)

- **QIDO-RS** — "Query based on ID for DICOM Objects" (PS3.18 §10.6): an HTTP `GET` search over the metadata catalog (DICOM verb analog C-FIND) returning the DICOM JSON Model. ([05](05-dicomweb-qido-wado-stow.md) §2)

- **RabbitMQ** — CUBE's AMQP broker (port 5672) carrying Celery tasks and the oxidicom series-registration job. ([10](10-cube-internals.md) §6; [01](01-chris-architecture.md) §4)

- **ReferencedSOPSequence (0008,1199)** — the STOW response sequence listing successfully stored instances, each with its `RetrieveURL`. ([05](05-dicomweb-qido-wado-stow.md) §4.4; [12](12-l2-dicomweb-implementation.md) §6.4)

- **`register_pacs_series`** — the Celery task (queue `main2`) oxidicom enqueues at association end; it runs `PACSSeriesSerializer.create` to write the `PACSSeries` + `PACSFile` rows (series-grain tags only, no instance-level data). ([11](11-oxidicom-and-ingestion.md) §8.1; [10](10-cube-internals.md) §6.1)

- **`reindex_pacs_instances`** — the L2 management command that backfills pre-existing PACS data by dispatching `index_pacs_instance` per `.dcm` (idempotent). ([12](12-l2-dicomweb-implementation.md) §7.6)

- **RetrieveURL (0008,1190)** — the WADO-RS URL of an object that QIDO returns (the QIDO→WADO contractual glue) and STOW echoes for each stored instance. ([05](05-dicomweb-qido-wado-stow.md) §2.5, §5)

- **S3 / S3Manager** — CUBE's S3/MinIO/Ceph object-storage backend (one of swift/s3/fslink). ([10](10-cube-internals.md) §3.4)

- **SCP / SCU** — DICOM Service Class Provider (the server/receiver, e.g. oxidicom) and Service Class User (the client/sender, e.g. Orthanc pushing). ([04](04-dicom-standard.md) §0; [11](11-oxidicom-and-ingestion.md) §2)

- **`SERVICES/PACS/`** — the storage path prefix under which all PACS `.dcm` files live (`SERVICES/PACS/<callingAET>/<study>/<series>/<sop>.dcm`); a folder owned by `chris`. ([10](10-cube-internals.md) §2.2; [11](11-oxidicom-and-ingestion.md) §4)

- **serie** — a ChRIS automation service that watches for newly-received series and auto-launches a pipeline; context, not on the DICOMweb critical path. ([01](01-chris-architecture.md) §2)

- **SOP Class / SOP Instance** — a SOP (Service-Object Pair) **Class** is the *type* of object (e.g. "CT Image Storage", `SOPClassUID`); a SOP **Instance** is one concrete object (`SOPInstanceUID`); "Instance" and "SOP Instance" are the same thing. ([04](04-dicom-standard.md) §2)

- **SQ (Sequence)** — the DICOM VR for a nested list of datasets; in JSON its `Value` is an array of nested dataset objects (used in STOW's Referenced/FailedSOPSequence). ([04](04-dicom-standard.md) §4; [06](06-pydicom.md) §3)

- **SSE (Server-Sent Events)** — the `GET /api/v1/pacs/sse/` `text/event-stream` endpoint that relays oxidicom's LONK NATS progress to browsers for live "received N/M" bars. ([09](09-cube-rest-api.md) §7.3; [11](11-oxidicom-and-ingestion.md) §5.4)

- **`StorageManager`** — CUBE's abstract storage interface (`ls`/`upload_obj`/`download_obj`/…), implemented by `SwiftManager` / `S3Manager` / `FilesystemManager` and selected by `connect_storage`. ([10](10-cube-internals.md) §3.1)

- **`stop_before_pixels`** — the pydicom `dcmread` flag that stops at `PixelData` so only the header is read — fast, especially on cold S3 reads. ([06](06-pydicom.md) §1.1)

- **STOW-RS** — "Store Over the Web" (PS3.18 §10.5): an HTTP `POST` that pushes DICOM instances into the server via `multipart/related; type="application/dicom"` (analog C-STORE); decided *in scope* (D3). ([05](05-dicomweb-qido-wado-stow.md) §4; [08](08-l2-architecture-decisions.md) D3)

- **swift / SwiftManager** — CUBE's historical default OpenStack-Swift object-storage backend (one of swift/s3/fslink). ([10](10-cube-internals.md) §3.4)

- **TA2 §2.6.1.6** — the grant budget line ("Imaging-Native APIs (e.g. DICOMweb) & Regression Testing," $109k, BCH + Red Hat) that funds this work: WADO+STOW+QIDO operational by Month 12, regression suite by Month 15. ([15](15-engagement-and-scoping.md) §1)

- **TD (Trusted / Technical Domain)** — an OpenShift deployment site in the ATLAS federation; the planned "ATLAS DICOMweb gateway" (§2.7.1.2) sits above one auth-aware DICOMweb endpoint per TD. ([08](08-l2-architecture-decisions.md) D1; [15](15-engagement-and-scoping.md) §1)

- **Transfer Syntax / TransferSyntaxUID** — the encoding rules of a DICOM dataset (explicit/implicit VR, byte order, pixel compression), named by a UID in `(0002,0010)`; Explicit VR Little Endian (`1.2.840.10008.1.2.1`) is the WADO-RS default and Implicit VR Little Endian (`1.2.840.10008.1.2`) the mandatory baseline. ([04](04-dicom-standard.md) §7)

- **trigram / `TrigramSimilar` / `TrigramExtension`** — the Postgres trigram tools (`pg_trgm`) behind QIDO fuzzy/substring matching: the migration creates the extension + GIN index, and the `TrigramSimilar` lookup must be registered for `__trigram_similar` to work. ([12](12-l2-dicomweb-implementation.md) §4.5; [08](08-l2-architecture-decisions.md) D4)

- **`ts` plugin** — a "topology-source" plugin type that joins/reshapes multiple parent branches. ([01](01-chris-architecture.md) §4.1)

- **UID** — a globally unique, dotted-decimal identifier (VR `UI`, ≤64 chars, digits and `.` only) that joins studies, series, instances, SOP classes, and transfer syntaxes. ([04](04-dicom-standard.md) §5)

- **`Unknown association ULID`** — the panic string from oxidicom's state-loop when a `Finish` arrives without a matching `Start` (e.g. a bare TCP probe); it silently kills ingestion while the container stays "Up." ([11](11-oxidicom-and-ingestion.md) §7; [13](13-deployment.md) §8)

- **`update_or_create`** — the idempotent ORM upsert the indexers use, keyed on `(series, SOPInstanceUID)`, so re-indexing a file is safe. ([02](02-cube-and-pacs-data-model.md) §6.2)

- **variant A / B / C** — the three D1 options for where DICOMweb lives: A = Django-only (re-reads files; weakest), B = oxidicom/Rust-hosted (fastest, must reimplement auth), C = hybrid (endpoints in Django, oxidicom publishes parsed tags on a *new* NATS event, Celery indexer as fallback) — **C recommended, B fallback**. ([08](08-l2-architecture-decisions.md) D1)

- **VM (Value Multiplicity)** — the count of values a data element may hold (e.g. `1`, `1-n`), with multiple string values delimited by backslash. ([04](04-dicom-standard.md) §3.3)

- **VR (Value Representation)** — the two-letter data-type code of a DICOM element (e.g. `PN`, `DA`, `UI`, `CS`, `US`); it governs both encoding and QIDO matching rules, and pydicom returns rich (non-primitive) VR types that must be coerced. ([04](04-dicom-standard.md) §4; [06](06-pydicom.md) §8)

- **WADO-RS** — "Web Access to DICOM Objects by RESTful services" (PS3.18 §10.4): an HTTP `GET` that retrieves the actual objects — full instances (`multipart/related; type="application/dicom"`), `/metadata`, `/frames`, `/bulkdata`, `/rendered`, `/thumbnail` (analog C-MOVE/C-GET). ([05](05-dicomweb-qido-wado-stow.md) §3)

- **WADO-URI** — the legacy single-frame-by-query-params retrieval (`/wado?requestType=WADO...`); out of scope, mentioned only so it isn't confused with WADO-**RS**. ([07](07-orthanc.md) §5)

- **`wait_for` (Ansible)** — the TCP-readiness module whose bare-connect probe of oxidicom's `:11111` triggered the silent-ingest-death panic (deploy bug #3); fixed by gating on container `running` state instead. ([13](13-deployment.md) §8; [11](11-oxidicom-and-ingestion.md) §7.3)

- **Workflow** — one execution of a Pipeline, tying the plugin instances it spawned back to the pipeline template. ([10](10-cube-internals.md) §4.6)

- **WORM** — "write-once, read-many": CUBE treats stored files as immutable, which is why WADO-RS streams stored bytes back unchanged and STOW never re-encodes. ([10](10-cube-internals.md) §3.1; [06](06-pydicom.md) §7)
