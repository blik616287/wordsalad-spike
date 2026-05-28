# ISC deliverables — ATLAS / ChRIS engagement

What Insight Softmax Consulting (ISC) has been asked to deliver, organized by certainty/timeframe. Sources are listed at the bottom; every claim in this document points at a specific document and section/timestamp.

---

## TL;DR

| Layer | Scope | Status | Audience |
|---|---|---|---|
| **L1. Research spike** | A written document describing what CUBE needs to become DICOMweb-compliant and how to do it. | **Done** — covered by `CURRENT_API.md`, `QIDO_PLAN.md`, and Phase A code in `dicomweb/`. We exceeded the ask. | BCH (Rudolph + Joshua) |
| **L2. MVP implementation** | Working DICOMweb endpoints on a single CUBE instance + the BCH dataset re-DICOMized + an inference plugin running through. | **Not started.** Estimated ~1–2 months, 1 engineer per ISC's own MVP proposal. Conditioned on BCH greenlight. | BCH engineering team + funding go-ahead |
| **L3. Grant §2.6.1.6** | WADO-RS + STOW-RS + QIDO-RS endpoints operational within TD constraints (Month 12) + regression suite (Month 15). | **Not started.** $109k budgeted. Joint with Red Hat. Conditioned on ARPA-H grant approval. | ARPA-H grant deliverable, BCH+RedHat lead |
| **L4. Broader §2.6.1 + other ISC pipeline** | $1.39M Airflow+ChRIS+DICOMweb full workstream, plus 8 other ranked grant opportunities. | **Aspirational.** Per ISC's internal opportunity analysis. Conditioned on relationship-building + grant approval. | ISC commercial pipeline |

The L1 deliverable is what was scoped at the May 19, 2026 meeting. L2 is what ISC's own MVP proposal sketched. L3 is what the grant proposal actually budgets and dates. L4 is ISC's internal positioning.

---

## L1 — Research spike (immediate)

### What BCH actually asked for

From the May 19 scoping meeting (`DICOMweb-QIDO-RS-intial-scoping-16d6d9b4-e0be.md`):

> **Joshua [25:32]**: "For the scope of the work that we initially reached out about… that ticket is just a research ticket to figure out what is involved in achieving that compliance. We'd have a kickoff call, engineer goes off, does the research, writes up a document of what's missing, what needs to happen, what needs to be done, a proposal for how to go about doing that. We discuss it, break it up into tickets and get going."

> **Alex [38:02]**: "It's not weeks and weeks of work to come back with some preliminary ideas… maybe a day, maybe two worth of time, something like that at this point."

Scope explicitly excluded by both sides:
- Security / encryption / TD-internal compliance (Tommy [29:21] flagged, Alex [29:58] and Joshua [30:14] confirmed: "we're currently just talking about communication, for interface")
- Indexing as a separate service (Rudolph [33:13–34:56]: deferred to "the actual product down the line" — not the spike)

### What ISC delivers for L1

A research document that answers, for BCH:
1. **What's in CUBE today that's relevant.** (Current API surface, the `pacsfiles` data model, what tags are stored, what endpoints exist.)
2. **What's missing for DICOMweb compliance.** (Instance-level tags, DICOM JSON Model renderer, QIDO query semantics, WADO-RS retrieval surface, STOW-RS upload surface.)
3. **A proposal for how to do it.** (Concrete file paths, data model changes, URL surface, sequencing/milestones, open questions.)
4. **An effort estimate.** (Engineer-weeks per phase.)

### How we've satisfied it

- **`CURRENT_API.md`** (15 KB, ~270 lines) — the "what's in CUBE today" answer. Live OpenAPI dump (`schema.yaml`, `schema.split.yaml`), per-domain route table, PACS deep dive, **gap analysis vs QIDO-RS** mapping each requirement to a specific change site.
- **`QIDO_PLAN.md`** (~580 lines) — the "how to do it" answer, scoped to QIDO-RS. Locked decisions, URL surface, schema changes, ingest pipeline changes, query parsing, renderer, view layer, RetrieveURL strategy, OHIF smoke-test checklist, capacity sizing, sequencing across 5 phases, open questions.
- **Phase A code** (in `ChRIS_ultron_backEnd/chris_backend/dicomweb/` and `pacsfiles/`) — the schema + ingest foundation. New `PACSInstance` model + migration, six new `PACSSeries` fields + migration, Celery indexing task wired into `PACSSeriesSerializer.create`, 103/103 existing tests still passing, 9/9 new tests added. Roughly 5–6 weeks of plan's worth of work, ~1 week's worth executed.

