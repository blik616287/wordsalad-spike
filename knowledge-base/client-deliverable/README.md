# DICOMweb on ChRIS/CUBE — Client Deliverable Bundle

This folder is the self-contained package of everything we're sharing on the
DICOMweb (QIDO-RS / WADO-RS / STOW-RS) work for CUBE: the research write-up BCH
asked for, the prototype implementation that de-risks it, the operations/deploy
tooling to stand it up, and supporting technical reference.

It corresponds to the grant deliverable **TA2 §2.6.1.6** — *Imaging-Native APIs
(e.g. DICOMweb) & Regression Testing* — making QIDO-RS, WADO-RS, and STOW-RS
operational on CUBE.

> **Status in one line:** the research output is complete, and the design is
> validated by a working prototype — **97/97 unit/integration tests pass** in a
> real CUBE checkout, and the **deployment stands up the full stack and serves
> QIDO/WADO/STOW over live HTTP** (latest cold-cycle validation: smoke 9/0/1, API
> 44/0). See `proposal/` for the findings, `operations/VALIDATION_REPORT.md` for
> the deployment evidence, and `reference/14-testing-and-validation.md` for the
> code-level evidence ledger.

---

## Quick start — run the testable deployment

Stand up the whole stack (CUBE + oxidicom + Orthanc), ingest sample DICOM,
overlay the L2 DICOMweb code, and run the validation — on any Docker host:

```sh
cd operations
tooling/bootstrap.sh    # one-time: builds a venv + vendors miniChRIS (no system installs)
./run.sh                # deploy + auto-run smoke + API validation (QIDO/WADO/STOW)
```

Then hit it (PACS id is the calling AET — here the test Orthanc's `SPIKEORTHANC`):

```sh
curl -u chris:chris1234 -H 'Accept: application/dicom+json' \
  http://localhost:8000/dicom-web/pacs/SPIKEORTHANC/studies
```

Re-run just the validation any time: `./run.sh --tags verify`, or standalone
`PACS_ID=SPIKEORTHANC ORTHANC_BASE_URL=http://localhost:8142 tooling/api_tests.sh`.
Tear down with `tooling/teardown.sh`.

- **Prerequisites + full walkthrough + troubleshooting:** `operations/RUN_GUIDE.md`
- **What the validation proves (with evidence):** `operations/VALIDATION_REPORT.md`

**Prove it runs anywhere (clean-room).** To verify on a machine with nothing
pre-installed, one command provisions a throwaway Ubuntu 24.04 KVM, installs only
the documented prereqs, copies this bundle in, and runs `bootstrap.sh` + `run.sh`
inside the guest:

```sh
operations/tooling/cleanroom_kvm.sh        # provision -> deploy -> validate -> destroy
```

(Needs KVM + libvirt + virt-install on the host; see `RUN_GUIDE.md` §7.)

> Demo-grade stack with public dev credentials (`chris:chris1234`) — not for
> production.

---

## What's inside

| Folder | What it is | Start with |
|---|---|---|
| **`proposal/`** | The BCH-facing research deliverables: what's missing for DICOMweb compliance, the proposed data model, architecture options (A/B/C) with a recommendation, the phased plan, and the Phase A implementation write-up. Includes the live OpenAPI dumps and the Phase A code patch. | `RESEARCH_TICKET_OUTPUT.md` |
| **`implementation/dicomweb-l2/`** | The full prototype `dicomweb` Django app — QIDO/WADO/STOW views, the DICOM-JSON encoder, the query parser, multipart handling, the two indexing paths, migrations, and the test suite. Drop-in for `chris_backend/dicomweb/`. | `README.md`, then `MAPPING.md` |
| **`operations/`** | The runnable deployment: `tooling/bootstrap.sh` (venv + vendored miniChRIS), `run.sh` (one-command deploy + validation), `tooling/api_tests.sh` (QIDO/WADO/STOW + auth + integrity), `tooling/teardown.sh`, the Ansible `site.yml` + six roles, and the run/validation docs. **This is how you stand it up and test it.** | `RUN_GUIDE.md` (then `VALIDATION_REPORT.md`) |
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
6. **`operations/RUN_GUIDE.md`** — how to stand up and validate the live
   deployment (see also the Quick start above); `operations/VALIDATION_REPORT.md`
   for the evidence it works.
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

## Two ways to test

**1. The deployment (recommended) — live end-to-end.** See *Quick start* above:
`operations/tooling/bootstrap.sh` then `operations/run.sh` brings up the real
stack and auto-runs the smoke + API validation against live QIDO/WADO/STOW.
Full guide: `operations/RUN_GUIDE.md`; evidence: `operations/VALIDATION_REPORT.md`.

**2. The code-level unit/integration suite** — runs inside a real CUBE checkout
via CUBE's own harness:

```sh
just test dicomweb
```

This builds `cube:dev`, stands up Postgres, applies the migrations, and runs the
unit + integration tests (97/97). The framework-free core (encoders, query
parser, multipart, serializers) also runs standalone in a plain venv. Details and
the full evidence ledger: `reference/14-testing-and-validation.md`.
