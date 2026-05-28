# ISC ↔ BCH (ATLAS / DICOMweb in CUBE) — internal compendium

This folder is the **ISC-internal** working package for the BCH engagement on DICOMweb compliance in ChRIS\_ultron\_backEnd (CUBE). It contains everything ISC has produced for the engagement to date, the source materials the work was anchored to, and an internal review trail. The intended audience is anyone at ISC who needs to:

- Understand where the engagement stands;
- Present the proposal to BCH;
- Participate in or run the internal review of what's been produced.

If you're new to this engagement and have 15 minutes, read this README, then `proposal-to-bch/RESEARCH_TICKET_OUTPUT.md`, then `internal-review/ISC_DELIVERABLES.md`. That'll tell you 90% of what you need to know.

---

## Engagement in one paragraph

BCH is the primary technical execution partner on the ATLAS grant (ARPA-H, ~$60M+, prime is Gradient Health). On 2026-05-19, Rudolph Pienaar and Joshua Kanner of BCH met with Alex Scammon, Tommy Sonin, and Adam McArthur of ISC. The agreed first piece of work was a **research/spike ticket**: ISC scopes out what's involved in adding DICOMweb compliance (QIDO-RS, WADO-RS, STOW-RS) to CUBE. Joshua's exact language: *"engineer goes off, does the research, writes up a document of what's missing, what needs to happen, what needs to be done, a proposal for how to go about doing that. We discuss it, break it up into tickets and get going."* The grant proposal budgets this as TA2 §2.6.1.6 at $109k, with WADO+STOW+QIDO endpoints due Month 12 and a regression suite due Month 15. Funding is conditional on ARPA-H grant approval, which has not yet been given; BCH has internal headcount budget that could fund pre-approval work. ISC has produced the research output, plus first-phase implementation code that derisks the larger design.

---

## What's in this folder

```
.                                        ← repo root (the ISC-internal compendium)
├── README.md                            ← you are here
├── proposal-to-bch/                     ← what we send to BCH (lead with this)
│   ├── RESEARCH_TICKET_OUTPUT.md        ← 5-page summary; the lead document
│   ├── CURRENT_API.md                   ← detailed: CUBE today + gap analysis
│   ├── QIDO_PLAN.md                     ← detailed: phased implementation plan
│   ├── PHASE_A_IMPLEMENTATION.md        ← detailed: what's coded + validations
│   ├── schema.yaml                      ← live OpenAPI 3.0.3 dump
│   ├── schema.split.yaml                ← codegen variant
│   └── code/                            ← Phase A code, applicable as one patch
│       ├── README.md                    ← how to apply
│       ├── phase-a.patch                ← single `git apply` patch (548 lines)
│       └── source/                      ← readable copies of new files
├── internal-review/                     ← ISC-only — do NOT open in front of BCH
│   ├── ISC_DELIVERABLES.md              ← L1/L2/L3/L4 scope reference
│   ├── REVIEW_RESPONSE_ROUND_1.md       ← response to Adam McArthur's review (2026-05-22)
│   └── SCOPE_GUARDRAILS.md              ← meeting cheat sheet + scope-creep redirects
└── source-materials/                    ← input documents (read-only references)
    ├── ATLAS_ ChRIS Federated Research Platform -- MVP Proposal.txt
    ├── ATLAS_Grant_Opportunity_Analysis_BCH_Final.docx
    ├── DICOMweb-QIDO-RS-intial-scoping-16d6d9b4-e0be.md
    └── notes.txt
```

The full ATLAS grant proposal PDF (`docsend_156_pages.pdf`, 37 MB, docsend-tracked) is **not** copied into this folder — it lives at `/home/blik/Desktop/chris/docsend_156_pages.pdf`. Reference its content; don't redistribute the binary.

---

## How we got here (process timeline)