This is more than the "day or two" research ticket Alex sized. **We should send L1 back to BCH before doing more engineering** — see "Recommended next move" at the bottom.

---

## L2 — MVP implementation (conditional, ~1–2 months)

### What ISC's own MVP proposal commits to

From `ATLAS_ ChRIS Federated Research Platform -- MVP Proposal.txt` (ISC-authored, May 1, 2026 context):

> **Precondition 1: DICOMweb Compliance in CUBE (~1–2 months, 1 engineer)**
> 1. Extend the schema to capture a well-defined tag set at all four DICOM hierarchy levels
> 2. Expose QIDO-RS endpoints in Django querying that schema
> 3. Expose WADO-RS endpoints proxying retrieval from storage

(STOW-RS is **not** mentioned in the MVP proposal — see the L3 reconciliation note below.)

> **Precondition 2: Dataset Preparation (parallel, bounded effort)** — ISC re-DICOMizes the BCH public dataset. "Insight Softmax Consulting has tooling for DICOM conversion and familiarity with edge cases in the tag structure."

> **MVP: End-to-End Demo (~1–2 months, after Preconditions complete)** — single ChRIS instance on MOC → BCH dataset ingested → QIDO-RS metadata browsable → existing BCH inference plugin dispatched programmatically → results returned centrally.

### What ISC delivers for L2

| Item | Owner | Source |
|---|---|---|
| QIDO-RS endpoints on CUBE | ISC | MVP proposal Precondition 1 |
| WADO-RS endpoints on CUBE | ISC | MVP proposal Precondition 1 |
| BCH public dataset converted to valid DICOM | ISC | MVP proposal Precondition 2 |
| MOC instance with CUBE running | TBD (open question at May 19 mtg) | MVP proposal Open Items #2 |
| BCH inference plugin dispatched programmatically | BCH | Scoping meeting [10:07–10:37] — Alex confirmed BCH owns this piece |
| Glue scripting between metadata browse → plugin dispatch → results return | ISC | MVP proposal MVP section |

### Explicitly out of L2 scope (per MVP proposal)

- Multi-site / multi-institution (Phase 2)
- Federated learning / distributed model training (Phase 2)
- Live researcher UX with interactive cohort selection (Phase 2)
- General catalog for non-DICOM data types
- Production de-identification (out of scope; public data only)
- Gradient Health (GH) integration (Phase 2)
- OHIF viewer integration — _may emerge for free once DICOMweb compliance lands, no extra engineering_

---

## L3 — Grant §2.6.1.6 (conditional on grant approval, Month 12 + Month 15)

### What the grant proposal commits to

From `docsend_156_pages.pdf` page 78:

> **Task 2.6.1.6 — Imaging-Native APIs (e.g. DICOMweb) & Regression Testing**
> **Budget:** $109k  **Prime:** BCH, Red Hat
> **Objective:** Expose DICOMweb-compliant APIs for standards-based imaging data access and establish regression testing for pipeline stability.
> **Milestones/Deliverables:**
> - **Month 12: DICOMweb WADO-RS, STOW-RS, and QIDO-RS endpoints operational within TD constraints.**
> - **Month 15: Regression test suite covering pipeline I/O, async execution, and DICOMweb endpoints validated for release gating.**

### Key reconciliation with L2

**The grant says all three (WADO + STOW + QIDO) ship together as one $109k sub-task. The MVP proposal said QIDO + WADO only, STOW deferred.** This is the single biggest mismatch between ISC's own framing and the grant's contractual language. L2 work should at minimum lay the foundation for STOW-RS (the Phase A `PACSInstance` model already does — STOW would create `PACSInstance` rows as a side-effect of upload).

