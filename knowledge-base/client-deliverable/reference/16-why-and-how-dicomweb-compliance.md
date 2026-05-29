# Why & How CUBE's API Must Become DICOMweb-Compliant

> The "why modernize" thesis in one place: what **DICOM** is and why it's painful to integrate, what
> **DICOMweb** changes, where **ChRIS/CUBE** stands today, and therefore **why** the API needs to be made
> compliant and **how** we'd do it. Companion to `04-dicom-standard.md` (DICOM), `05-dicomweb-…md` (the
> web services), `09-cube-rest-api.md` (CUBE's current API), and `12-l2-dicomweb-implementation.md` (the
> build). Written to defend the case to a stakeholder.

---

## 1. DICOM — the standard, and why it's hard to integrate

**What it is.** DICOM (Digital Imaging and Communications in Medicine) is *the* universal standard for
medical imaging — storage *and* network transfer. Every scanner, PACS, and viewer speaks it (Siemens,
Philips, GE, every hospital, public or private). Its data model is a four-level hierarchy —
**Patient → Study → Series → Instance** — with typed **Data Elements** (tag `(group,element)`, a VR, a
value) and globally-unique **UIDs** at each level (see `04-dicom-standard.md`). You cannot avoid DICOM;
it is the lingua franca of the field.

**Why it's painful.** DICOM predates the web (ACR-NEMA **1985**; DICOM 3.0 added TCP/IP in 1993 — older
than HTTP). Its classic network layer, **DIMSE**, is a stateful, port-based, **AE-title-negotiated**
protocol, not HTTP. The integration pain points:

- **Reversed client-server (the C-MOVE quirk).** To *retrieve* an image with DIMSE C-MOVE, the client
  doesn't pull it — it asks the server to **push** it to a pre-registered Application Entity (AET) on a
  known host/port. Both ends must be configured in advance, firewalls must allow the inbound
  association, and it breaks across network/cloud boundaries. (`04-`, `05-`.)
- **No native JSON, no native web clients.** DIMSE isn't HTTP — you can't point a browser, a REST
  gateway, or a cloud service at it. Integrating with modern auth, proxies, and tooling is bespoke work.
- **Configuration burden.** AE titles, port maps, transfer-syntax negotiation per peer.

That's why it's often called a "legacy protocol" — ubiquitous and load-bearing, but awkward for the
web/cloud world research and modern clinics live in.

## 2. DICOMweb — the modernization (same data, modern transport)

DICOMweb is the **DICOM Standard's own RESTful HTTP face** (PS3.18). It keeps the DICOM data model but
moves it onto HTTP (`05-dicomweb-qido-wado-stow.md`):

| Service | Verb | Replaces (DIMSE) | Does |
|---|---|---|---|
| **QIDO-RS** | `GET` | C-FIND | **Query** for studies/series/instances → `application/dicom+json` |
| **WADO-RS** | `GET` | C-MOVE / C-GET | **Retrieve** objects/metadata/frames (`multipart/related`, `dicom+json`) |
| **STOW-RS** | `POST` | C-STORE | **Store** new objects (`multipart/related; type=application/dicom`) |

**Why it fixes the pain:**

- **HTTP pull, not push.** WADO-RS is a plain `GET` — firewall-friendly, no AET dance, works through
  proxies and across cloud boundaries. The "reversed client-server" problem disappears.
- **Standard web clients.** Zero-footprint viewers like **OHIF** speak DICOMweb natively → browser-based
  viewing, no fat-client install, no per-site AE config.
- **JSON + REST.** The **DICOM JSON Model** (tag-keyed `{ "vr": …, "Value": [...] }`) is ordinary JSON;
  the endpoints behave like any web API — easy to put behind auth, gateways, and cloud infra.

Net: DICOMweb is DICOM's bridge to the modern web **without abandoning the data model** every device
already produces.

## 3. ChRIS / CUBE today — where the gap is

CUBE (`ChRIS_ultron_backEnd`) is the platform backend (`01-`, `09-`, `10-`): a Django app exposing a
REST API in **collection+json** (its own hypermedia dialect), with **oxidicom** ingesting DICOM via
C-STORE and the **`pacsfiles`** app storing it in object storage.

The gap is precise: **CUBE's API is collection+json, not DICOMweb.** Concretely (`09-`, `12-`,
`CURRENT_API.md`):

- A clinician's **DICOMweb viewer (OHIF)** or a **hospital PACS** literally **cannot talk to CUBE** —
  they speak QIDO/WADO/STOW; CUBE speaks collection+json.
- Retrieval today rides the legacy **pfdcm / C-MOVE** path — the very thing DICOMweb modernizes (and
  `GET /api/v1/pacs/` even 500s when pfdcm is unreachable).
- There is **no instance/study index, no DICOM-JSON renderer, no QIDO query semantics, and no
  WADO/STOW surface.** The tags are there on `PACSSeries`; the *web-standard way to ask for and serve
  them* is not.

## 4. WHY the API must be made compliant

1. **Research → clinic interoperability (the thesis).** Research outputs only become useful to clinicians
   if they show up in the tools clinicians already use — and those tools (OHIF, PACS) speak **DICOMweb**.
   Without compliance, ChRIS is an island that requires custom integration per site. (`15-`.)
2. **It's a contractual grant deliverable.** ATLAS **§2.6.1.6** requires **WADO-RS + STOW-RS + QIDO-RS
   operational (Month 12)** plus a regression suite (Month 15). Compliance isn't optional for the grant.
   (`15-`.)
3. **Federation.** A *federated* research platform exchanges imaging **across sites over standard
   DICOMweb**, not a bespoke per-institution API. DICOMweb is the interop substrate.
4. **Modernization of the legacy path.** Replace the brittle, firewall-hostile C-MOVE/pfdcm pull with a
   plain HTTP `GET` that any standard client can drive.

## 5. HOW we'd make it compliant (additive, not a rewrite — and proven)

The spike's finding: this is **additive on top of what CUBE already has** (oxidicom ingest + `pacsfiles`
storage + the existing auth), not a rewrite. Five pieces (`12-l2-dicomweb-implementation.md`):

1. an **instance index** (`PACSInstance`) + an **explicit `PACSStudy`**;
2. a **DICOM-JSON renderer** (`application/dicom+json`);
3. a **QIDO query parser** (tag/keyword matching, ranges, wildcards, fuzzy, `includefield`, paging);
4. the **WADO-RS retrieve surface** (`multipart/related; application/dicom`, metadata, native frames);
5. **STOW-RS** upload.

It **reuses CUBE's auth verbatim** and is fed by the existing ingest. Indexing can either **re-read**
files (simple) or — the efficient path — **consume the tags oxidicom already parsed** (variant C, no
re-read). The whole surface was **built and proven end-to-end against a real CUBE**: 97/97 tests, live
QIDO/WADO/STOW over HTTP, real-time auto-indexing, fuzzy via `pg_trgm` (`14-testing-and-validation.md`).

## TL;DR for the room

DICOM is universal but pre-web (C-MOVE makes the server push to you). **DICOMweb is DICOM-over-HTTP**
(QIDO/WADO/STOW) — a normal pull that OHIF and PACS speak. **CUBE today speaks collection+json, not
DICOMweb**, so standard clinical tools can't consume ChRIS imaging. Making the API compliant is what lets
**research outputs flow into clinicians' existing viewers** — it's required by the grant, needed for
federation, and (we've shown) an **additive, proven** change on top of CUBE's existing ingest + storage.
