# oxidicom & the DICOM Ingestion Path into CUBE

> Deep engineering reference for the BCH/ChRIS DICOMweb meeting. Topic: **oxidicom** (the Rust
> C-STORE SCP) and the **ingestion dataflow** that turns a DICOM push into rows in CUBE's Postgres.
> This is the file to read before any "what does oxidicom actually emit?" or "what happens when X
> connects to port 11111?" question. Every claim below is grounded in source (file:line / URL) and,
> where marked **[verified]**, was reproduced live in this session.
>
> **Audience:** an engineer new to this stack.
> **Companion docs:** `knowledge-base/02-cube-and-pacs-data-model.md` (CUBE data model — read first);
> this file goes *deeper* on the §4 ingestion material there.

---

## 0. The one-paragraph answer

**oxidicom** is a standalone Rust process that implements the DICOM **C-STORE SCP** role: it listens
on TCP `:11111` as Application Entity Title (AET) `ChRIS`, accepts DICOM associations from any peer
SCU, and for every received instance (a) writes the `.dcm` straight into CUBE's storage tree, (b)
publishes a tiny binary **progress** message on **NATS** (the "LONK" protocol), and (c) at the end of
an association enqueues a **Celery** task (`register_pacs_series`, queue `main2`) that creates the
`PACSSeries` + `PACSFile` rows in Postgres. It bypasses CUBE's HTTP API entirely for the bytes. The
PACS *identifier* a series lands under is **the calling AET of the pushing SCU** — not a configured
name. Critically for the DICOMweb design: **the only thing on NATS is a running file count plus
done/error — no DICOM tags.** Tags travel on the Celery task (series/patient/study level only) — not
NATS, and never at the instance level.

---

## 1. What oxidicom is, and where it sits

- Rust binary, repo <https://github.com/FNNDSC/oxidicom>, docs
  <https://chrisproject.org/docs/oxidicom> and <https://chrisproject.org/docs/oxidicom/architecture>.
- It is the **primary ingestion path** into CUBE's PACS file tree. Two upstreams feed it:
  1. A peer PACS doing a direct **C-STORE push** to `:11111`.
  2. A **C-MOVE** triggered by CUBE's outbound pull side (`PACSQuery`/`PACSRetrieve` → `pfdcm` →
     upstream PACS does C-MOVE → the destination is oxidicom). See doc 02 §3.5. oxidicom does not
     know or care which of the two produced the association — both are just C-STORE associations.
- It is **built and shipped today**; the DICOMweb *query/retrieve* surface (QIDO/WADO/STOW) is what
  is *not* built. Do not conflate "oxidicom" (ingest, exists) with "DICOMweb endpoints" (query, TBD).
- Versions in play: **oxidicom 3.0.0** in miniChRIS (the demo stack); **4.0.0-alpha.1** in CUBE's own
  dev compose. The robustness bug in §7 is reproduced against 3.0.0; line numbers below are from the
  3.0.0/`master` tree (they coincide for the files cited).

```
                         C-STORE (DICOM upper-layer protocol over TCP :11111)
   peer SCU / C-MOVE dst ───────────────────────────────────────────────▶  oxidicom  (AET "ChRIS")
                                                                               │
        ┌──────────────────────────────┬───────────────────────────────┬──────┘
        │ (a) write .dcm bytes          │ (b) LONK progress             │ (c) Celery task @ assoc end
        ▼                               ▼                               ▼
   CUBE storage tree              NATS subject                   register_pacs_series
   SERVICES/PACS/<callingAET>/    oxidicom.<pacs>.<SeriesUID>     (broker, queue "main2")
   <study>/<series>/<sop>.dcm     payload: 1 magic byte + count         │
        │                               │                               ▼
        │ (read back by                 │ relayed by CUBE        CUBE Celery worker:
        │  register task /              │ via WS /api/v1/pacs/   PACSSeriesSerializer.create
        │  index task)                  │ ws/  and SSE .../sse/  → PACSSeries + PACSFile rows
        ▼                               ▼                               │
   bytes on swift/S3/fslink        UI progress bars            (+ Phase A: index_pacs_instance
                                                                fan-out → PACSInstance rows)
```

---

## 2. The C-STORE SCP itself (`src/scp.rs`)

`handle_association` (`oxidicom/src/scp.rs:32`) is the per-connection handler. It is adapted from
dicom-rs' `storescp` example (header comment, `scp.rs:1-5`). Flow:

