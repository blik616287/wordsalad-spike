# 00 — Glossary

> One-sentence definitions for every term an outsider trips over in this spike. Definitions are
> pulled from the KB files so they stay consistent; the parenthetical points to where the term is
> developed. Use with [`00-INDEX.md`](00-INDEX.md) and the meeting brief
> ([`../MEETING_BRIEF.md`](../MEETING_BRIEF.md)).

---

## ChRIS platform & components

- **ChRIS** — the *ChRIS Research Integration Service*, an open-source, container-native platform (created at FNNDSC / Boston Children's Hospital) for running trees of containerized analysis plugins against medical imaging at scale. ([01](01-chris-architecture.md) §1)

- **CUBE** (**ChRIS_ultron_backEnd**) — the Django + Django REST Framework backend that is ChRIS's "brain": the REST API, the database of users/files/jobs, the feed/plugin/pipeline model, and the auth chain. ([02](02-cube-and-pacs-data-model.md) §1)

- **ChRIS_ui** — the React single-page frontend (host port 8020) that drives CUBE over its REST API and bundles the Cornerstone3D + Niivue in-browser DICOM viewers (it does **not** embed OHIF). ([01](01-chris-architecture.md) §6)

- **pfcon** — the "data and compute CONtroller," a Python/Flask web API in front of a compute resource that brokers a plugin job's input/output files and its lifecycle (port 5005). ([01](01-chris-architecture.md) §4)

- **pman** — the "process manager" shim behind pfcon that translates a CUBE job request into a concrete call to the compute backend (Docker / Podman / Swarm / Kubernetes / SLURM); port 5010. ([01](01-chris-architecture.md) §4)

- **oxidicom** — the Rust **C-STORE SCP** that is CUBE's DICOM "front door": it listens on TCP 11111 (AET `ChRIS`), writes received `.dcm` files into CUBE's storage tree under `SERVICES/PACS/`, publishes reception progress on NATS, and enqueues a `register_pacs_series` Celery task. ([01](01-chris-architecture.md) §5.1; [02](02-cube-and-pacs-data-model.md) §4)

- **pfdcm** — the Python bridge (port 4005) that drives **C-FIND** (query) and **C-MOVE** (retrieve) against an *upstream* hospital PACS, i.e. the DICOM *pull* path into CUBE. ([01](01-chris-architecture.md) §5.2)

- **NATS** — a lightweight message broker (port 4222) used **only** for oxidicom→CUBE DICOM-reception *progress* events; it carries no DICOM tags today. ([01](01-chris-architecture.md) §5.1; [08](08-l2-architecture-decisions.md) Background)

- **LONK** — "Light Oxidicom NotifiKations," oxidicom's NATS progress protocol carrying exactly three message kinds — `progress` (`0x01` + file count), `done` (`0x00`), `error` (`0x02`) — on subject `oxidicom.<pacs>.<SeriesInstanceUID>`, with **no metadata/tags**. ([01](01-chris-architecture.md) §5.1)

- **RabbitMQ + Celery** — CUBE's AMQP broker (port 5672) and async task framework; named queues are `main1` (latency-sensitive plugin state machine), `main2` (side-effects + DICOM indexing), and `periodic` (cron). ([01](01-chris-architecture.md) §4.4)

- **chrisomatic** — a one-shot declarative registrar that installs the compute resource and seed plugins into a fresh miniChRIS stack. ([03](03-minichris-docker.md) §3.2)

## ChRIS work model

- **Plugin** — a containerized command-line program plus a `description.json` I/O contract; types are `fs` (feed-source, takes no input), `ds` (data-source, transforms an input dir), and `ts` (topology-source, joins branches). ([01](01-chris-architecture.md) §4.1)

- **Feed** — the root entity of an analysis in ChRIS, under which a tree of plugin instances executes. ([01](01-chris-architecture.md) §4.2)

- **Pipeline** — a saved template of plugin-to-plugin edges with default parameters; one execution of a pipeline is a *workflow*. ([01](01-chris-architecture.md) §4.2)

## PACS & DICOM core

- **PACS** — Picture Archiving and Communication System, a hospital's medical-image store/network node; in CUBE a `PACS` row is one registered upstream DICOM source (e.g. `BCH`, `MINICHRISORTHANC`). ([02](02-cube-and-pacs-data-model.md) §3.2)

- **DICOM** — *Digital Imaging and Communications in Medicine*, the medical-imaging standard that is *both* a file format (`.dcm`) and a network protocol family, maintained by NEMA as the multi-part PS3.x standard. ([04](04-dicom-standard.md) §0)

- **DIMSE** — the classic DICOM message service over TCP/IP (C-STORE, C-FIND, C-MOVE, C-ECHO), as opposed to the HTTP-based DICOMweb. ([04](04-dicom-standard.md) §13)

- **AE Title (AET)** — Application Entity Title, the (≤16-char) name of a DICOM network node; oxidicom answers C-STORE as AET `ChRIS`. ([04](04-dicom-standard.md) §4; [03](03-minichris-docker.md) §3.3)

- **Patient → Study → Series → Instance** — DICOM's four-level information hierarchy: a person, an exam, one acquisition (one modality), one image/object; keyed respectively by PatientID, StudyInstanceUID, SeriesInstanceUID, SOPInstanceUID. ([04](04-dicom-standard.md) §1)

- **SOP Class / SOP Instance** — a SOP (Service-Object Pair) **Class** is the *type* of object (e.g. "CT Image Storage", identified by `SOPClassUID`); a SOP **Instance** is one concrete object of that class (`SOPInstanceUID`); "Instance" and "SOP Instance" are the same thing. ([04](04-dicom-standard.md) §2)

- **UID** — a globally unique, dotted-decimal identifier (VR `UI`, max 64 chars, digits and `.` only) that serves as the join key for studies, series, instances, SOP classes, and transfer syntaxes. ([04](04-dicom-standard.md) §5)

- **VR** (Value Representation) — the two-letter data-type code of a DICOM data element (e.g. `PN` person name, `DA` date, `UI` UID, `CS` code string, `US` unsigned short); it governs both encoding and QIDO matching rules. ([04](04-dicom-standard.md) §4)

- **VM** (Value Multiplicity) — the count of values a data element may hold (e.g. `1`, `1-n`), with multiple string values delimited by backslash. ([04](04-dicom-standard.md) §3.3)

- **Transfer Syntax** — the encoding rules of a DICOM dataset (explicit vs implicit VR, byte order, and pixel-data compression), named by a UID in `(0002,0010)`; Explicit VR Little Endian (`1.2.840.10008.1.2.1`) is the WADO-RS default and Implicit VR Little Endian (`1.2.840.10008.1.2`) is the mandatory baseline. ([04](04-dicom-standard.md) §7)

- **Modality** — the `(0008,0060)` code string naming the acquisition type, fixed per series (e.g. `CT`, `MR`, `US`, `PT`, `SR`). ([04](04-dicom-standard.md) §9)

## DICOMweb (the deliverable)

- **DICOMweb** — the HTTP/REST face of DICOM defined in PS3.18, comprising QIDO-RS (query), WADO-RS (retrieve), and STOW-RS (store); adding these three to CUBE is the entire ATLAS deliverable. ([05](05-dicomweb-qido-wado-stow.md) §0)

- **QIDO-RS** — "Query based on ID for DICOM Objects" (PS3.18 §10.6): an HTTP `GET` search over the metadata catalog (the DICOM verb analog is C-FIND) that returns the DICOM JSON Model. ([05](05-dicomweb-qido-wado-stow.md) §2)

- **WADO-RS** — "Web Access to DICOM Objects by RESTful services" (PS3.18 §10.4): an HTTP `GET` that retrieves the actual objects — full instances (multipart/related `application/dicom`), `/metadata`, `/frames`, `/rendered`, `/thumbnail` (analog C-MOVE/C-GET). ([05](05-dicomweb-qido-wado-stow.md) §3)

- **STOW-RS** — "Store Over the Web" (PS3.18 §10.5): an HTTP `POST` that pushes DICOM instances into the server via `multipart/related; type="application/dicom"` (analog C-STORE). ([05](05-dicomweb-qido-wado-stow.md) §4)

- **DICOM JSON Model** — the PS3.18 Annex F response format (`application/dicom+json`): a JSON object keyed by 8-hex-digit tags, each value carrying a `"vr"` plus at most one of `Value` / `BulkDataURI` / `InlineBinary` (note PN serializes as `[{"Alphabetic": "DOE^JANE"}]`, not a bare string). ([05](05-dicomweb-qido-wado-stow.md) §1; [04](04-dicom-standard.md) §11)

- **OHIF** — the open-source Open Health Imaging Foundation web DICOM viewer; in this stack OHIF is **only** served by the bundled Orthanc plugin (`/ohif/` on :8042), **not** by ChRIS_ui — but a standard WADO/QIDO surface on CUBE is what any OHIF/3D-Slicer/Weasis client would consume. ([01](01-chris-architecture.md) §6; [03](03-minichris-docker.md) §3.2)

## CUBE/Django specifics

- **collection+json** — the hypermedia format (`application/vnd.collection+json`) that is CUBE's default DRF renderer, wrapping data in `links`/`items`/`queries`/`template` envelopes — and exactly what DICOMweb deliberately breaks from (it speaks `application/dicom+json` on a separate URL mount). ([02](02-cube-and-pacs-data-model.md) §2)

- **DRF** — Django REST Framework, the toolkit CUBE's API is built on; its `DEFAULT_AUTHENTICATION_CLASSES` give DICOMweb its Token/Basic/Session auth for free. ([02](02-cube-and-pacs-data-model.md) §2)

- **pg_trgm** — the PostgreSQL trigram-index extension that enables substring-wildcard (`*DOE*`) and fuzzy `PN` matching for QIDO via GIN indexes; enabling it is a one-line `TrigramExtension()` migration. ([08](08-l2-architecture-decisions.md) D4)

- **`PACSSeries`** — CUBE's central, finest-grain metadata row today (one per DICOM series, unique on `(pacs, SeriesInstanceUID)`), with Patient and Study tags denormalized onto it. ([02](02-cube-and-pacs-data-model.md) §3.3)

- **`PACSInstance`** — the Phase A model (one row per `.dcm`, FK to `PACSSeries`, 1-to-1 with `PACSFile`) that adds the missing instance level needed by QIDO/WADO. ([02](02-cube-and-pacs-data-model.md) §6.2)

- **`PACSStudy`** — the recommended new explicit Study-level model (one row per `(pacs, StudyInstanceUID)`, with Patient/Study tags and denormalized counts), replacing query-time GROUP-BY rollups. ([08](08-l2-architecture-decisions.md) D2)

## Tooling & reference servers

- **pydicom** — the Python library CUBE uses to read `.dcm` headers (`dcmread(..., stop_before_pixels=True, force=True)`) for indexing and STOW validation; pinned `>=3.0,<4.0`. ([06](06-pydicom.md) §0–§1)

- **Orthanc** — a lightweight open-source DICOM server used in this spike as both the test data source (C-STORE push into oxidicom) and the DICOMweb conformance oracle (its `/dicom-web/` QIDO/WADO/STOW is the behavioral reference to diff CUBE against). ([07](07-orthanc.md) §1, §4)

- **dcm4chee** — a reference enterprise DICOM archive whose DICOMweb behavior (and STOW failure-reason codes) is used as a cross-check alongside Orthanc and Azure. ([05](05-dicomweb-qido-wado-stow.md) §4)

## Deployment

- **miniChRIS-docker** — the single-compose, demo-grade distribution of the whole ChRIS backend that the spike's deployment **wraps** (never forks); the DICOM ingest pipeline is gated behind its `pacs` compose profile. ([03](03-minichris-docker.md) §1–§2)

- **Flux GitRepository / HelmRelease** — GitOps custom resources (a `GitRepository` defines a source repo, a `HelmRelease` declares a Helm chart to reconcile from it) used by ChRIS's *production* Helm path (`fnndsc/charts`); out of scope for this spike, which uses miniChRIS + Ansible. (production path noted in [03](03-minichris-docker.md) §1)

## Program / grant

- **ARPA-H / ATLAS** — ARPA-H is the U.S. Advanced Research Projects Agency for Health; **ATLAS** is the grant program funding this DICOMweb work (prime: Gradient Health; partner: Boston Children's Hospital), whose §2.6.1.6 sub-task makes QIDO-RS + WADO-RS + STOW-RS a joint Month-12 deliverable. ([08](08-l2-architecture-decisions.md) D3, D5)

- **TD** — a Technical Domain / deployment site in the ATLAS federation; the planned "ATLAS DICOMweb gateway" (grant §2.7.1.2) sits above one auth-aware DICOMweb endpoint per TD. ([08](08-l2-architecture-decisions.md) D1)

- **L1 / L2 / L3 / L4** — the engagement layering: L1 research spike (done), L2 MVP implementation (this — endpoints on one CUBE + the BCH dataset), L3 grant §2.6.1.6 (operational within TD constraints + regression suite), L4 broader pipeline (aspirational). ([08](08-l2-architecture-decisions.md) D5)
