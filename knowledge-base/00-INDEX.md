# 00 — Master Index: DICOMweb-on-CUBE Knowledge Base

> Searchable master index for the ARPA-H ATLAS **DICOMweb-on-CUBE** spike (QIDO-RS /
> WADO-RS / STOW-RS on `ChRIS_ultron_backEnd`). Prime: Gradient Health; partner: Boston
> Children's Hospital (BCH). This index is the front door — start here, then jump to the
> file that answers your question.

**Who this is for:** the ISC engineer (new to the ChRIS stack) who has to be the technical
voice in the BCH stakeholder meeting. For the meeting cheat-sheet itself, see
[`../MEETING_BRIEF.md`](../MEETING_BRIEF.md). For unfamiliar terms, see
[`00-glossary.md`](00-glossary.md).

---

## How to search this KB

Everything is grounded Markdown — `ripgrep` it. From the repo root:

```sh
rg -i 'oxidicom'                  knowledge-base/      # find every mention
rg -i 'OXIDICOM_SCP_AET'          knowledge-base/      # exact env var / port / setting
rg -i 'PACSStudy|PACSInstance'    knowledge-base/      # the new models
rg -i '00100010|PatientName'      knowledge-base/      # a DICOM tag (hex or keyword)
rg -l 'pg_trgm'                   knowledge-base/      # which files mention a topic
rg -i 'variant c|hybrid'          knowledge-base/      # the architecture recommendation
```

Tip: DICOM tags are searchable in **both** forms — 8-hex (`00080060`) and keyword (`Modality`).
The keyword↔tag↔field map lives in [`../implementation/dicomweb-l2/MAPPING.md`](../implementation/dicomweb-l2/MAPPING.md).

---

## The KB files (number · title · what it is · questions it answers)

| # | File | One-line description | Questions it answers |
|---|---|---|---|
| 00 | [`00-INDEX.md`](00-INDEX.md) | This master index + search guide | "Where do I find X?" "How do I grep this KB?" |
| 00 | [`00-glossary.md`](00-glossary.md) | One-sentence definitions of every term | "What is pfcon / NATS / VR / collection+json / ATLAS?" |
| 01 | [`01-chris-architecture.md`](01-chris-architecture.md) | The whole ChRIS platform: CUBE, pfcon, pman, oxidicom, pfdcm, NATS, the plugin/feed/pipeline model, component diagram, ports | "What are all these services?" "How does a plugin run?" "How does DICOM enter (push vs pull)?" "Where would QIDO/WADO/STOW fit?" |
| 02 | [`02-cube-and-pacs-data-model.md`](02-cube-and-pacs-data-model.md) | CUBE internals: DRF + collection+json, auth chain, the `pacsfiles` data model (`PACS`/`PACSSeries`/`PACSFile`), how oxidicom ingests, Phase A additions, the DICOM→model field map | "Where does the StudyInstanceUID live?" "What's `PACSSeries`?" "What did Phase A add?" "How does the parent-folder walk work?" |
| 03 | [`03-minichris-docker.md`](03-minichris-docker.md) | The deployable stack we wrap: compose profiles, service-by-service ports/images, networks/volumes, credentials, how to push DICOM in, bring-up/teardown | "What ports/images?" "How do I start the PACS pipeline?" "How do I push a study to oxidicom?" "What are the dev creds?" |
| 04 | [`04-dicom-standard.md`](04-dicom-standard.md) | DICOM from zero: Patient→Study→Series→Instance, SOP Class/Instance, data elements, VR, UIDs, file format (PS3.10), transfer syntaxes, modalities, the key tags | "What's a SOP Instance?" "What's a VR / transfer syntax?" "What's the DICOM JSON Model?" "What tags matter?" |
| 05 | [`05-dicomweb-qido-wado-stow.md`](05-dicomweb-qido-wado-stow.md) | The core DICOMweb reference (PS3.18): URL templates, query params, matching semantics, media types, status codes, the STOW response, curl examples for all three services | "What's the QIDO URL for series?" "How does WADO multipart work?" "What does a STOW response body look like?" "What status code on partial store?" |
| 06 | [`06-pydicom.md`](06-pydicom.md) | pydicom usage: `dcmread`, header-only reads, the DA/TM parsing bug, STOW upload handler skeleton, synthetic test fixtures, VR type gotchas | "How do we read `.dcm` headers?" "Why `stop_before_pixels`/`force`?" "How do we build test DICOMs?" "How does STOW parse uploads?" |
| 07 | [`07-orthanc.md`](07-orthanc.md) | Orthanc as test PACS + DICOMweb conformance oracle: Docker, native REST upload, its QIDO/WADO/STOW, C-STORE push into oxidicom, `dicomweb-client` harness | "How do I seed DICOM?" "How do I validate our output against a reference?" "How do I push studies into miniChRIS?" |
| 08 | [`08-l2-architecture-decisions.md`](08-l2-architecture-decisions.md) | The decision record: D1 (where endpoints live — variant C/B), D2 (explicit `PACSStudy`), D3 (STOW in scope), D4 (`pg_trgm`), D5 (L1–L4 layering); tradeoff tables + the one question to ask BCH | "What do we recommend and why?" "What's the hybrid variant C?" "What must BCH decide?" "How is the work phased?" |