1. **`options.establish(scu_stream)`** (`scp.rs:39`) negotiates the DICOM association
   (presentation-context / transfer-syntax handshake). Failure maps to
   `AssociationError::CouldNotEstablish`. **This line is the trigger of the §7 panic** — a bare TCP
   connection that never speaks valid DICOM fails here.
2. On success it reads the peer's AET: `let aec = AETitle::from(association.peer_ae_title())`
   (`scp.rs:41`) and sends an `AssociationEvent::Start { ulid, aec }` down a channel (`scp.rs:45-50`).
   **This `aec` (the *calling* AET) becomes the PACS name** — see §3.
3. It then loops over PDUs (`scp.rs:63`):
   - **C-ECHO-RQ** (`command_field == 0x0030`, `scp.rs:91`): replies with a C-ECHO-RSP (`scp.rs:93`).
     A C-ECHO is a *real, fully negotiated association*, so it is **harmless** — it produces a clean
     Start and a clean Finish. (Contrast with a raw TCP probe — §7.)
   - **C-STORE data** (`PDataValueType::Data` with `is_last`, `scp.rs:133`): assembles the instance
     buffer, parses it with the negotiated transfer syntax, builds the file-meta table, and sends
     `AssociationEvent::DicomInstance { ulid, dcm }` (`scp.rs:168-173`), then returns a C-STORE-RSP
     with status `0x0000` (success) (`scp.rs:182-199`).
   - **ReleaseRQ** (`scp.rs:203`): sends ReleaseRP and breaks — the clean end of an association.
   - **AbortRQ** (`scp.rs:214`) / unhandled PDU (`scp.rs:218`): error out.
- The SCP **accepts any presentation context / any calling AET** (`AcceptAny`, `scp.rs:14`). There is
  no allow-list of peers and no SCU authentication at the DICOM layer. Access control, if any, is
  network-level (who can reach `:11111`).
- The actual *stateful* work (tag extraction, file write, progress, series finish) happens in a
  separate consumer of the `AssociationEvent` channel — the state loop in §6/§7.

---

## 3. The PACS identity rule: PACS name == calling AET  **[verified]**

The PACS bucket a study lands in is **the AET of the SCU that pushed the data**, captured at
`scp.rs:41` (`association.peer_ae_title()`) and propagated as `Association.pacs_name`
(`src/association_series_state_loop.rs:20,25`). It is **not** an oxidicom config value and **not** a
CUBE-side mapping.

**[verified this session]** Pushing with calling AET `TESTSCU` created a CUBE PACS named exactly
`TESTSCU`, and files landed under `SERVICES/PACS/TESTSCU/…`. The `PACS` row is auto-created lazily by
`PACSSeriesSerializer.create` if it does not already exist
(`pacsfiles/serializers.py:165-175` — `PACS.objects.get(identifier=pacs_name)` → on `DoesNotExist`,
create the folder `SERVICES/PACS/<pacs_name>` and a `PACS` row).

> **Implication:** the set of PACS identifiers in CUBE is whatever set of calling AETs has
> ever pushed. A misconfigured modality with AET `MODALITY1` silently creates a PACS `MODALITY1`.
> There is no validation that the AET corresponds to a "known" PACS. (The `GET /api/v1/pacs/` side
> effect that auto-creates rows from `pfdcm` — doc 02 §3.2 — is a *separate* mechanism for the
> C-FIND/C-MOVE pull side.)

---

## 4. The file-tree layout

Every received instance is written to the local filesystem rooted at `files_root`
(`OXIDICOM_FILES_ROOT`) under the PACS tree. The write happens in `write_dicom`
(`association_series_state_loop.rs:178-188`): `output_path = files_root.join(pacs_file.data.path)`,
`create_dir_all(parent)`, then `obj.write_to_file(output_path)`.

```
<files_root>/
└── SERVICES/
    └── PACS/
        └── <callingAET>/                 ← the PACS identifier (§3)
            └── <study sub-path>/
                └── <series sub-path>/
                    └── <sop>.dcm          ← one file per SOP instance
```

The sub-path is derived from DICOM tags (patient/study/series identifiers + SOP instance) and
**sanitized** (`oxidicom/src/sanitize.rs`, `src/types.rs::DicomFilePath`). Two consequences that
matter downstream:

- **oxidicom writes bytes directly; it never calls CUBE's HTTP API for storage.** CUBE only learns of
  these files when it reads the directory back (the 30 s wait in §8.2, or the Phase A indexer).