### Adjacent grant sub-tasks touching our work

These are not ISC's deliverables but the seams matter:

| Grant ID | Title | Budget | Prime | Why it matters to us |
|---|---|---|---|---|
| §2.7.1 | Normalized At-rest Format for Radiology DICOM | $946k | BCH | Month 12 deliverable says "deployed to cloud object storage **with DICOMweb access**". The at-rest format and the DICOMweb endpoints converge by Month 12. |
| §2.7.1.1 | Ingestion-time Data Tagging, Cleaning, DICOM Standardization | $437k | BCH | Upstream of our `PACSInstance` indexing — tag normalization, missing metadata imputation, transfer syntax conversion, private tag removal. Done in Airflow as ChRIS plugins. |
| §2.7.1.2 | Cloud-native At-rest Format for Radiology | $400k | UoU | Designs for "DICOMweb WADO-RS compatibility" + references an **"ATLAS DICOMweb gateway"** (federated layer above per-TD DICOMweb endpoints — Phase 2 of the MVP). |
| §2.6.1.1–2.6.1.5 | Airflow deployment, orchestration, async, filesystem-bus I/O, scalability | $1.530M | BCH, Red Hat | Same parent task (§2.6.1) as our DICOMweb work. If we do §2.6.1.6 we should expect to interact with these sibling sub-tasks. |
| §2.6.2 | Data Provenance (Open Lineage) | $656k | BCH | DICOMweb retrieval events should emit Open Lineage facets. |

---

## L4 — ISC's broader pipeline in the grant (aspirational)

From `ATLAS_Grant_Opportunity_Analysis_BCH_Final.docx`, ISC has ranked 9 grant opportunities by fit. Listed here for completeness; only the DICOMweb work has any current BCH-side commitment.

| # | Tier | Task | Budget | ISC framing |
|---|---|---|---|---|
| 1 | T1 | §2.2.2.2 Neocloud Orchestration (MOC bare-metal OpenShift) | $300–500k | "Core maintainers of OpenStack Ironic — unique competitive advantage" |
| 2 | T1 | §2.2.1 TD Infrastructure as Code Definition | $1.09M | OpenShift + K8s + DevOps fit |
| 3 | T1 | §2.9.3 Federated Learning: MedGemma 27B training | $1.8M | "DICOM AI platform alignment — highest dollar value" |
| 4 | T2 | §2.6.1 Airflow Pipelines with ChRIS + DICOMweb | $1.39M | **Our current foothold.** $109k DICOMweb sub-task is the named entry point. |
| 5 | T2 | §3.4.3 VA VINCI integration | $1.5M | "Explicit FTE slots — high staff need" |
| 6 | T2 | §2.9.1.1 Radiology DICOM data loader | $520k | DICOM expertise match |
| 7 | T2 | §2.7.1 / 2.7.2 DICOM ingestion + standardization | $787k | "DICOM standardization — our platform experience" |
| 8 | T3 | §2.3.2 Developer Ops Tooling | $437k | Broad DevOps fit |
| 9 | T3 | §2.11.17 / 2.11.18 FDA RSTKs scaling | $300k | HPC + OpenShift |

Plus a **marketplace product** opportunity: list the Retuve hip-dysplasia plugin (`retuve-chris-plugin/` in this workspace) in the ATLAS App Marketplace (§3.5.1, $700k integration budget). Compute-credit revenue, not consulting.

These are positioning artifacts, not deliverables. They become deliverables only if BCH/UoU/GH respectively engage ISC on those sub-tasks.

---

## Funding and contracting state

As of May 19, 2026 (scoping meeting):

