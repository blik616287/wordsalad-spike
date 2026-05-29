# 00 — Master Index (the curve-ball lookup)

> **What this is.** A searchable master index for the whole DICOMweb-on-CUBE knowledge base.
> When someone in the BCH meeting throws a term, an acronym, or a "where does X live?" question,
> start here, then jump to the file that owns the answer. Pair it with the
> [glossary](00-glossary.md) (one-sentence definitions) and the
> [`../MEETING_BRIEF.md`](../MEETING_BRIEF.md) (the spoken cheat-sheet).

## How to use this KB

1. **Know the term but want depth?** Find it in the **keyword → file map** (§3) or the
   **topic → file map** (§2), open that file, jump to the cited section.
2. **Grep it.** The KB is plain Markdown; the fastest lookup is:
   ```sh
   rg <term> knowledge-base/
   rg -i 'stow.*409' knowledge-base/        # case-insensitive, regexy
   rg -l PACSStudy knowledge-base/          # just list files that mention it
   ```
3. **Want the code, not the prose?** Follow the [code pointers](#5-code-deploy-and-brief-pointers)
   into `implementation/dicomweb-l2/` (our L2 app), `implementation/ChRIS_ultron_backEnd/`
   (the CUBE submodule), and `deploy/ansible/` (the wrap).
4. **The files are layered.** Background/standard (01–08) is "what the world's DICOM/ChRIS
   facts are." This-repo engineering (09–14) is "what *we* built and verified, with `file:line`
   citations." Engagement (15) is the who/why/money. Read 01 → 05 → 08 → 12 for the fastest
   technical on-ramp; read 15 + `MEETING_BRIEF.md` for the room.

---

## 1. The files (grouped)

### Background / the standard (01–08)

| File | Title | One-line description | Questions it answers |
|---|---|---|---|
| [01](01-chris-architecture.md) | ChRIS Platform Architecture | The end-to-end component map: CUBE, ChRIS_ui, pfcon/pman, oxidicom, pfdcm, NATS, storage. | What is ChRIS? What are all the services and ports? How does a plugin run? How does DICOM enter (push vs pull)? Where do DICOMweb endpoints fit? Is OHIF in ChRIS_ui? |
| [02](02-cube-and-pacs-data-model.md) | CUBE Internals & PACS / DICOM Data Model | Bridges DICOM Patient/Study/Series/Instance to CUBE's Django models (`PACS`/`PACSSeries`/`PACSFile`) + the Phase A `PACSInstance`. | Where does StudyInstanceUID live? Why no instance row today? What did Phase A add? Why variant C not A? The DICOM→model field map. |
| [03](03-minichris-docker.md) | miniChRIS-docker: the Deployable Stack | Verbatim transcription of the compose stack the deploy wraps — services, images, ports, env, profiles, creds, scripts. | What image/port is each service? What's the `pacs` profile? Default creds? How do I push DICOM in? How is oxidicom↔Orthanc wired? |
| [04](04-dicom-standard.md) | DICOM Standard Fundamentals | DICOM from zero: information model, data elements, tags, VRs, UIDs, file format (PS3.10), transfer syntaxes, modalities. | What's a VR/VM/tag/UID? What's a transfer syntax? What does a `.dcm` file look like? Which tags matter for QIDO? The PN encoding gotcha. |
| [05](05-dicomweb-qido-wado-stow.md) | DICOMweb: QIDO-RS / WADO-RS / STOW-RS | The byte-level reference for the three PS3.18 web services: URL templates, params, matching, media types, JSON model, status codes. | QIDO query syntax? WADO multipart/related & transfer-syntax negotiation? STOW status codes (200/202/400/409/415)? The Store Instances Response shape? |
| [06](06-pydicom.md) | pydicom: Reading & Writing DICOM in Python | How CUBE touches DICOM bytes in Python: indexing headers, STOW parsing, test fixtures; the DA/TM bug; VR type gotchas. | How does the indexer read headers? `stop_before_pixels`/`force`? How to build a STOW handler? Synthetic `.dcm` fixtures? Why coerce VR types? |
| [07](07-orthanc.md) | Orthanc: a Test PACS / DICOMweb Reference Server | Operational cheat-sheet for Orthanc as the sample-data source and the DICOMweb conformance oracle. | How do I load sample DICOM? `orthancteam` vs `jodogne`? `ORTHANC__` env config? How to C-STORE-push to oxidicom? `dicomweb-client` smoke harness? |
| [08](08-l2-architecture-decisions.md) | L2 Architecture Decisions & Recommendations | The decision record: D1 (where endpoints live A/B/C), D2 (`PACSStudy` vs GROUP BY), D3 (STOW in scope), D4 (`pg_trgm`), D5 (L1–L4). | Variant A/B/C tradeoffs? Why hybrid C with fallback B? GROUP-BY vs `PACSStudy`? Is STOW in scope? The one factual question for BCH? |

### This repo's engineering (09–14) — with `file:line` citations

| File | Title | One-line description | Questions it answers |
|---|---|---|---|
| [09](09-cube-rest-api.md) | CUBE's REST API (the surface clients use today) | The *existing* `/api/v1/...` wire protocol: collection+json, auth, pagination, the 18-endpoint PACS surface, drf-spectacular quirks. | How does collection+json work? Auth/download tokens? Why does `GET /pacs/` 500 when pfdcm is down? The PACS endpoint matrix? Why no PATCH? |
| [10](10-cube-internals.md) | CUBE Django Internals — engine room | The filesystem model (`ChrisFolder`/`ChrisFile`/`ChrisLinkFile`), `StorageManager`, the compute/plugin state machine, Celery queues, settings, auth — with `file:line`. | Where do files physically live? swift/s3/fslink? `main1` vs `main2`? Why ASGI/uvicorn? `/opt/app-root/src`? How are permissions denormalized? |
| [11](11-oxidicom-and-ingestion.md) | oxidicom & the DICOM Ingestion Path | Deep dive on the Rust C-STORE SCP and the ingest dataflow; the PACS-name=calling-AET rule; the `wait_for` panic bug. | What does oxidicom emit on NATS (LONK)? PACS name == calling AET? The robustness panic on a TCP probe? Celery task vs HTTP callback registration? |
| [12](12-l2-dicomweb-implementation.md) | L2 DICOMweb Implementation (the `dicomweb` app) | The actual code of our QIDO/WADO/STOW app: models, query parser, renderers, multipart, the two indexing paths, URL/CSRF wiring. | How does our QIDO parser map tags→ORM? STOW `_store_one` flow? The two indexers (re-read vs variant C)? The CSRF-on-dispatcher fix? Native frames / the 501 boundary? |
| [13](13-deployment.md) | The Ansible Deployment | The `deploy/` tree that wraps miniChRIS and overlays L2; the six deploy bugs; overlay vs baked image. | How does the deploy work (six roles)? The cube-port override? Overlay seam (`/opt/app-root/src`)? Why a second Orthanc? The oxidicom TCP-probe crash? |
| [14](14-testing-and-validation.md) | Testing & Validation: the Evidence Ledger | "How do you know it works?" — 97/97 tests, the harness, the live e2e matrix, 11 bugs found, and what is NOT proven. | What's tested and how? Unit vs integration vs live? The variant-C "no file read" proof? The 11-bug ledger? What's NOT proven? |

### Engagement / scope (15)

| File | Title | One-line description | Questions it answers |
|---|---|---|---|
| [15](15-engagement-and-scoping.md) | Engagement & Scoping: the non-code context | Who's in the room, what BCH asked for (May 19), what's committed/conditional/open, funding state, scope-creep redirects. ⚠️ mixes 🟢 BCH-facing and 🔒 internal-only. | What did we commit to? Who are Rudolph/Joshua/Alex/Tommy/Adam? Is STOW in scope? Is the grant approved? How to deflect scope creep? |

### Why & how (16) — the thesis, narrative

| File | Title | One-line description | Questions it answers |
|---|---|---|---|
| [16](16-why-and-how-dicomweb-compliance.md) | Why & How CUBE's API Must Become DICOMweb-Compliant | The "why modernize" narrative tying DICOM + DICOMweb + ChRIS together: why DICOM is hard to integrate (C-MOVE), what DICOMweb fixes (HTTP pull), CUBE's gap (collection+json ≠ DICOMweb), why compliance is needed (clinic interop, grant, federation), and how (additive, proven). | Why does CUBE need DICOMweb at all? Why is DICOM "legacy"? What does DICOMweb actually change? Why can't OHIF/PACS talk to CUBE today? Is this a rewrite? |

> **Companion top-level docs:** [`../MEETING_BRIEF.md`](../MEETING_BRIEF.md) (the spoken cheat-sheet,
> Q&A), [`00-glossary.md`](00-glossary.md) (one-line definitions of every term below).

---

## 2. Topic → file map

| Topic | Primary file(s) | Section |
|---|---|---|
| ChRIS what/why, component map, ports | [01](01-chris-architecture.md) | §1–§3, §7 |
| Plugin/feed/pipeline/workflow compute model | [01](01-chris-architecture.md), [10](10-cube-internals.md) | 01 §4; 10 §4 |
| How DICOM enters CUBE (push vs pull) | [01](01-chris-architecture.md), [11](11-oxidicom-and-ingestion.md) | 01 §5; 11 §1–§8 |
| The DICOM data model (Patient/Study/Series/Instance) | [04](04-dicom-standard.md), [02](02-cube-and-pacs-data-model.md) | 04 §1; 02 §5 |
| CUBE PACS data model (`PACS`/`PACSSeries`/`PACSFile`) | [02](02-cube-and-pacs-data-model.md), [09](09-cube-rest-api.md) | 02 §3; 09 §7.5 |
| Instance-level row (`PACSInstance`, Phase A) | [02](02-cube-and-pacs-data-model.md), [12](12-l2-dicomweb-implementation.md) | 02 §6; 12 §1.3 |
| Study-level model (`PACSStudy`, L2/D2) | [12](12-l2-dicomweb-implementation.md), [08](08-l2-architecture-decisions.md) | 12 §1.2; 08 D2 |
| The unified filesystem (`ChrisFolder`/`ChrisFile`/links) | [10](10-cube-internals.md) | §2 |
| Object storage abstraction (swift/s3/fslink) | [10](10-cube-internals.md) | §3 |
| Celery queues (`main1`/`main2`/`periodic`) | [10](10-cube-internals.md), [01](01-chris-architecture.md) | 10 §6; 01 §4.4 |
| oxidicom (C-STORE SCP, internals, bug) | [11](11-oxidicom-and-ingestion.md) | all |
| LONK / NATS progress protocol | [11](11-oxidicom-and-ingestion.md), [01](01-chris-architecture.md) | 11 §5; 01 §5.1 |
| Series registration (`register_pacs_series` / `POST pacs/series/`) | [11](11-oxidicom-and-ingestion.md), [09](09-cube-rest-api.md) | 11 §8; 09 §7.4 |
| CUBE REST API style (collection+json, auth, pagination) | [09](09-cube-rest-api.md), [10](10-cube-internals.md) | 09 §2–§6; 10 §8 |
| Auth chain (Token/Basic/Session/LDAP) + `pacs_users` | [09](09-cube-rest-api.md), [10](10-cube-internals.md), [02](02-cube-and-pacs-data-model.md) | 09 §4; 10 §8; 02 §3.6 |
| DICOM standard internals (VR/VM/tags/UIDs/transfer syntax) | [04](04-dicom-standard.md) | §3–§7 |
| DICOM file format (preamble/DICM/file-meta) | [04](04-dicom-standard.md), [06](06-pydicom.md) | 04 §6; 06 §1.2 |
| QIDO-RS (query) | [05](05-dicomweb-qido-wado-stow.md), [12](12-l2-dicomweb-implementation.md) | 05 §2; 12 §4 |
| WADO-RS (retrieve, metadata, frames, bulkdata) | [05](05-dicomweb-qido-wado-stow.md), [12](12-l2-dicomweb-implementation.md) | 05 §3; 12 §5 |
| STOW-RS (store) | [05](05-dicomweb-qido-wado-stow.md), [12](12-l2-dicomweb-implementation.md) | 05 §4; 12 §6 |
| DICOM JSON Model (Annex F) / `application/dicom+json` | [05](05-dicomweb-qido-wado-stow.md), [12](12-l2-dicomweb-implementation.md), [04](04-dicom-standard.md) | 05 §1; 12 §2; 04 §11 |
| QIDO matching (wildcard/range/UID-list/fuzzy) | [05](05-dicomweb-qido-wado-stow.md), [12](12-l2-dicomweb-implementation.md), [08](08-l2-architecture-decisions.md) | 05 §2.4; 12 §4.2; 08 D4 |
| pg_trgm / fuzzy matching / trigram lookup | [08](08-l2-architecture-decisions.md), [12](12-l2-dicomweb-implementation.md), [14](14-testing-and-validation.md) | 08 D4; 12 §4.5; 14 §6 |
| pydicom (read/write/fixtures/VR types) | [06](06-pydicom.md) | all |
| The two indexing paths (re-read vs variant C) | [12](12-l2-dicomweb-implementation.md), [11](11-oxidicom-and-ingestion.md) | 12 §7; 11 §10 |
| Architecture decisions A/B/C + D1–D5 | [08](08-l2-architecture-decisions.md) | all |
| Orthanc (test PACS + DICOMweb oracle) | [07](07-orthanc.md) | all |
| Deployment / Ansible / wrapping miniChRIS | [13](13-deployment.md), [03](03-minichris-docker.md) | 13 all; 03 §9 |
| Overlay vs baked image (prod path) | [13](13-deployment.md) | §10 |
| Testing evidence / bug ledger / what's not proven | [14](14-testing-and-validation.md) | §2, §6, §7 |
| Engagement, scope, funding, the room | [15](15-engagement-and-scoping.md), [`MEETING_BRIEF.md`](../MEETING_BRIEF.md) | 15 all |
| Grant / ATLAS / TD / §2.6.1.6 / L1–L4 | [15](15-engagement-and-scoping.md), [08](08-l2-architecture-decisions.md) | 15 §1, §3; 08 D5 |

---

## 3. Keyword / term → file map (the curve-ball lookup)

Dense by design — these are the words and tokens a stakeholder might lob across the table.
`rg <term> knowledge-base/` is always the backstop.

### Acronyms & protocol terms
- **AET / AE Title** → [04](04-dicom-standard.md) §4, [11](11-oxidicom-and-ingestion.md) §3/§9 (PACS-name = calling-AET), [03](03-minichris-docker.md) §3.3
- **AMQP** → [01](01-chris-architecture.md) §5.1, [10](10-cube-internals.md) §6, [03](03-minichris-docker.md) §3.3 (`OXIDICOM_AMQP_ADDRESS`)
- **ARPA-H / ATLAS** → [15](15-engagement-and-scoping.md) §1, [08](08-l2-architecture-decisions.md) D5
- **ASGI / uvicorn / Channels** → [10](10-cube-internals.md) §0, §9
- **BulkDataURI** → [05](05-dicomweb-qido-wado-stow.md) §1.1/§3, [12](12-l2-dicomweb-implementation.md) §2.4/§5.2
- **C-ECHO / C-FIND / C-MOVE / C-STORE** → [04](04-dicom-standard.md) §0/§13, [01](01-chris-architecture.md) §5, [11](11-oxidicom-and-ingestion.md) §2/§7.4 (C-ECHO safe), [07](07-orthanc.md) §6
- **collection+json** → [09](09-cube-rest-api.md) §3, [02](02-cube-and-pacs-data-model.md) §2, [10](10-cube-internals.md) §5.3
- **DICM (magic bytes)** → [04](04-dicom-standard.md) §6, [06](06-pydicom.md) §1.1, [14](14-testing-and-validation.md) §2.4
- **DICOM JSON Model / `application/dicom+json`** → [05](05-dicomweb-qido-wado-stow.md) §1, [12](12-l2-dicomweb-implementation.md) §2, [04](04-dicom-standard.md) §11
- **DICOMweb** → [05](05-dicomweb-qido-wado-stow.md) (all), [08](08-l2-architecture-decisions.md), [12](12-l2-dicomweb-implementation.md)
- **DIMSE** → [04](04-dicom-standard.md) §0/§13, [11](11-oxidicom-and-ingestion.md) §2, [13](13-deployment.md) §8 ("never TCP-probe a DIMSE port")
- **drf-spectacular** → [09](09-cube-rest-api.md) §9, [02](02-cube-and-pacs-data-model.md) §2, [12](12-l2-dicomweb-implementation.md) §12
- **DRF (Django REST Framework)** → [09](09-cube-rest-api.md) §2, [10](10-cube-internals.md) §5.3
- **fuzzymatching / TrigramSimilar / pg_trgm / gin_trgm_ops** → [08](08-l2-architecture-decisions.md) D4, [12](12-l2-dicomweb-implementation.md) §4.5, [14](14-testing-and-validation.md) §6 (bug #11)
- **Gradient Health (GH)** → [15](15-engagement-and-scoping.md) §1 (prime), §11
- **HATEOAS / hypermedia** → [09](09-cube-rest-api.md) §1, §3.5
- **Hasura / GraphQL** → [01](01-chris-architecture.md) §7, [03](03-minichris-docker.md) §3.4
- **includefield** → [05](05-dicomweb-qido-wado-stow.md) §2.3, [12](12-l2-dicomweb-implementation.md) §3 (no-op), [14](14-testing-and-validation.md) §7
- **InlineBinary** → [05](05-dicomweb-qido-wado-stow.md) §1.1, [12](12-l2-dicomweb-implementation.md) §2.1
- **IOD** → [04](04-dicom-standard.md) §2, §13
- **L1 / L2 / L3 / L4** → [08](08-l2-architecture-decisions.md) D5, [15](15-engagement-and-scoping.md) §3
- **LONK / LONK-WS** → [11](11-oxidicom-and-ingestion.md) §5, [01](01-chris-architecture.md) §5.1, [10](10-cube-internals.md) §9, [08](08-l2-architecture-decisions.md) Background
- **multipart/related** → [05](05-dicomweb-qido-wado-stow.md) §3.3/§4.2, [12](12-l2-dicomweb-implementation.md) §5.1/§6.1, [06](06-pydicom.md) §7
- **NATS / `oxidicom-meta.>`** → [11](11-oxidicom-and-ingestion.md) §5/§10, [12](12-l2-dicomweb-implementation.md) §7.2 (`consume_dicomweb_index`), [01](01-chris-architecture.md) §5.1
- **OHIF** → [01](01-chris-architecture.md) §6 (not in ChRIS_ui), [03](03-minichris-docker.md) §3.2 (Orthanc `/ohif/`), [07](07-orthanc.md)
- **Orthanc** → [07](07-orthanc.md) (all), [03](03-minichris-docker.md) §3.3/§6, [13](13-deployment.md) §3.3
- **PS3.x (PS3.3 / 3.5 / 3.6 / 3.10 / 3.18)** → [04](04-dicom-standard.md) (3/5/6/10), [05](05-dicomweb-qido-wado-stow.md) (18)
- **QIDO-RS** → [05](05-dicomweb-qido-wado-stow.md) §2, [12](12-l2-dicomweb-implementation.md) §4
- **ReferencedSOPSequence / FailedSOPSequence / FailureReason** → [05](05-dicomweb-qido-wado-stow.md) §4.4, [12](12-l2-dicomweb-implementation.md) §6.4, [06](06-pydicom.md) §7
- **RetrieveURL (0008,1190)** → [05](05-dicomweb-qido-wado-stow.md) §2.5/§5, [12](12-l2-dicomweb-implementation.md) §3 (`RetrieveURLBuilder`)
- **SOP Class / SOP Instance / SOPClassUID / SOPInstanceUID** → [04](04-dicom-standard.md) §2/§5, [02](02-cube-and-pacs-data-model.md) §6.2
- **SSE (Server-Sent Events) `/api/v1/pacs/sse/`** → [09](09-cube-rest-api.md) §7.3, [10](10-cube-internals.md) §9, [11](11-oxidicom-and-ingestion.md) §5.4
- **STOW-RS / STOW 409 / 202 / 415** → [05](05-dicomweb-qido-wado-stow.md) §4, [12](12-l2-dicomweb-implementation.md) §6, [08](08-l2-architecture-decisions.md) D3
- **STOW-RS scope (in/out)** → [08](08-l2-architecture-decisions.md) D3, [15](15-engagement-and-scoping.md) §5 (D3), §10
- **TA2 §2.6.1.6 / §2.6.1 / §2.7.1.2** → [15](15-engagement-and-scoping.md) §1, [08](08-l2-architecture-decisions.md) D3/D5
- **TD (Trusted / Technical Domain)** → [08](08-l2-architecture-decisions.md) D1, [15](15-engagement-and-scoping.md) §1
- **Transfer Syntax / TransferSyntaxUID** → [04](04-dicom-standard.md) §7, [05](05-dicomweb-qido-wado-stow.md) §3.2, [12](12-l2-dicomweb-implementation.md) §5.1
- **trigram (`__trigram_similar`)** → [12](12-l2-dicomweb-implementation.md) §4.5, [08](08-l2-architecture-decisions.md) D4, [14](14-testing-and-validation.md) §6 (bug #11)
- **UID / UI VR** → [04](04-dicom-standard.md) §5, [05](05-dicomweb-qido-wado-stow.md) §2.4 (UID-list match)
- **variant A / B / C** → [08](08-l2-architecture-decisions.md) D1, [02](02-cube-and-pacs-data-model.md) §2, [12](12-l2-dicomweb-implementation.md) §7.2
- **VR / VM** → [04](04-dicom-standard.md) §3–§4, [06](06-pydicom.md) §8
- **WADO-RS / WADO-URI** → [05](05-dicomweb-qido-wado-stow.md) §3, [07](07-orthanc.md) §5 (URI), [12](12-l2-dicomweb-implementation.md) §5

### Code symbols, models, tasks, env vars
- **`auto_silent`** (Ansible interpreter keyword, bug #2) → [13](13-deployment.md) §8, [14](14-testing-and-validation.md) §6.1
- **`ChrisFolder` / `ChrisFile` / `ChrisLinkFile`** → [10](10-cube-internals.md) §2
- **`connect_storage` / `StorageManager`** → [10](10-cube-internals.md) §3, [02](02-cube-and-pacs-data-model.md) §6.4
- **`consume_dicomweb_index` / `index_from_metadata`** → [12](12-l2-dicomweb-implementation.md) §7.2, [14](14-testing-and-validation.md) §5.2
- **`core/api.py` (single route registry)** → [09](09-cube-rest-api.md) §1/§5, [10](10-cube-internals.md) §7
- **CSRF / `@csrf_exempt` on dispatcher** → [12](12-l2-dicomweb-implementation.md) §8.3, [14](14-testing-and-validation.md) §6.2 (bug #10)
- **`cube-port.yml` / `COMPOSE_FILE` / `!override`** → [13](13-deployment.md) §4
- **`dcmread` / `stop_before_pixels` / `force` / `enforce_file_format`** → [06](06-pydicom.md) §1, §5
- **`fslink` / `swift` / `s3` (storage backends)** → [10](10-cube-internals.md) §3.4, [02](02-cube-and-pacs-data-model.md) §6.4
- **`index_pacs_instance` (re-read indexer)** → [02](02-cube-and-pacs-data-model.md) §6.4, [12](12-l2-dicomweb-implementation.md) §7.1, [11](11-oxidicom-and-ingestion.md) §10
- **`main1` / `main2` / `periodic` (Celery queues)** → [10](10-cube-internals.md) §6, [01](01-chris-architecture.md) §4.4, [11](11-oxidicom-and-ingestion.md) §9
- **`/opt/app-root/src`** → [10](10-cube-internals.md) §0, [13](13-deployment.md) §8 (bug #5), §10
- **`OXIDICOM_*` env vars (`SCP_AET`, `FILES_ROOT`, `DEV_SLEEP`, `PROMISCUOUS`, …)** → [11](11-oxidicom-and-ingestion.md) §9, [03](03-minichris-docker.md) §3.3
- **`PACSStudy` / `PACSSeries` / `PACSInstance` / `PACSFile`** → [02](02-cube-and-pacs-data-model.md) §3/§6, [12](12-l2-dicomweb-implementation.md) §1, [09](09-cube-rest-api.md) §7.5
- **`PACSQuery` / `PACSRetrieve`** → [02](02-cube-and-pacs-data-model.md) §3.5, [09](09-cube-rest-api.md) §7.1
- **`pacs_users` group** → [02](02-cube-and-pacs-data-model.md) §3.6, [09](09-cube-rest-api.md) §4.4, [10](10-cube-internals.md) §8.3
- **`register_pacs_series`** → [11](11-oxidicom-and-ingestion.md) §8.1, [10](10-cube-internals.md) §6.1, [01](01-chris-architecture.md) §5.1
- **`reindex_pacs_instances` (backfill cmd)** → [12](12-l2-dicomweb-implementation.md) §7.6, [14](14-testing-and-validation.md) §6.2 (bug #9)
- **`_parse_dicom_time` / DA-TM length-dispatch bug** → [06](06-pydicom.md) §6.1, [02](02-cube-and-pacs-data-model.md) §6.4, [14](14-testing-and-validation.md) §6
- **`Unknown association ULID` (oxidicom panic)** → [11](11-oxidicom-and-ingestion.md) §7, [13](13-deployment.md) §8 (bug #3), [14](14-testing-and-validation.md) §6.1
- **`PUSHED^META` (variant-C proof)** → [14](14-testing-and-validation.md) §5.2, [12](12-l2-dicomweb-implementation.md) §7.2
- **`SERVICES/PACS/` (storage path prefix)** → [02](02-cube-and-pacs-data-model.md) §4.3, [10](10-cube-internals.md) §2, [11](11-oxidicom-and-ingestion.md) §4

### People & money
- **Rudolph Pienaar / Joshua Kanner (BCH)** → [15](15-engagement-and-scoping.md) §1
- **Alex Scammon / Tommy (Aldo) Sonin / Adam McArthur (ISC)** → [15](15-engagement-and-scoping.md) §1
- **$109k / $1.39M / Month 12 / Month 15** → [15](15-engagement-and-scoping.md) §1, [08](08-l2-architecture-decisions.md) D3/D5
- **MOC (Mass Open Cloud)** → [15](15-engagement-and-scoping.md) §6 (open item)
- **"research output, not escalation"** → [15](15-engagement-and-scoping.md) §9

---

## 4. The 8 things most likely to be asked (and where each is nailed)

1. **"Where do the DICOMweb endpoints live — Django or Rust?"** → [08](08-l2-architecture-decisions.md) D1 (variant C hybrid, fallback B); the one factual question is whether oxidicom is the only ingestion path.
2. **"Can't you just consume the existing NATS stream to build the index?"** → [11](11-oxidicom-and-ingestion.md) §5.2 — **no**, LONK carries no tags; variant C needs a *new* oxidicom event.
3. **"You re-parse files oxidicom already parsed — isn't that wasteful?"** → [12](12-l2-dicomweb-implementation.md) §7, [14](14-testing-and-validation.md) §5.2 (variant C exists exactly for this; proven live).
4. **"Why a `PACSStudy` table instead of GROUP BY?"** → [08](08-l2-architecture-decisions.md) D2, [12](12-l2-dicomweb-implementation.md) §1.2.
5. **"How do you KNOW it works?"** → [14](14-testing-and-validation.md) (97/97, live e2e, 11-bug ledger, the NOT-proven list).
6. **"Our `wait_for` on :11111 broke ingestion — why?"** → [11](11-oxidicom-and-ingestion.md) §7, [13](13-deployment.md) §8 bug #3 (never TCP-probe a DIMSE port; use C-ECHO).
7. **"Is STOW in scope / what did we commit to?"** → [08](08-l2-architecture-decisions.md) D3, [15](15-engagement-and-scoping.md) §2, §5, §10.
8. **"Does this fork CUBE / miniChRIS?"** → [13](13-deployment.md) §2/§10, [12](12-l2-dicomweb-implementation.md) §13 — no; drop-in app + Ansible wrap; prod = baked image.

---

## 5. Code, deploy, and brief pointers

- **Our L2 DICOMweb app:** `implementation/dicomweb-l2/` — `models.py`, `query_parser.py`,
  `dicomjson.py`, `serializers.py`, `multipart.py`, `qido_views.py`, `wado_views.py`,
  `stow_views.py`, `urls.py`, `signals.py`, `tasks.py`, `migrations/`, `tests/`,
  `management/commands/{reindex_pacs_instances,consume_dicomweb_index}.py`, plus `README.md` and
  `MAPPING.md` (the attribute→ORM-field table). Documented file-by-file in
  [12](12-l2-dicomweb-implementation.md).
- **The CUBE source (submodule):** `implementation/ChRIS_ultron_backEnd/chris_backend/` — the
  real Django backend the KB cites as `file:line`. Key dirs: `core/` (models, storage, celery,
  api), `pacsfiles/` (PACS models, `lonk.py`, `consumers.py`, `tasks.py`, `serializers.py`),
  `config/settings/`. Mapped in [09](09-cube-rest-api.md) §12 and [10](10-cube-internals.md) appendix.
- **The deploy (Ansible wrap):** `deploy/ansible/` — `site.yml`, `group_vars/all.yml` (single
  source of truth for pins/ports/toggles), `roles/{prereqs,minichris,orthanc,sample_data,
  dicomweb_app,verify}/`, `compose-overrides/cube-port.yml`, `scripts/{smoke.sh,teardown.sh}`;
  the wrapped stack is the pinned submodule `deploy/vendor/miniChRIS-docker/`. Documented in
  [13](13-deployment.md).
- **The spoken cheat-sheet:** [`../MEETING_BRIEF.md`](../MEETING_BRIEF.md) — executive narrative,
  the stack in 5 minutes, talking points, technical Q&A.
- **Definitions:** [`00-glossary.md`](00-glossary.md) — one crisp sentence per term, alphabetical.