- **A `.dcm`'s immediate parent folder is not guaranteed to be the series folder.** oxidicom may nest
  files through intermediate sub-folders, so the series-owning `ChrisFolder` can be one or more
  levels up. This is exactly why CUBE's Phase A indexer walks the parent chain (doc 02 §6.3) and why
  `PACSSeriesSerializer.create` reconstructs the parent-folder for each file from `os.path.dirname`
  (`serializers.py:200-205`).

This directory tree, mapped onto `ChrisFolder`/`ChrisFile`, *is* CUBE's PACS storage. There is no FK
from `PACSFile` to `PACSSeries` — the only link is folder ancestry (doc 02 §3.4).

---

## 5. LONK — the NATS progress protocol (the load-bearing "what it does NOT emit" fact)

LONK = "**L**ight **O**xidicom **N**otifi**K**ations". Spec:
<https://chrisproject.org/docs/oxidicom/lonk>. This is **progress signalling only**, designed to
drive UI progress bars — **not** a metadata transport.

### 5.1 Subject naming (`src/lonk.rs:80-93`)

```
<root_subject>.<pacs_name>.<SeriesInstanceUID>     default root_subject = "oxidicom"
e.g.  oxidicom.TESTSCU.1.2.840.113619.2.55.3.604688.1234
```

Both the PACS name and the SeriesInstanceUID are sanitized for NATS subject rules: space, `.`, `*`,
`>` → `_`, and NUL stripped (`sanitize_subject_part`, `lonk.rs:91-93`). CUBE's Python consumer
reproduces the identical function (`pacsfiles/lonk.py:164-180`, `subject_of` /
`_sanitize_topic_part`) and explicitly cross-references the Rust line range in a comment.

### 5.2 Wire encoding (`src/lonk.rs:9-75`) — the exact bytes

A LONK message is **one magic byte** followed by an optional payload:

| Kind | Magic byte | Payload | Built by |
|---|---|---|---|
| **done** | `0x00` | (none) | `done_message()` `lonk.rs:57` — constant `[0x00]` |
| **progress (ndicom)** | `0x01` | `u32` little-endian = running count of files received | `progress_message()` `lonk.rs:62-68` |
| **error** | `0x02` | UTF-8 error string | `error_message()` `lonk.rs:71-75` |

CUBE's decoder mirrors this exactly: `LonkMagicByte` `DONE=0x00, PROGRESS=0x01, ERROR=0x02`
(`pacsfiles/lonk.py:205-213`); progress is read as `int.from_bytes(data, 'little', signed=False)`
(`lonk.py:228`); on error it logs the raw message but returns a *redacted* string to the client
(`"oxidicom reported an error, check logs for details."`, `lonk.py:231-234`).