- **Grant approval not yet given.** Rumor (via Red Hat Summit) had it "sitting on Clark Minor's desk" at ARPA-H awaiting final approval. No green light confirmed.
- **BCH has internal headcount budget** — "the budget currently has at least two more people to be onboarded" (Rudolph [14:26]). This is grant-allocated budget, not BCH discretionary. Could fund ISC pre-approval as a contractor instead of hiring directly.
- **Each prime has its own budget**, not gated by GH. Rudolph [22:48]: "Each PI / each group is getting their own budget. It's not as though everything goes to Gradient Health and everyone has to go to them."
- **ISC is working partly on spec** — Joshua [10:56]: "We've already established that you guys are able to work to some degree on spec while we're waiting for funding to get approved."
- **Hire-from-scratch alternative**: ~4 month lag time before someone could be onboarded if BCH went that route instead of contracting ISC (Rudolph [15:10]).

**Implication**: L1 effort cost is bounded and reasonable for ISC to absorb. L2 should be conditioned on at least a verbal commitment from BCH that the headcount budget will be applied to ISC. L3 requires the grant to actually land.

---

## Open dependencies (from BCH, before L2 starts)

These were left open at the May 19 meeting:

1. **Kickoff call** — Joshua [42:10]: "We'd have a kickoff call where everyone's there." Not yet scheduled.
2. **Comms channel** — BCH uses a matrix server + daily standups. ISC would need to be invited.
3. **Engineer identification** — Alex + Tommy planned to "powwow" after the meeting and "hit back either later today or tomorrow" with who from ISC would do the work. Status not in our document set.
4. **MOC instance** — open from the MVP proposal: "Is there a ChRIS instance already running on MOC, or does one need to be stood up?" If standing up: lead time?
5. **Dataset hand-off** — Adam offered to take re-DICOMization. Needs BCH to share the public dataset links.

---

## What to do with this document

This file is layered so it can be:
- **Kept internal** as a single source of truth for what's promised, by whom, when.
- **Trimmed to L1 + an effort estimate** and sent back to BCH as the research-ticket deliverable they asked for. (`CURRENT_API.md` + `QIDO_PLAN.md` are the supporting artifacts.)
- **Used to brief a new ISC engineer** stepping into the engagement.

### Recommended next move

Send L1 to BCH (Rudolph + Joshua) before doing more engineering. We have a research-ticket deliverable that **exceeds the day-or-two scope Alex sized**, and the next phase (Phase B of `QIDO_PLAN.md` — renderer + query parser) commits more engineering time on spec. Closing the loop with BCH first means:

- Confirming the spike output meets what Joshua had in mind for "what's missing, what needs to be done, a proposal."
- Surfacing the **scope mismatch on STOW-RS** (MVP proposal said no, grant says yes) so BCH and ISC are aligned before more code lands.
- Getting the kickoff call scheduled, ISC invited to BCH's matrix server, and the engineer-of-record formally introduced.
- Starting the conversation about whether BCH's internal headcount budget covers the L2 work or whether we wait for grant approval.

---

## Sources

- **`ATLAS_ ChRIS Federated Research Platform -- MVP Proposal.txt`** — ISC-authored MVP proposal, references May 1, 2026 meeting.
- **`DICOMweb-QIDO-RS-intial-scoping-16d6d9b4-e0be.md`** — transcript of the May 19, 2026 scoping meeting (Alex Scammon, Tommy Aldo Sonin, adamm / ISC; Rudolph Pienaar, Joshua Kanner / BCH).
- **`ATLAS_Grant_Opportunity_Analysis_BCH_Final.docx`** — ISC-internal opportunity analysis dated April 2026.
- **`docsend_156_pages.pdf`** — ATLAS grant proposal (full WBS with budgets). Relevant sections: §2.6.1 (page 75), §2.6.1.6 (page 78), §2.7.1 (page 82), §2.7.1.2 (page 83). TOC on pages 1–9.
- **`notes.txt`** — DICOM PS3.18 §10.6 (QIDO-RS spec), dcm4chee QIDO-RS docs, Mass Open Cloud links.
- **`CURRENT_API.md`**, **`QIDO_PLAN.md`**, **`schema.yaml`**, **`schema.split.yaml`** — artifacts already produced for L1.
- **Phase A code** — `ChRIS_ultron_backEnd/chris_backend/dicomweb/` (new app) and modifications to `pacsfiles/`.