| When | What happened | Output |
|---|---|---|
| ~May 1, 2026 | ISC drafts MVP proposal — a single-node DICOMweb demo with QIDO-RS + WADO-RS as the precondition. | `source-materials/ATLAS_ ChRIS Federated Research Platform -- MVP Proposal.txt` |
| ~April 2026 | ISC produces internal opportunity analysis: 9 ranked grant work-packages where ISC could provide consulting/contracting, plus a product play (Retuve marketplace listing). DICOMweb sub-task identified as Tier 2 direct fit. | `source-materials/ATLAS_Grant_Opportunity_Analysis_BCH_Final.docx` |
| 2026-05-19 | Scoping call between BCH (Rudolph + Joshua) and ISC (Alex + Tommy + Adam). Scope of first deliverable agreed: a research/spike ticket on DICOMweb compliance. Estimated "day or two of effort." Security hardening and the indexing-architecture question explicitly deferred. | `source-materials/DICOMweb-QIDO-RS-intial-scoping-16d6d9b4-e0be.md` |
| 2026-05-20 | ISC reads CUBE codebase, dumps live OpenAPI schema, produces `CURRENT_API.md` (API spec + gap analysis), `QIDO_PLAN.md` (phased plan). | `proposal-to-bch/CURRENT_API.md`, `proposal-to-bch/QIDO_PLAN.md`, `proposal-to-bch/schema.yaml`, `proposal-to-bch/schema.split.yaml` |
| 2026-05-20 | ISC ships Phase A (schema + ingest foundation). New `dicomweb` Django app, `PACSInstance` model, six new `PACSSeries` fields, Celery indexing task. 103/103 pacsfiles tests pass; 9/9 new tests pass; zero schema drift. | `proposal-to-bch/code/` + `proposal-to-bch/PHASE_A_IMPLEMENTATION.md` |
| 2026-05-22 | Adam McArthur reviews the package. Two architectural critiques: (1) consider hosting DICOMweb endpoints in oxidicom instead of Django, (2) make Patient/Study/Series/Instance hierarchy explicit in the data model rather than computed via GROUP BY. | `internal-review/REVIEW_RESPONSE_ROUND_1.md` |
| 2026-05-22 | Adam follow-up: approves the response framing but warns to (a) frame BCH-facing materials as research output, not escalation; (b) expect scope-creep asks (security hardening etc.) in the next BCH meeting. | `proposal-to-bch/RESEARCH_TICKET_OUTPUT.md` (BCH-facing summary that bakes in the architectural recommendation rather than escalating it), `internal-review/SCOPE_GUARDRAILS.md` (meeting cheat sheet) |
| **Now** | Awaiting BCH response to the research output; awaiting clarity on grant approval / headcount-budget allocation. | — |

---

## Reading order by purpose

### If you're new to the engagement