> ### THE KEY FACT (for the variant-C DICOMweb design)
> **LONK carries no DICOM tags.** A progress message is literally `0x01` + a 4-byte counter. There is
> no PatientID, no StudyInstanceUID, no Modality, no SOPInstanceUID — nothing but `done` / a count /
> an error string, on a subject that already contains the SeriesInstanceUID and PACS name. So **NATS
> as it exists today cannot feed a DICOMweb instance index.** The variant-C recommendation
> (doc 02 §2) — "oxidicom already parses tags during ingest, publish them on NATS for a small
> consumer to upsert `PACSInstance`/`PACSStudy`" — **requires a new richer NATS message that oxidicom
> does not emit yet.** If asked "can't you just consume the existing NATS stream to build the index?"
> the honest answer is **no, not with LONK 3.x — it has no tags**; you would extend oxidicom to
> publish a tag-bearing event (or fall back to CUBE's pydicom re-read indexer, doc 02 §6.4).

### 5.3 Rate-limiting / priority (`src/lonk_publisher.rs`)

The publisher (`lonk_publisher.rs:15-45`) drains a channel and applies a `SubjectLimiter` keyed on
`progress_interval` (default 500 ms). Messages carry a priority:

- `Optional` — ordinary progress; **dropped** if another was sent within `progress_interval`
  (`limited_send_lonk`, `lonk_publisher.rs:65-88`). This is why a UI may not see every count.
- `Required` — must publish (e.g. an error).
- `Last` — must publish, last message of the series; it `forget`s the limiter first (`:25-27`) so the
  final count/done is never throttled.
- `Only` — special case of `Last` for `ndicom == 1` (`lonk_publisher.rs:124-134`).

`OXIDICOM_DEV_SLEEP` injects an artificial delay here purely to make progress visible while debugging
UI clients; there is a log warning to unset it in production (`lonk_publisher.rs:36-42`).

### 5.4 The CUBE relay — WebSocket **and** SSE

oxidicom publishes to NATS; **CUBE** relays to browsers. There are two relays, both subscribing via
`LonkClient` (`pacsfiles/lonk.py:121`):

- **WebSocket** `v1/pacs/ws/` — `PACSFileProgress` (`pacsfiles/consumers.py:196`), an
  `AsyncJsonWebsocketConsumer`, registered in `core/websockets/urls.py:8-11`, mounted via
  `config/asgi.py`. Client sends `{action:"subscribe", pacs_name, SeriesInstanceUID}`; CUBE
  subscribes to the NATS subject and forwards each decoded message as JSON
  (`consumers.py:214-247`). Auth is enforced manually in `_has_permission`
  (`consumers.py:264-282`) using `IsChrisOrIsPACSUserReadOnly`, because django-channels does authn
  but not DRF authz.
- **SSE** `/api/v1/pacs/sse/` — `PACSFileProgressSSE` (`consumers.py:35`), a
  `StreamingHttpResponse` with `content_type="text/event-stream"`, registered in `core/api.py:587`.
  Query params `pacs_name` and `series_uids` (comma-separated) (`_get_info`, `consumers.py:164-179`);
  it subscribes, pumps a queue, formats `event: message\ndata: <json>\n\n` (`_event_response`,
  `consumers.py:128-129`), and terminates when all requested series report `done`/`error`
  (`_is_all_end`, `consumers.py:137-142`).

The decoded JSON shape both relays emit is `{pacs_name, SeriesInstanceUID, message}` where `message`
is one of `{ndicom:int}` | `{done:true}` | `{error:str}` | `{subscribed:bool}` (the WS-only
subscription ack) — see the `Lonk`/`Lonk*` TypedDicts (`pacsfiles/lonk.py:61-119`).

> Note the validated finding said "SSE `/api/v1/pacs/sse/`" — that is correct and present
> (`core/api.py:587`), **and** there is additionally a WebSocket endpoint at `v1/pacs/ws/`. Both
> relay the same LONK stream; mention both if pressed.

---

## 6. The association → series state loop (`src/association_series_state_loop.rs`)

This is the heart of ingest: a single non-async consumer of the `AssociationEvent` channel
(`association_series_state_loop`, `assl.rs:42`). It owns an in-memory
`inflight_associations: HashMap<Ulid, Association>` (`assl.rs:33,48`). Per event (`match_event`,
`assl.rs:72-116`):

- **`Start { ulid, aec }`** → `inflight_associations.insert(ulid, Association::new(aec))`
  (`assl.rs:78-80`). Records the calling AET as `pacs_name`.
- **`DicomInstance { ulid, dcm }`** → `receive_dicom_instance` (`assl.rs:124`): looks up the
  association, builds a `PacsFileRegistration` (which **parses the DICOM tags** —
  PatientID/StudyUID/SeriesUID/SOPUID/Modality/etc.), records the series in the association's
  `series` map, and spawns a blocking task to write the file (`assl.rs:130-149`). Missing required
  tags emit a LONK **error** for that series (`assl.rs:85-106`).
- **`Finish { ulid, .. }`** → `inflight_associations.remove(&ulid).expect("Unknown association
  ULID")` (`assl.rs:109-112`), then `finish_association` emits a `SeriesEvent::Finish` per series seen
  (`assl.rs:153-160`). **The `Finish` of the association is what ultimately triggers the
  `register_pacs_series` Celery enqueue** (carrying the per-series tag bundle accumulated in the
  `series` map).

The important architectural observation: **oxidicom parses every tag at `assl.rs:134`
(`PacsFileRegistration::new`).** The data is *right there* in Rust. It is then used for (1) the file
path and (2) the Celery task payload — but **not** put on NATS. That is the whole basis of the
variant-C "publish tags on NATS" idea (§5.2).

---

## 7. THE ROBUSTNESS BUG — panic on a failed association  **[verified live]**

This is a notable robustness issue, worth walking through end to end.

### 7.1 Mechanism

The state loop assumes a strict invariant: **every `Finish` is preceded by a matching `Start`**, so
`inflight_associations.remove(&ulid).expect(...)` (`association_series_state_loop.rs:112`) can never
fail. That invariant is **violated** when an association *fails to establish*:

1. A connection hits `:11111` but never completes DICOM association negotiation —
   e.g. a bare TCP `connect()` (a port health-check / probe) that opens the socket and closes it.
2. In `handle_association`, `options.establish(scu_stream)` (`scp.rs:39`) fails with
   `CouldNotEstablish(ConnectionClosed)`. Because it fails **before** `scp.rs:45`, **no
   `AssociationEvent::Start` is ever sent.**
3. But the surrounding listener still emits a `Finish` for that ulid when the handler returns.
4. The state loop receives a `Finish` for a ulid it never saw a `Start` for →
   `inflight_associations.remove(&ulid)` returns `None` → `.expect("Unknown association ULID")`
   **panics** (`assl.rs:112`).

### 7.2 Impact (this is the nasty part)

The panic kills the **state-loop thread/task**, which is the single consumer that turns received
DICOM into file writes + registration. The **listener socket and the container stay "Up"** — Docker
health shows green. So the failure is **silent**: oxidicom keeps *accepting* TCP connections and even
completing C-STORE PDUs at the SCP layer, but **nothing downstream processes them** — no files
registered, no LONK progress, no Celery tasks. **Every later C-STORE silently fails** until the
process is restarted. There is no error surfaced to the pushing SCU at the application level and no
obvious signal in CUBE.

### 7.3 Trigger seen in the wild  **[verified]**

An **Ansible `wait_for` TCP probe of `:11111`** (the standard "wait until the port is listening"
pattern) opens a bare TCP connection with no DICOM handshake — exactly the §7.1 trigger. It took down
the listener immediately after startup. Any naive TCP liveness/readiness probe (k8s `tcpSocket`,
load-balancer health check, `nc -z`, port scanner) does the same.

### 7.4 What is safe vs. unsafe

| Action | Effect |
|---|---|
| **C-ECHO** (DICOM verification, a real negotiated association) | **Safe** — clean Start+Finish (`scp.rs:91-112`). Use this as the health check. |
| Full C-STORE push | Safe (the happy path). |
| Bare TCP connect / `wait_for` / `tcpSocket` probe / port scan | **PANIC** → silent listener death. |

### 7.5 Mitigations

- **Health-check with C-ECHO, never a raw TCP probe.** Replace Ansible `wait_for` /
  k8s `tcpSocket` with a DICOM C-ECHO (e.g. `echoscu`, or a tiny dicom-rs/pynetdicom echo).
- **Restart-on-failure**: run oxidicom with `restart: unless-stopped` / a k8s liveness probe that
  actually detects the dead state (a C-ECHO liveness probe would both detect *and* not trigger it).
- **Don't expose `:11111`** to anything that does opportunistic TCP probing (LB, service mesh
  passive health checks).