---

## Topic → file lookup

| If you want to know about… | Go to |
|---|---|
| ChRIS platform overview, all components, ports | [01](01-chris-architecture.md) §2, §7 |
| Plugin / feed / pipeline compute model, pfcon/pman | [01](01-chris-architecture.md) §4 |
| Push path (oxidicom C-STORE) vs pull path (pfdcm C-FIND/C-MOVE) | [01](01-chris-architecture.md) §5; [02](02-cube-and-pacs-data-model.md) §4 |
| LONK / NATS progress protocol (and that it carries **no tags**) | [01](01-chris-architecture.md) §5.1; [08](08-l2-architecture-decisions.md) "Background" |
| CUBE auth (Token/Basic/Session/LDAP), `pacs_users` | [02](02-cube-and-pacs-data-model.md) §2 |
| `PACSSeries` / `PACSFile` / `PACS` models and indexes | [02](02-cube-and-pacs-data-model.md) §3 |
| `PACSInstance` (Phase A), `index_pacs_instance` task | [02](02-cube-and-pacs-data-model.md) §6 |
| DICOM tag → CUBE field map (today vs Phase A vs gap) | [02](02-cube-and-pacs-data-model.md) §5; [MAPPING.md](../implementation/dicomweb-l2/MAPPING.md) |
| miniChRIS compose profiles, networks, volumes, creds | [03](03-minichris-docker.md) §2–§5 |
| How to push DICOM into the stack | [03](03-minichris-docker.md) §8; [07](07-orthanc.md) §6 |
| DICOM information model, SOP Class/Instance, UIDs, VR | [04](04-dicom-standard.md) §1–§5 |
| Transfer syntaxes, file format (preamble/DICM/file-meta) | [04](04-dicom-standard.md) §6–§7 |
| DICOM JSON Model encoding (PN, DA/TM, IS/DS gotchas) | [04](04-dicom-standard.md) §11; [05](05-dicomweb-qido-wado-stow.md) §1 |
| QIDO-RS URLs, query params, matching, status codes | [05](05-dicomweb-qido-wado-stow.md) §2 |
| WADO-RS URLs, multipart/related, transfer-syntax negotiation | [05](05-dicomweb-qido-wado-stow.md) §3 |
| STOW-RS URLs, content types, Store Instances Response | [05](05-dicomweb-qido-wado-stow.md) §4 |
| pydicom read/write, the DA/TM bug, test fixtures | [06](06-pydicom.md) §1, §6, §9 |
| Orthanc as conformance oracle / `dicomweb-client` harness | [07](07-orthanc.md) §4 |
| Architecture recommendation (C / fallback B), the BCH question | [08](08-l2-architecture-decisions.md) D1 |
| Explicit `PACSStudy` vs GROUP BY rollups | [08](08-l2-architecture-decisions.md) D2 |
| STOW-RS in scope + why | [08](08-l2-architecture-decisions.md) D3 |
| Fuzzy/wildcard matching, `pg_trgm` | [08](08-l2-architecture-decisions.md) D4; [04](04-dicom-standard.md) §4 |
| L1/L2/L3/L4 layering, scope guardrails | [08](08-l2-architecture-decisions.md) D5 |

---

## Keyword map (for fast ripgrep)

