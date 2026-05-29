# DICOMweb on ChRIS/CUBE — Client Deliverable Bundle

This folder is the self-contained package of everything we're sharing on the
DICOMweb (QIDO-RS / WADO-RS / STOW-RS) work for CUBE: the research write-up BCH
asked for, the prototype implementation that de-risks it, the operations/deploy
tooling to stand it up, and supporting technical reference.

It corresponds to the grant deliverable **TA2 §2.6.1.6** — *Imaging-Native APIs
(e.g. DICOMweb) & Regression Testing* — making QIDO-RS, WADO-RS, and STOW-RS
operational on CUBE.

> **Status in one line:** the research output is complete, and the design is
> validated by a working prototype — **97/97 tests pass** in a real CUBE checkout,
> with all three endpoints exercised over live HTTP. See `proposal/` for the
> findings and `reference/14-testing-and-validation.md` for the evidence ledger,
> including an honest list of what is **not** yet proven.

---

## What's inside

| Folder | What it is | Start with |
|---|---|---|
| **`proposal/`** | The BCH-facing research deliverables: what's missing for DICOMweb compliance, the proposed data model, architecture options (A/B/C) with a recommendation, the phased plan, and the Phase A implementation write-up. Includes the live OpenAPI dumps and the Phase A code patch. | `RESEARCH_TICKET_OUTPUT.md` |
| **`implementation/dicomweb-l2/`** | The full prototype `dicomweb` Django app — QIDO/WADO/STOW views, the DICOM-JSON encoder, the query parser, multipart handling, the two indexing paths, migrations, and the test suite. Drop-in for `chris_backend/dicomweb/`. | `README.md`, then `MAPPING.md` |
| **`operations/ansible/`** | The deployment that wraps miniChRIS and overlays the app: `site.yml`, the six roles, compose overrides, and the `smoke.sh` / `teardown.sh` scripts. How to actually stand it up. | `ansible/README.md` |
| **`reference/`** | Supporting technical reference: the ChRIS/CUBE architecture, the DICOM and DICOMweb standards, the CUBE data model and REST API, the ingestion path (oxidicom), the implementation walkthrough, deployment notes, and the testing evidence. | `01-chris-architecture.md`, `05-dicomweb-qido-wado-stow.md` |

---

## Suggested reading order

1. **`proposal/RESEARCH_TICKET_OUTPUT.md`** — the lead document: what's missing,
   the recommended approach, and the open questions.
2. **`proposal/CURRENT_API.md`** — CUBE's API today and the gap vs DICOMweb.
3. **`proposal/QIDO_PLAN.md`** — the phased implementation plan (A–E).
4. **`proposal/PHASE_A_IMPLEMENTATION.md`** — the shipped foundation + validation log.
5. **`implementation/dicomweb-l2/README.md`** — the prototype, with its honest
   limitations section.
6. **`operations/ansible/README.md`** — how to deploy and smoke-test it.
7. **`reference/`** — depth on any term, subsystem, or decision as needed.

---

## The three endpoints at a glance

| Service | Verb | What it does | PS3.18 |
|---|---|---|---|
| **QIDO-RS** | Query | Search Studies / Series / Instances, return DICOM JSON Model | §10.6 |
| **WADO-RS** | Retrieve | Fetch DICOM objects, metadata, and native frames/bulkdata | §10.4 |
| **STOW-RS** | Store | Push new DICOM instances into the archive | §10.5 |

All three mount under `/dicom-web/pacs/<pacs_identifier>/…` and reuse CUBE's
existing authentication chain unchanged. Full endpoint→handler map is in
`reference/12-l2-dicomweb-implementation.md` §14.

---

## How to run the test suite

The prototype's suite runs inside a real CUBE checkout via CUBE's own harness:

```sh
just test dicomweb
```

This builds `cube:dev`, stands up Postgres, applies the migrations, and runs the
unit + integration tests. The framework-free core (encoders, query parser,
multipart, serializers) also runs standalone in a plain venv. Details and the
full evidence ledger: `reference/14-testing-and-validation.md`.