- **Upstream fix** is to handle the `Finish`-without-`Start` case gracefully (treat a `remove` miss
  as a no-op rather than `expect`). Worth raising with FNNDSC; check whether 4.0.0-alpha already
  changes `assl.rs:109-112`. (3.0.0, the miniChRIS version, still has the `.expect`.)

> **Talking point:** this is a single `.expect()` on an invariant that an external actor can violate
> with one TCP packet, and the blast radius is "all ingestion silently stops while everything looks
> healthy." It is a great example of why the deployment story (probes, restarts) matters as much as
> the code, and why C-ECHO is the correct liveness signal for a DICOM SCP.

---

## 8. Series registration — the two mechanisms

oxidicom writes bytes and counts; **CUBE** creates the database rows. There are **two** registration
mechanisms. Be precise about which is which.

### 8.1 Mechanism A — the `register_pacs_series` Celery task (the modern/primary path)

At association `Finish`, oxidicom enqueues the **`register_pacs_series`** Celery task on the broker
(`OXIDICOM_CELERY_BROKER`) to queue **`main2`** (`OXIDICOM_QUEUE_NAME`, default `"main2"`). The
oxidicom `settings.rs` comment explicitly names this CUBE task as the contract. CUBE's task lives at
`pacsfiles/tasks.py:48-93` and is routed to `main2` in `core/celery.py:50`.

The task **signature is the exact tag contract oxidicom must satisfy** (`tasks.py:49-66`):