| Search term | Where it's defined / discussed |
|---|---|
| `CUBE`, `ChRIS_ultron_backEnd`, `Django`, `DRF` | 01 §2, 02 §1–§2 |
| `oxidicom`, `C-STORE`, `SCP`, `11111`, `OXIDICOM_SCP_AET`, `ChRIS` (AET) | 01 §5.1, 02 §4, 03 §3.3 |
| `pfdcm`, `C-FIND`, `C-MOVE`, `4005`, `PACSQuery`, `PACSRetrieve` | 01 §5.2, 02 §3.5 |
| `pfcon`, `pman`, `5005`, `5010`, `in-network`, `out-of-network` | 01 §4 |
| `NATS`, `LONK`, `4222`, `progress`, `done`, `error`, `sse` | 01 §5.1, 03 §3.3, 08 Background |
| `RabbitMQ`, `Celery`, `main1`, `main2`, `periodic`, `5672`, `register_pacs_series` | 01 §4.4, 02 §4, 03 §3.1 |
| `PACSSeries`, `PACSFile`, `PACS`, `unique_together`, `ChrisFolder` | 02 §3 |
| `PACSInstance`, `PACSStudy`, `index_pacs_instance`, `_find_series_for_file` | 02 §6, 08 D2, MAPPING.md |
| `pydicom`, `dcmread`, `stop_before_pixels`, `force`, `_parse_dicom_time` | 06 §1, §6 |
| `QIDO-RS`, `WADO-RS`, `STOW-RS`, `application/dicom+json`, `multipart/related` | 04, 05, 08 |
| `DICOM JSON Model`, `vr`, `Value`, `BulkDataURI`, `InlineBinary` | 04 §11, 05 §1 |
| `SOPClassUID`, `SOPInstanceUID`, `StudyInstanceUID`, `SeriesInstanceUID`, `TransferSyntaxUID` | 04 §5, MAPPING.md |
| `Modality`, `Rows`, `Columns`, `NumberOfFrames`, `RetrieveURL`, `ModalitiesInStudy` | 04 §8–§10, 05 §2.5 |
| `pg_trgm`, `fuzzymatching`, `wildcard`, `ILIKE`, `__trigram_similar` | 08 D4, 05 §2.4 |
| `Orthanc`, `8042`, `4242`, `MINICHRISORTHANC`, `orthancteam`, `dicomweb-client` | 03 §3.3/§6, 07 |
| `miniChRIS`, `docker compose`, `pacs profile`, `chrisomatic`, `chris1234` | 03 |
| `variant A/B/C`, `hybrid`, `D1`, `PACSStudy`, `D2`, `D3`, `D4` | 08 |
| `ARPA-H`, `ATLAS`, `BCH`, `Gradient Health`, `§2.6.1.6`, `L1/L2/L3/L4`, `TD` | 08 D5, RESEARCH_TICKET_OUTPUT.md |

---

## Implementation & deployment pointers

| Artifact | Path | What it is |
|---|---|---|
| **L2 DICOMweb app** | [`../implementation/dicomweb-l2/`](../implementation/dicomweb-l2/) | A reviewable Django `dicomweb` app implementing QIDO/WADO/STOW on the Phase A foundation — a drop-in for `chris_backend/dicomweb/`. **L2 test implementation, not a merged CUBE PR.** |
| → README | [`../implementation/dicomweb-l2/README.md`](../implementation/dicomweb-l2/README.md) | What it implements, how to apply/test it, and the **Known limitations** (frames/bulkdata are `501` stubs, no transcoding, HTTP/DB tests need a live CUBE checkout). |
| → attribute map | [`../implementation/dicomweb-l2/MAPPING.md`](../implementation/dicomweb-l2/MAPPING.md) | DICOM attribute → model-field map per level; matching semantics by VR. |
| **Ansible deploy** | [`../deploy/ansible/`](../deploy/ansible/) | One-command deployment that **wraps** miniChRIS-docker (`pacs` profile), runs a test Orthanc, seeds DICOM, overlays the L2 code, and smoke-tests QIDO/WADO/STOW. |
| → README | [`../deploy/ansible/README.md`](../deploy/ansible/README.md) | `ansible-playbook -i inventory.ini site.yml`, tags, the integration seam, sample-data modes, teardown, caveats. |
| **Engagement framing** | [`../proposal-to-bch/RESEARCH_TICKET_OUTPUT.md`](../proposal-to-bch/RESEARCH_TICKET_OUTPUT.md) | The research deliverable to BCH: what's missing, the proposed model, variants A/B/C, sequencing, open items. |
| Prior-phase artifacts | `../proposal-to-bch/{CURRENT_API,QIDO_PLAN,PHASE_A_IMPLEMENTATION}.md`, `schema.yaml` | Deep API map, phased plan, Phase A walkthrough, live OpenAPI dump. |

---

## The 4 things to remember

1. **DICOM enters CUBE two ways:** pushed via **oxidicom** (Rust C-STORE SCP, port 11111) or pulled via **pfdcm** (C-FIND/C-MOVE). See [01](01-chris-architecture.md) §5.
2. **`PACSSeries` is the central row** today; **Phase A added `PACSInstance`** (instance level). QIDO/WADO/STOW are the unbuilt view layer. See [02](02-cube-and-pacs-data-model.md).
3. **DICOMweb endpoints belong in Django/CUBE** (for the auth chain); the recommendation is **variant C (hybrid)**, fallback **B**. See [08](08-l2-architecture-decisions.md) D1.
4. **Deployment wraps miniChRIS-docker** — do not fork it. See [03](03-minichris-docker.md) and the [Ansible deploy](../deploy/ansible/README.md).