1. This README (you're here). 10 minutes.
2. `source-materials/DICOMweb-QIDO-RS-intial-scoping-16d6d9b4-e0be.md` — the May 19 transcript. The single most useful document for understanding the BCH expectation and tone. 15 minutes.
3. `proposal-to-bch/RESEARCH_TICKET_OUTPUT.md` — what we're proposing to BCH. 15 minutes.
4. `internal-review/ISC_DELIVERABLES.md` — the L1/L2/L3/L4 layered scope. Tells you what ISC has committed to vs what's aspirational. 15 minutes.

That's 55 minutes and you're fully oriented.

### If you're presenting the proposal to BCH

1. `internal-review/SCOPE_GUARDRAILS.md` — read this **before** the meeting. Tab order at the bottom tells you what to keep open. Pre-walked redirect language for likely scope-creep asks.
2. `proposal-to-bch/RESEARCH_TICKET_OUTPUT.md` — lead with this. Short enough to actually walk through on a call.
3. `proposal-to-bch/CURRENT_API.md` and `proposal-to-bch/QIDO_PLAN.md` — pull up if BCH wants technical depth.
4. `proposal-to-bch/PHASE_A_IMPLEMENTATION.md` — pull up if BCH asks about validation discipline or wants to see the code.
5. **Don't open** anything from `internal-review/` in front of BCH. That's ISC scaffolding.

### If you're running the internal review of the engineering work

1. `proposal-to-bch/PHASE_A_IMPLEMENTATION.md` — what was built, what's validated, what's known risk.
2. `proposal-to-bch/code/phase-a.patch` — the actual diff (548 lines).
3. `proposal-to-bch/QIDO_PLAN.md` — the broader plan Phase A sits inside.
4. `internal-review/REVIEW_RESPONSE_ROUND_1.md` — Adam's first-round comments and ISC's response, including reopened architectural decisions.

### If you're sizing follow-on work / commercial development

1. `internal-review/ISC_DELIVERABLES.md` §L4 — the broader pipeline of ranked grant opportunities.
2. `source-materials/ATLAS_Grant_Opportunity_Analysis_BCH_Final.docx` — full opportunity analysis with the 9-row ranking.
3. The grant PDF (`/home/blik/Desktop/chris/docsend_156_pages.pdf`) — for budget lines and FTE allocations per sub-task.

---

## State of the work (snapshot, 2026-05-22)

| Item | State | Notes |
|---|---|---|
| L1 research deliverable | **Done** | Packaged in `proposal-to-bch/`. Exceeds the "day or two" Alex sized at May 19. |
| Phase A implementation | **Done + validated** | 103/103 existing tests pass; 9/9 new tests pass; `makemigrations --dry-run` shows no drift; Django system check clean. |
| Architecture: where do endpoints live (Django vs oxidicom vs hybrid) | **Reopened** | Was implicitly "Django" in `QIDO_PLAN.md` v1; review surfaced strong reasons to favour hybrid (oxidicom emits parsed tags on NATS, Django serves endpoints). Recommendation baked into `RESEARCH_TICKET_OUTPUT.md` as an option presentation. |
| Hierarchy model: GROUP BY vs explicit `PACSStudy` | **Reopened** | Was "GROUP BY for MVP" in `QIDO_PLAN.md` v1; review surfaced grant-scale concerns. Recommendation now: add `PACSStudy` model explicitly; keep Patient implicit unless a concrete query demands it. |
| STOW-RS in MVP scope | **Open scope mismatch** | MVP proposal said QIDO+WADO only; grant §2.6.1.6 says all three by Month 12. Phase A model supports all three; decision affects Phase C view layer sizing. |
| Phase B (renderer + query parser + `PACSStudy` + `pg_trgm`) | **Not started** | Architecture-independent — same shape under any of the variants above. Can begin once BCH closes out the open items. |
| Phase C (view layer) | **Not started** | Shape depends on architecture choice. **Don't start** before architecture is decided. |
| Phase D (integration tests + backfill management command) | **Not started** | |
| Phase E (polish) | **Not started** | |
| Funding | **Pending** | Grant approval not yet given as of May 19. BCH internal headcount budget potentially available pre-approval. No written agreement in place. |
| BCH comms channel | **Not yet established** | Joshua mentioned a matrix server + daily standups. ISC not yet invited. Kickoff call mentioned but not scheduled. |

---

## Open items requiring BCH input

These are the things the next BCH conversation needs to close. All three are folded into `RESEARCH_TICKET_OUTPUT.md` as recommendations + the one factual question, **not** as a separate "please decide" block — that framing was Adam's specific advice.

1. **Architecture**: A (Django views), B (oxidicom-hosted), or C (hybrid — oxidicom emits parsed tags on NATS, a small consumer indexes them, Django serves the API). ISC recommendation: **C**, falling back to **B** if oxidicom is confirmed as the only intended ingestion path.
2. **Hierarchy model**: GROUP BY rollups vs explicit `PACSStudy`. ISC recommendation: add `PACSStudy` explicitly; keep Patient implicit.
3. **Ingestion ownership** (factual): going forward, is oxidicom the only DICOM ingestion path into CUBE, or are other routes planned (STOW-RS, S3 bulk import, plugin outputs into the PACS tree, etc.)? The answer affects #1.

Plus:

4. **STOW-RS scope**: MVP proposal said no; grant says yes. Reconcile before Phase C is sized.
5. **MOC instance status**: is there a ChRIS instance already running on MOC, or does one need to be stood up? What's the lead time?
6. **Pre-approval funding**: confirmation (in writing, even informally) that BCH internal headcount budget can fund the L2 work before grant approval.
7. **Comms channel**: invite ISC to the matrix server; schedule the kickoff call.

---

## Open items the ISC team should discuss internally

Independent of BCH input, these are things ISC should align on before the next BCH meeting:

- **Who's the engineer of record** for the L2 (MVP implementation) work? Adam flagged this at the May 19 meeting — Alex and Tommy were to "powwow" after the call and identify the person. Status unclear from the documentation in this folder; resolve before BCH conversation.
- **Stance on scope-creep asks.** `SCOPE_GUARDRAILS.md` has pre-walked language for 7 likely asks. Confirm everyone presenting at the next BCH meeting agrees with the redirect language and the boundary positions.
- **Codex / AI-driven iteration angle** (Adam's suggestion). If we go that route for Phase B/C/D, decide who owns the prompt/test-harness work and how it's billed. Adam suggested it; not yet adopted as a methodology.
- **Whether to share `internal-review/REVIEW_RESPONSE_ROUND_1.md` with Adam directly** as a record, or to keep it as just-Marty's-notes. Adam already approved the response; his approval is in chat, not in this doc.

---

## A note on the source materials

`source-materials/` contains the documents this work was anchored to. Treat them as **inputs**, not as deliverables:

- **MVP proposal** (.txt) — ISC-authored framing of a single-node demo. Predates the grant proposal and is more conservative on scope (QIDO+WADO, not STOW).
- **Scoping meeting transcript** (.md) — the most directly load-bearing document for understanding BCH's expectation. If a question of "what did BCH ask for?" comes up, the answer is in this transcript.
- **Opportunity analysis** (.docx) — ISC-internal commercial doc. Reads ISC's positioning across all of TA2/TA3 of the grant. Useful for follow-on conversations; not a BCH-facing document.
- **notes.txt** — pointers to the QIDO-RS / dcm4chee / MOC reference URLs that the engineer used during the spike.

The full ATLAS grant proposal PDF is at `/home/blik/Desktop/chris/docsend_156_pages.pdf` — referenced from here rather than copied due to size (37 MB) and the docsend tracking on it. Pages most relevant to this engagement: §2.6.1.6 (DICOMweb) on doc page 78; §2.6.1 (Airflow + ChRIS parent task) on doc page 75; §2.7.1 (At-rest format for radiology DICOM) on doc page 82.

---

## Canonical copy

This repository is the **canonical, single source of truth** for the engagement. It supersedes the older `/home/blik/Desktop/chris/deliverables/` bundle, which contained only the BCH-facing subset (`proposal-to-bch/` equivalents) without the source materials or internal-review scaffolding. If that older folder still exists and drifts from this repo, **this repo wins** — it has the framing Adam asked for (research output, not escalation) baked into `proposal-to-bch/RESEARCH_TICKET_OUTPUT.md` and the scope guardrails next to it.

---

## What to do next

Concrete near-term moves, in priority order:

1. **Internal alignment call.** 30 minutes between Alex, Tommy, Marty, and Adam (and whoever else from ISC). Walk through `RESEARCH_TICKET_OUTPUT.md` and `SCOPE_GUARDRAILS.md`. Confirm the architectural recommendation (C with fallback to B) and the hierarchy recommendation (`PACSStudy` explicit) are positions everyone is willing to defend in front of BCH.
2. **Send the BCH-facing package.** Email or Slack to Rudolph + Joshua with `RESEARCH_TICKET_OUTPUT.md` as the body (or as an attachment), and the supporting docs as a zip / shared folder. The other research docs (`CURRENT_API.md`, `QIDO_PLAN.md`, `PHASE_A_IMPLEMENTATION.md`) go along but `RESEARCH_TICKET_OUTPUT.md` is the lead.
3. **Schedule the BCH follow-up call.** Reference Joshua's "discuss it, break it up into tickets" workflow. Frame as a working session, not a decision meeting.
4. **Hold Phase B** until BCH closes out the open architectural items. The `pg_trgm` migration and the `PACSStudy` model are architecture-independent and could begin sooner, but the value is low until BCH confirms direction.