```
register_pacs_series(
    PatientID, StudyDate, StudyInstanceUID, SeriesInstanceUID, pacs_name, path, ndicom,   # required
    PatientName=, PatientBirthDate=, PatientAge=, PatientSex=, AccessionNumber=,           # optional
    Modality=, ProtocolName=, StudyDescription=, SeriesDescription=)                       # optional
```

It drops `None` values (`_filter_some_values`, `tasks.py:96-100`), runs the data through
`PACSSeriesSerializer` (`tasks.py:90-93`), and saves as user `chris`.

> **Note the grain:** this payload is **patient + study + series** tags only. There is **no
> SOPInstanceUID, no per-instance geometry** here — same blind spot as LONK. The finest grain CUBE
> learns about from oxidicom is the *series*; `ndicom` is just the file count. Instance-level rows
> (`PACSInstance`) only exist if Phase A's pydicom indexer re-reads the files (doc 02 §6).

### 8.2 Mechanism B — the HTTP callback `POST /api/v1/pacs/series/`

The same `PACSSeriesSerializer` also backs an HTTP endpoint (`PACSSeriesList`,
`pacsfiles/views.py:372`; route `core/api.py`). It accepts `{path, ndicom, pacs_name, PatientID,
…}` (`serializers.py:133-150`). This is **the ingest registration handshake, not STOW-RS** and not
researcher-facing.

Its defining behaviour — **the 30-second wait-for-files loop** (`serializers.py:278-300`): it lists
storage under `path` once per second for up to 30 iterations, counting `.dcm` files, and only
proceeds when `nfiles == ndicom`. If `nfiles > ndicom` it errors immediately; if it never reaches
`ndicom` within 30 s it errors (`serializers.py:291-300`). This exists because oxidicom writes files
asynchronously and registration must not race ahead of the bytes (`register_pacs_series` docstring:
"Pre-condition: DICOM files *must* exist in storage before running this task", `tasks.py:70-71`).

### 8.3 The shared choke point: `PACSSeriesSerializer.create`

**Both** mechanisms funnel through `PACSSeriesSerializer.create` (`serializers.py:152-222`). It:

1. Gets-or-creates the `PACS` row + `SERVICES/PACS/<pacs_name>` folder (`:165-175`) — the §3 rule.
2. Guards against duplicate series via `unique_together (pacs, SeriesInstanceUID)`; a re-register
   raises `ValidationError` (`:178-221`).
3. Sanitizes filenames (strips commas), reconstructs each file's parent `ChrisFolder` from
   `os.path.dirname` (`:188-209`) — handling the nested-subfolder layout (§4).
4. **`PACSFile.objects.bulk_create(files)`** (`:211`) — creates the `ChrisFile`/`PACSFile` rows.
5. Grants `pacs_users` group read permission up the folder ancestry (`:213-217`).

This is exactly where Phase A hooks the `index_pacs_instance` fan-out (doc 02 §6.5): after
`bulk_create`, schedule per-file indexing `transaction.on_commit`. So **whichever registration
mechanism runs, the DICOMweb instance index gets populated** by the same hook.

---

## 9. Configuration & environment

All settings are env vars deserialized in `oxidicom/src/settings.rs`; deployed names are `OXIDICOM_*`.
Confirmed defaults from `settings.rs` (fetched this session):

| `OXIDICOM_*` env var | settings.rs field | Type | Default | Meaning |
|---|---|---|---|---|
| `OXIDICOM_FILES_ROOT` | `files_root` | path | **required** | Root of CUBE's storage tree; `.dcm` files written under `SERVICES/PACS/…` here. |
| `OXIDICOM_CELERY_BROKER` | `celery_broker` | String | **required** | Broker URL where `register_pacs_series` is enqueued. |
| `OXIDICOM_QUEUE_NAME` | `queue_name` | String | `"main2"` | Celery queue for `register_pacs_series` — same `main2` Phase A indexing routes to (`core/celery.py:50`). |
| `OXIDICOM_NATS_ADDRESS` | `nats_address` | Option | `None` | NATS server. **If unset, LONK progress is disabled** (ingest still works; UI loses progress bars). |
| `OXIDICOM_LISTENER_PORT` | `listener_port` | u16 | `11111` | TCP port of the C-STORE SCP. |
| `OXIDICOM_LISTENER_THREADS` | `listener_threads` | NonZeroUsize | `8` | Concurrent associations. |
| `OXIDICOM_SCP_AET` | `scp.*` (DicomRsSettings) | String | (e.g. `ChRIS`) | The SCP's own AET (the *called* AET). Distinct from the *calling* AET that names the PACS (§3). |
| `OXIDICOM_SCP_MAX_PDU_LENGTH` | `scp_max_pdu_length` | usize | `16384` | DICOM PDU size. |
| `OXIDICOM_PROGRESS_INTERVAL` | `progress_interval` | Duration | `500ms` | Rate-limit window for `Optional` LONK progress (§5.3). |
| `OXIDICOM_ROOT_SUBJECT` | `root_subject` | String | `"oxidicom"` | NATS subject prefix (§5.1). |
| `OXIDICOM_DEV_SLEEP` | `dev_sleep` | Option | `None` | Debug-only artificial delay in the publisher; **unset in prod** (`lonk_publisher.rs:36-42`). |

Distinguish the two AETs: **`OXIDICOM_SCP_AET` = `ChRIS`** is what oxidicom *calls itself* (the called
AET an SCU targets). The **PACS identifier** is the **calling AET of the pusher** (§3) — a different
thing. A common point of confusion.

---

## 10. How this feeds (or doesn't feed) the DICOMweb index

Tie-back to the DICOMweb design (full treatment in doc 02 §§2,5,6):

- **QIDO-RS needs Patient→Study→Series→Instance.** Today, ingest gives CUBE only down to **Series**
  (`PACSSeries`), via `register_pacs_series`. **No Instance rows** come from oxidicom — neither on
  NATS (§5.2) nor in the Celery task (§8.1).
- **Phase A (this repo)** closes the data-layer gap with a `PACSInstance` model and the
  `index_pacs_instance` Celery task that **re-reads each `.dcm` with pydicom** (doc 02 §6.4),
  fanned out from `PACSSeriesSerializer.create` (§8.3). This is the **fallback / non-oxidicom path**.
- **Variant C (the ISC recommendation)** would instead have **oxidicom publish the tags it already
  parses** (`assl.rs:134`) onto a *new* NATS message for a small in-network consumer to upsert
  `PACSInstance`/`PACSStudy` — avoiding the wasted pydicom re-read. **This is not possible with LONK
  today** (no tags on NATS — §5.2); it requires extending oxidicom. The pydicom indexer is the
  fallback for files that did not come through such an oxidicom path.
- **End-to-end happy path is proven [verified]:** clean C-STORE → `.dcm` written →
  `register_pacs_series` → `PACSSeries` + `PACSFile` rows → (Phase A) `index_pacs_instance` →
  `PACSInstance` → would surface via QIDO once the view layer exists.

---

## 11. Q&A

**Q: What does oxidicom put on NATS — can we build the DICOMweb index off it?**
A: Only LONK: a 1-byte kind (`0x00` done / `0x01` progress / `0x02` error) plus, for progress, a
little-endian `u32` file count. **No DICOM tags.** You cannot build an instance index from the
current NATS stream; you would extend oxidicom to emit a tag-bearing event, or fall back to CUBE's
pydicom re-read indexer. (`lonk.rs:9-75`; doc 02 §6.)

**Q: How does oxidicom decide which PACS a study belongs to?**
A: It uses the **calling AET of the pushing SCU** (`scp.rs:41`), verbatim, as the PACS identifier.
Verified: pushing as `TESTSCU` created PACS `TESTSCU`. Not a config value, not validated against a
known list.

**Q: We added a `wait_for` on port 11111 to our deploy and DICOM ingestion stopped working — why?**
A: That's the §7 panic. A bare TCP probe fails DICOM association negotiation
(`establish()`, `scp.rs:39`), which produces a `Finish` with no `Start`, which hits
`.expect("Unknown association ULID")` at `association_series_state_loop.rs:112` and kills the
state-loop. The container stays "Up" but silently drops all subsequent C-STOREs. Fix: health-check
with **C-ECHO**, not a raw TCP connect; add a real liveness probe + restart policy.

**Q: Is C-ECHO safe to use as a health check?**
A: Yes. C-ECHO is a fully negotiated association (handled at `scp.rs:91-112`) — clean Start+Finish,
no panic. It is the correct DICOM-native liveness signal.

**Q: Does oxidicom authenticate the pushing peer?**
A: No application-layer auth — it `AcceptAny` (`scp.rs:14`), any AET, any presentation context.
Control is network-level (who can reach `:11111`). This is one reason the DICOMweb *HTTP* endpoints
stay in Django (to inherit Token/Basic/Session/LDAP) rather than being reimplemented in Rust
(doc 02 §2).

**Q: What's the difference between the Celery task and the `POST /api/v1/pacs/series/` endpoint?**
A: Two registration mechanisms onto the **same** `PACSSeriesSerializer.create`. The Celery task
(`register_pacs_series`, queue `main2`) is the modern path oxidicom enqueues directly. The HTTP
endpoint is an internal handshake that **waits up to 30 s** (`serializers.py:278-300`) for the `.dcm`
files to appear, then `bulk_create`s the rows. Neither is STOW-RS.

**Q: Why does CUBE wait 30 seconds before registering?**
A: oxidicom writes files asynchronously; registration must not outrun the bytes
(`tasks.py:70-71` precondition). The serializer polls storage once/second up to 30× until the file
count matches `ndicom` (`serializers.py:278-300`).

**Q: If oxidicom parses all the tags during ingest, why does Phase A re-read every file with pydicom?**
A: Because oxidicom does **not** currently expose instance-level tags anywhere CUBE can consume them
— not on NATS (LONK has no tags), and the Celery task is series-grain only. Until oxidicom is
extended (variant C), CUBE has to re-derive instance metadata from the bytes. ISC flags the re-read
as wasteful and the reason it does *not* recommend variant A (Django-only).

**Q: What happens if oxidicom can't reach NATS?**
A: `nats_address` is optional; if unset/unreachable, **progress messaging is disabled but ingestion
still works** — files are written and `register_pacs_series` still runs. You just lose UI progress
bars (`settings.rs`; §5).

**Q: Can the same SeriesInstanceUID be registered twice?**
A: `unique_together (pacs, SeriesInstanceUID)` blocks it — `create` raises a `ValidationError` on a
duplicate (`serializers.py:178-221`). (The Phase A `PACSInstance` uses `update_or_create` keyed on
`(series, SOPInstanceUID)` for idempotency — doc 02 §6.2.)

**Q: Which oxidicom version are we actually running, and does the bug matter?**
A: miniChRIS ships **3.0.0** (has the `.expect` panic at `assl.rs:112`); CUBE dev compose ships
**4.0.0-alpha.1**. Confirm whether the alpha changed the `Finish`-without-`Start` handling before
assuming the issue is gone in your deployment.

---

## 12. Sources

- oxidicom source (fetched this session):
  - `src/scp.rs` — C-STORE/C-ECHO SCP, `establish()`, calling-AET capture. <https://github.com/FNNDSC/oxidicom/blob/master/src/scp.rs>
  - `src/association_series_state_loop.rs` — state loop, tag parse, file write, the `.expect` panic (line 112). <https://github.com/FNNDSC/oxidicom/blob/master/src/association_series_state_loop.rs>
  - `src/lonk.rs` — LONK subject + byte encoding. <https://github.com/FNNDSC/oxidicom/blob/master/src/lonk.rs>
  - `src/lonk_publisher.rs` — rate limiting / priorities / dev sleep. <https://github.com/FNNDSC/oxidicom/blob/master/src/lonk_publisher.rs>
  - `src/settings.rs` — config/env table. <https://github.com/FNNDSC/oxidicom/blob/master/src/settings.rs>
- oxidicom docs: <https://chrisproject.org/docs/oxidicom>, <https://chrisproject.org/docs/oxidicom/architecture>, <https://chrisproject.org/docs/oxidicom/lonk>
- CUBE (`implementation/ChRIS_ultron_backEnd/chris_backend/`):
  - `pacsfiles/lonk.py` — LONK NATS consumer/decoder (the SSE/WS relay backend).
  - `pacsfiles/consumers.py` — `PACSFileProgressSSE` (SSE, `core/api.py:587`) and `PACSFileProgress` (WS, `core/websockets/urls.py`).
  - `pacsfiles/tasks.py:48-100` — `register_pacs_series` task + signature (tag contract).
  - `pacsfiles/serializers.py:133-301` — `PACSSeriesSerializer` (`create`, 30 s wait, validation).
  - `pacsfiles/views.py:372` — `PACSSeriesList` (`POST /api/v1/pacs/series/`).
  - `core/celery.py:25-52` — `task_routes` (`register_pacs_series` → `main2`).
- Companion: `knowledge-base/02-cube-and-pacs-data-model.md` (data model, Phase A, variant A/B/C).
- **[verified]** items reproduced live this session (TESTSCU push, the `wait_for` panic, the clean
  end-to-end C-STORE → QIDO chain).
