# DICOMweb: QIDO-RS, WADO-RS, STOW-RS (the core reference)

This is the authoritative reference for the three RESTful web services defined by **DICOM PS3.18** (Web
Services) that, together, are what people mean by "DICOMweb." Adding these to CUBE is the entire ATLAS
deliverable. This file is meant to be correct to the byte: it specifies URL templates, query parameters,
matching semantics, media types, the DICOM JSON Model encoding, and gives copy-pasteable `curl` examples
for each service.

Primary normative source throughout: **DICOM PS3.18, Section 10** (Worklist/Transactions) and **Annex F**
(DICOM JSON Model). https://dicom.nema.org/medical/dicom/current/output/html/part18.html
Concrete real-world examples are cross-checked against three reference implementations: Orthanc
(https://orthanc.uclouvain.be/book/plugins/dicomweb.html), Microsoft Azure DICOM service conformance
statement (https://learn.microsoft.com/en-us/azure/healthcare-apis/dicom/dicom-services-conformance-statement-v2),
and the DICOMweb overview at https://www.dicomstandard.org/using/dicomweb.

---

## 0. The 30-second framing for tomorrow

| Service | Verb | DICOM verb analog | PS3.18 §  | What it does |
|---|---|---|---|---|
| **QIDO-RS** | `GET` | C-FIND | 10.6 | **Q**uery based on **ID** for DICOM **O**bjects. Search the catalog; returns metadata as JSON. |
| **WADO-RS** | `GET` | C-MOVE / C-GET | 10.4 | **W**eb **A**ccess to **D**ICOM **O**bjects by **RES**tful services. Retrieve the actual pixels/objects. |
| **STOW-RS** | `POST` | C-STORE | 10.5 | **STO**re **O**ver the **W**eb. Push DICOM objects into the server. |

Source: https://www.dicomstandard.org/using/dicomweb

All three share one root URL ("the DICOMweb service base", PS3.18 calls it `{s}` or the *Base URL*). In
CUBE the agreed root is per-PACS: `http://<host>/dicom-web/pacs/<pacs_identifier>/` (see `QIDO_PLAN.md`
§2). For brevity below, `{s}` = that base. The resource hierarchy is always
**Study → Series → Instance → Frame**, keyed by their UIDs:

```
{s}/studies/{StudyInstanceUID}
                /series/{SeriesInstanceUID}
                            /instances/{SOPInstanceUID}
                                          /frames/{frameList}
```

`{StudyInstanceUID}` = tag (0020,000D); `{SeriesInstanceUID}` = (0020,000E); `{SOPInstanceUID}` =
(0008,0018). These are dotted DICOM UID strings, e.g. `1.2.840.113619.2.55.3.604688119.971...`.

---

## 1. The DICOM JSON Model (PS3.18 Annex F) — the data format everything returns

QIDO-RS results, WADO-RS metadata, and STOW-RS responses are all serialized as the **DICOM JSON Model**,
media type **`application/dicom+json`**, UTF-8 (`ISO_IR 192`).
Source: PS3.18 Annex F, https://dicom.nema.org/medical/dicom/current/output/chtml/part18/sect_F.2.html

### 1.1 Rules

- A DICOM dataset becomes a **JSON object** whose keys are the attribute's **8-character, uppercase
  hexadecimal tag** — group (4 hex) immediately followed by element (4 hex), **no comma, no `0x`,
  no parentheses**. Example: `StudyInstanceUID` (0020,000D) → key `"0020000D"`.
- Each value is itself an object with a mandatory **`"vr"`** (the two-letter Value Representation) and
  **at most one** of the following (PS3.18 F.2.2 says verbatim "*shall* have ... **at most one** of"):
  - **`"Value"`** — a JSON array of the attribute's values (DICOM is multi-valued).
  - **`"BulkDataURI"`** — a URL where the (large/binary) value can be fetched via WADO-RS (used in
    metadata responses for pixel data, etc.).
  - **`"InlineBinary"`** — base64-encoded binary embedded directly.

  "At most one" — **not** "exactly one": a present-but-empty attribute is encoded with `"vr"` and **none**
  of the three (e.g. `{"vr": "PN"}`), which is the canonical way to express a zero-length value. Source:
  PS3.18 F.2.2, https://dicom.nema.org/medical/dicom/current/output/chtml/part18/sect_F.2.2.html
- A query/retrieve that returns **multiple datasets** returns a **top-level JSON array** of these objects.
  Source: PS3.18 F.2.1, "Multiple results ... organized as a single top-level array of JSON objects."
  https://dicom.nema.org/medical/dicom/current/output/chtml/part18/sect_F.2.html

```json
[
  {
    "00080020": { "vr": "DA", "Value": ["20230102"] },
    "00080060": { "vr": "CS", "Value": ["CT"] },
    "00100010": { "vr": "PN", "Value": [{ "Alphabetic": "DOE^JANE" }] },
    "0020000D": { "vr": "UI", "Value": ["1.2.840.113619.2.55.3.604688119.971.1437406488.926"] },
    "00201206": { "vr": "IS", "Value": [3] },
    "00201208": { "vr": "IS", "Value": [142] }
  }
]
```

### 1.2 VR-specific encoding gotchas (these break clients if you get them wrong)

| VR | JSON encoding | Note |
|---|---|---|
| `PN` (Person Name) | `Value` is an array of **objects**, not strings: `[{"Alphabetic": "DOE^JANE", "Ideographic": "...", "Phonetic": "..."}]` | `Alphabetic`/`Ideographic`/`Phonetic` keys correspond to the three `=`-separated component groups of a DICOM PN. Most data only has `Alphabetic`. The `^` separators **inside** a group (family^given^middle^prefix^suffix) are preserved literally. PS3.18 F.2.2. |
| `IS`, `DS` | May be a JSON **number** or **string**. Best practice: emit `IS` as integer, `DS` as number. | Some clients are strict; pydicom-derived servers commonly emit numbers. |
| `DA`, `TM`, `DT` | DICOM **string** form, not ISO-8601: `"20230102"`, `"143052.000000"`, `"20230102143052"` | Even though your DB stores Python `date`/`time`, serialize back to the DICOM string. |
| `UI` | plain string in `Value` array | UIDs. |
| `SQ` (Sequence) | `Value` is an array of nested **dataset objects** (recursively the same model) | Used in STOW-RS responses (`ReferencedSOPSequence`) and rich metadata. |
| `OB`/`OW`/`OF`/`UN` (bulk binary) | **not** `Value`; use `BulkDataURI` or `InlineBinary` | Pixel data is never inlined in a QIDO result — QIDO is metadata only. |
| Empty attribute | Either omit the key entirely **or** emit `{"vr": "XX", "Value": []}` (or `{"vr":"XX"}`) | Spec permits both. Omitting is smaller and OHIF handles it. |

VRs can be looked up programmatically with `pydicom.datadict.dictionary_VR(tag)`; private/unknown tags
raise `KeyError` — guard and default to `"UN"`. (https://pydicom.github.io/)

---

## 2. QIDO-RS — Query (PS3.18 §10.6)

### 2.1 URL templates

Source: https://www.dicomstandard.org/using/dicomweb/query-qido-rs and PS3.18 Table 10.6.1-1.

| Resource | Template (relative to `{s}`) | Returns |
|---|---|---|
| All Studies | `GET {s}/studies?{query}` | array of Study-level datasets |
| Study's Series | `GET {s}/studies/{StudyInstanceUID}/series?{query}` | array of Series-level datasets |
| Series' Instances | `GET {s}/studies/{StudyInstanceUID}/series/{SeriesInstanceUID}/instances?{query}` | array of Instance-level datasets |
| All Series (cross-study) | `GET {s}/series?{query}` | array of Series-level datasets |
| All Instances (cross-study) | `GET {s}/instances?{query}` | array of Instance-level datasets |
| Study's Instances | `GET {s}/studies/{StudyInstanceUID}/instances?{query}` | array of Instance-level datasets |

All are **`GET`**. There are no detail (single-object) QIDO endpoints — QIDO is always list-shaped. (A
single object is retrieved via WADO-RS, not QIDO.)

### 2.2 Response media types & status

- `Accept: application/dicom+json` — the default and the one OHIF/3D Slicer send. **Required** to support.
- `Accept: application/json` — must be treated as equivalent to `application/dicom+json`. (Azure: "must
  have the value `application/dicom+json`"; the standard also allows `application/json`.)
- `Accept: application/dicom+xml` (Native DICOM Model, PS3.19) — optional; Orthanc supports it, OHIF does
  not require it. Out of scope for CUBE MVP.

Status codes (PS3.18 §10.6.3):

| Code | Meaning |
|---|---|
| `200 OK` | "The search completed successfully, and the results are contained in the payload." (PS3.18 §10.6.3) Body is the JSON array — `[]` if nothing matched. |
| `400 Bad Request` | "There was a problem with the request. For example, the Query Parameter syntax is incorrect." (PS3.18 §10.6.3) — bad tag hex, malformed range. |
| `401 / 403` | Auth / permission (CUBE's DRF chain). |
| `406 Not Acceptable` | `Accept` is a media type the server can't produce. |
| `413 Payload Too Large` | "The search was too broad, and the body of the response should contain a Status Report." (PS3.18 §10.6.3). The `Warning` header field references a Search Status report. |

Note on `204 No Content`: the **current** PS3.18 §10.6.3 table does **not** list `204` for Search — a valid
query that matches nothing returns `200` with an empty array. Some implementations (e.g. Azure DICOM) do
emit `204` for an empty result and for an `offset` past the end. CUBE returns `200` + `[]` uniformly (the
spec-conformant choice; see `QIDO_PLAN.md` §2). Source for the status table: PS3.18 §10.6.3,
https://dicom.nema.org/medical/dicom/current/output/chtml/part18/sect_10.6.3.html ; Azure 204 behavior:
https://learn.microsoft.com/en-us/azure/healthcare-apis/dicom/dicom-services-conformance-statement-v2

### 2.3 Query parameters

Source: https://www.dicomstandard.org/using/dicomweb/query-qido-rs ; PS3.18 §10.6.1.2 and §6.7.1.

| Parameter | Form | Meaning |
|---|---|---|
| `{attributeID}={value}` | `?00100010=DOE^JANE` or `?PatientName=DOE^JANE` | **Matching attribute** (a.k.a. match key). The result set is constrained to objects matching `value`. |
| `includefield={attributeID}` | `?includefield=00081030` or `?includefield=StudyDescription` | Adds an attribute to the response that isn't returned by default. Repeatable / comma-list. |
| `includefield=all` | `?includefield=all` | Return every attribute the server supports at that level. |
| `fuzzymatching=true\|false` | `?fuzzymatching=true` | Enable fuzzy matching on `PN`-VR attributes (see 2.4). Default `false`. |
| `limit={n}` | `?limit=50` | Return at most `n` results. |
| `offset={n}` | `?offset=100` | Skip the first `n` results (pagination). |

**Attribute ID encoding** (PS3.18 §6.7.1.1.1): a tag may be given as the 8-hex form `{group}{element}`
(`0020000D`) **or** as the DICOM keyword (`StudyInstanceUID`). Nested attributes use a dotted path of
hex tags. CUBE supports the two flat forms. Source:
https://learn.microsoft.com/en-us/azure/healthcare-apis/dicom/dicom-services-conformance-statement-v2

### 2.4 Matching semantics (PS3.18 §C.2.2.2, applied to QIDO via §10.6)

| Match type | VRs | Syntax | Behavior |
|---|---|---|---|
| **Single Value** | most | `?00080060=CT` | Exact equality. |
| **List of UID** | `UI` | `?0020000D=1.2.3,4.5.6` (comma or `\` separated) | Match if the attribute equals **any** UID in the list (OR). |
| **Wildcard** | string VRs (`PN`,`LO`,`SH`,`LT`,`ST`,`UT`,`UC`,`UR`,`AE`,`CS`) | `?PatientName=DOE*` | `*` matches 0..N chars; `?` matches exactly 1 char. Maps to SQL `ILIKE` (`*`→`%`, `?`→`_`). |
| **Range** | `DA`,`TM`,`DT` | `?StudyDate=20230101-20231231` | Inclusive range `>= v1 AND <= v2`. Open-ended `20230101-` (on/after) and `-20231231` (on/before) are valid; `-` alone is **invalid**. |
| **Empty (zero-length) value** | any | `?00080060=` | Match objects where the attribute is present but empty. (Many servers, incl. Azure, do **not** support; CUBE MVP rejects with `400`.) |
| **Universal / "return key"** | any | `?00080060` (no `=value`) | No filtering; just requests the attribute be returned (same as `includefield`). |
| **Sequence Matching** | `SQ` | nested | Match within a sequence item. Not needed at the QIDO levels CUBE emits. |
| **Fuzzy** | `PN` | `?PatientName=joh&fuzzymatching=true` | Prefix word-match of any name component. E.g. `John^Doe` matches `joh`, `do`, `jo do`, `Doe`, `John Doe`, but not `ohn`. Source: Azure conformance statement. |

### 2.5 Returned attributes

Each level returns a **default** required attribute set plus anything requested via `includefield`. The
required sets are in PS3.18 Tables (Study §10.6.1.5; Series; Instance). The de-facto interoperable default
sets used by Azure/dcm4chee/OHIF:

**Study level** (PS3.18 + Azure defaults):
`StudyDate (0008,0020)`, `StudyTime (0008,0030)`, `AccessionNumber (0008,0050)`,
`ModalitiesInStudy (0008,0061)`, `ReferringPhysicianName (0008,0090)`, `StudyDescription (0008,1030)`,
`PatientName (0010,0010)`, `PatientID (0010,0020)`, `PatientBirthDate (0010,0030)`, `PatientSex (0010,0040)`,
`StudyInstanceUID (0020,000D)`, `NumberOfStudyRelatedSeries (0020,1206)`,
`NumberOfStudyRelatedInstances (0020,1208)`, and `RetrieveURL (0008,1190)`.

**Series level**: `Modality (0008,0060)`, `SeriesDescription (0008,103E)`,
`ManufacturerModelName (0008,1090)`, `SeriesInstanceUID (0020,000E)`, `SeriesNumber (0020,0011)`,
`NumberOfSeriesRelatedInstances (0020,1209)`, `PerformedProcedureStepStartDate (0040,0244)`,
`RetrieveURL (0008,1190)`.

**Instance level**: `SOPClassUID (0008,0016)`, `SOPInstanceUID (0008,0018)`, `InstanceNumber (0020,0013)`,
`Rows (0028,0010)`, `Columns (0028,0011)`, `BitsAllocated (0028,0100)`, `NumberOfFrames (0028,0008)`,
`RetrieveURL (0008,1190)`.

`ModalitiesInStudy`, `NumberOfStudyRelated*` and `NumberOfSeriesRelatedInstances` are **aggregated/computed**
(GROUP BY across the lower levels — in CUBE that's `ArrayAgg('Modality', distinct=True)` and `Count(...)`,
see `QIDO_PLAN.md` §7.2). `RetrieveURL` is the WADO-RS URL of that object — the QIDO→WADO bridge.

### 2.6 `curl` examples

```bash
# All studies, default attributes (DICOM JSON)
curl -u chris:chris1234 \
  -H 'Accept: application/dicom+json' \
  'http://localhost:8000/dicom-web/pacs/BCH/studies'

# Studies for a patient-name wildcard, return only 5, plus StudyDescription
curl -u chris:chris1234 -H 'Accept: application/dicom+json' \
  'http://localhost:8000/dicom-web/pacs/BCH/studies?PatientName=DOE*&includefield=00081030&limit=5'

# CT or MR series in a date range (tag-hex form, multi-value, range)
curl -u chris:chris1234 -H 'Accept: application/dicom+json' \
  'http://localhost:8000/dicom-web/pacs/BCH/series?00080060=CT,MR&StudyDate=20230101-20231231'

# Drill: series within a study, then instances within a series
curl -u chris:chris1234 -H 'Accept: application/dicom+json' \
  'http://localhost:8000/dicom-web/pacs/BCH/studies/1.2.840.../series'
curl -u chris:chris1234 -H 'Accept: application/dicom+json' \
  'http://localhost:8000/dicom-web/pacs/BCH/studies/1.2.840.../series/1.3.12.../instances'
```

Example response shape (one Study):

```json
[
  {
    "00080020": { "vr": "DA", "Value": ["20230102"] },
    "00080050": { "vr": "SH", "Value": ["A12345"] },
    "00080061": { "vr": "CS", "Value": ["CT", "MR"] },
    "00081030": { "vr": "LO", "Value": ["CHEST CT W/CONTRAST"] },
    "00081190": { "vr": "UR", "Value": ["http://localhost:8000/dicom-web/pacs/BCH/studies/1.2.840..."] },
    "00100010": { "vr": "PN", "Value": [{ "Alphabetic": "DOE^JANE" }] },
    "00100020": { "vr": "LO", "Value": ["MRN0001"] },
    "00100040": { "vr": "CS", "Value": ["F"] },
    "0020000D": { "vr": "UI", "Value": ["1.2.840.113619.2.55.3.604688119.971..."] },
    "00201206": { "vr": "IS", "Value": [3] },
    "00201208": { "vr": "IS", "Value": [142] }
  }
]
```

---

## 3. WADO-RS — Retrieve (PS3.18 §10.4)

WADO-RS retrieves the actual objects: full DICOM instances, just the metadata, individual frames, raw
bulkdata, or a rendered (consumer-format) image. The hierarchy mirrors QIDO.
Source: https://www.dicomstandard.org/using/dicomweb/retrieve-wado-rs-and-wado-uri ; PS3.18 §10.4.

### 3.1 URL templates

| Resource | Template (relative to `{s}`) | Default response media type |
|---|---|---|
| Retrieve Study | `GET {s}/studies/{study}` | `multipart/related; type="application/dicom"` |
| Retrieve Series | `GET {s}/studies/{study}/series/{series}` | `multipart/related; type="application/dicom"` |
| Retrieve Instance | `GET {s}/studies/{study}/series/{series}/instances/{instance}` | `multipart/related; type="application/dicom"` |
| Retrieve **Metadata** (study/series/instance) | `.../{...}/metadata` | `application/dicom+json` |
| Retrieve **Frames** | `.../instances/{instance}/frames/{frameList}` | `multipart/related; type="application/octet-stream"` (or `image/jp2` etc.) |
| Retrieve **Bulkdata** | `{s}/{bulkdataURIReference}` (or `.../bulkdata`) | `multipart/related; type="application/octet-stream"` |
| Retrieve **Rendered** | `.../{study|series|instance}/rendered` | `image/jpeg` (default), `image/png`, `image/gif` |
| Retrieve **Thumbnail** | `.../thumbnail` | `image/jpeg` etc. |

`{frameList}` is a comma-separated, **1-based** list, e.g. `/frames/1,2,3`.

### 3.2 Media types and transfer-syntax negotiation

This is where WADO-RS is fiddly. The client expresses what encoding it wants via the **`Accept`** header,
which carries both a media type and an optional `transfer-syntax=` parameter naming a DICOM Transfer
Syntax UID. Source (concrete, verbatim): Azure conformance statement,
https://learn.microsoft.com/en-us/azure/healthcare-apis/dicom/dicom-services-conformance-statement-v2

Common, interoperable `Accept` values for **instance/series/study** retrieval:

```
multipart/related; type="application/dicom"; transfer-syntax=*
multipart/related; type="application/dicom"                          # no transfer-syntax => default 1.2.840.10008.1.2.1 (Explicit VR Little Endian)
multipart/related; type="application/dicom"; transfer-syntax=1.2.840.10008.1.2.1
multipart/related; type="application/dicom"; transfer-syntax=1.2.840.10008.1.2.4.90   # JPEG 2000 Lossless
*/*                                                                  # => application/dicom, transfer-syntax=*
```

For **single-instance** retrieval, `application/dicom; transfer-syntax=...` (non-multipart) is also valid.
Key transfer-syntax UIDs:
- `1.2.840.10008.1.2` — Implicit VR Little Endian
- `1.2.840.10008.1.2.1` — Explicit VR Little Endian (**the WADO-RS default**)
- `1.2.840.10008.1.2.4.50` — JPEG Baseline (lossy)
- `1.2.840.10008.1.2.4.90` — JPEG 2000 Lossless
- `transfer-syntax=*` — "any / don't transcode; give me the stored encoding"

An **unsupported** `transfer-syntax` produces **`406 Not Acceptable`**. A malformed request (bad UID
format, etc.) produces `400 Bad Request`. Source: Azure conformance statement.

For **bulkdata/frames**, media types include `application/octet-stream` (raw, uncompressed octets) and
pixel-encapsulated forms like `image/jp2`. For **rendered**, `image/jpeg` / `image/png` / `image/gif`.
Source: https://www.dicomstandard.org/using/dicomweb/retrieve-wado-rs-and-wado-uri

### 3.3 The multipart/related wire format

A study/series/instance retrieve returns **one HTTP body** that is a MIME multipart document; each part is
one DICOM object (PS3.10 stream). The boundary is server-chosen and echoed in the response `Content-Type`:

```
HTTP/1.1 200 OK
Content-Type: multipart/related; type="application/dicom"; boundary=abcd1234

--abcd1234
Content-Type: application/dicom
Content-Location: http://localhost:8000/dicom-web/pacs/BCH/studies/1.2.../series/1.3.../instances/1.4...

<...binary DICOM Part-10 stream of instance 1...>
--abcd1234
Content-Type: application/dicom

<...binary DICOM Part-10 stream of instance 2...>
--abcd1234--
```

Orthanc's docs state plainly that a study retrieve "is a multipart stream of `application/dicom` DICOM
instances" that the client must parse rather than render directly
(https://orthanc.uclouvain.be/book/plugins/dicomweb.html). The CUBE WADO-RS implementation streams each
`.dcm` from `core.storage` into a part (see `CURRENT_API.md` — the storage abstraction already streams
binaries via `BinaryFileRenderer`/`FileResponse`).

### 3.4 Status codes (PS3.18 §10.4.3)

| Code | Meaning |
|---|---|
| `200 OK` | All requested content returned. |
| `206 Partial Content` | Some, but not all, of the target's content returned (e.g. some frames). |
| `400 Bad Request` | Malformed request (bad UID, unsupported transfer-syntax format). |
| `404 Not Found` | The target resource (study/series/instance) does not exist. |
| `406 Not Acceptable` | `Accept` media type / transfer-syntax cannot be satisfied. |
| `410 Gone` | The resource existed but was deleted. |

### 3.5 `curl` examples

```bash
# Retrieve a whole study as a multipart/related stream of application/dicom parts
curl -u chris:chris1234 \
  -H 'Accept: multipart/related; type="application/dicom"; transfer-syntax=*' \
  'http://localhost:8000/dicom-web/pacs/BCH/studies/1.2.840...' \
  --output study.multipart

# Retrieve a single instance, default (Explicit VR Little Endian)
curl -u chris:chris1234 \
  -H 'Accept: multipart/related; type="application/dicom"' \
  'http://localhost:8000/dicom-web/pacs/BCH/studies/1.2.../series/1.3.../instances/1.4...' \
  --output instance.multipart

# Retrieve just the metadata (DICOM JSON) for a series — this is what OHIF asks for first
curl -u chris:chris1234 -H 'Accept: application/dicom+json' \
  'http://localhost:8000/dicom-web/pacs/BCH/studies/1.2.../series/1.3.../metadata'

# Retrieve frame 1 of an instance, raw octets
curl -u chris:chris1234 \
  -H 'Accept: multipart/related; type="application/octet-stream"; transfer-syntax=*' \
  'http://localhost:8000/dicom-web/pacs/BCH/studies/1.2.../series/1.3.../instances/1.4.../frames/1' \
  --output frame1.bin

# Rendered (consumer-format) PNG of an instance
curl -u chris:chris1234 -H 'Accept: image/png' \
  'http://localhost:8000/dicom-web/pacs/BCH/studies/1.2.../series/1.3.../instances/1.4.../rendered' \
  --output preview.png
```

> WADO-RS metadata responses set `BulkDataURI` on pixel-data attributes (e.g. `7FE00010 PixelData`)
> pointing at the corresponding `.../bulkdata` or `.../frames/{n}` URL, rather than inlining the pixels.
> This is how OHIF lazily fetches pixels: QIDO catalog → WADO `/metadata` → WADO `/frames`.

---

## 4. STOW-RS — Store (PS3.18 §10.5)

STOW-RS is the push direction: a client `POST`s one or more DICOM objects and the server stores them.
Source: https://www.dicomstandard.org/using/dicomweb/store-stow-rs ; PS3.18 §10.5.

### 4.1 URL templates and method

| Resource | Template | Behavior |
|---|---|---|
| Store to any study | `POST {s}/studies` | Stores a set of instances that **may have different Study Instance UIDs**. |
| Store to one study | `POST {s}/studies/{StudyInstanceUID}` | Stores instances that must all **belong to the given Study**; instances with a different `StudyInstanceUID` are rejected (counts as a failure → 409). |

Source (verbatim): PS3.18 Table 10.5.1-1,
https://dicom.nema.org/medical/dicom/current/output/chtml/part18/sect_10.5.html

### 4.2 Request `Content-Type`

Two payload encodings (PS3.18 §8.7.3, §10.5):

- **`multipart/related; type="application/dicom"; boundary=...`** — the common one. Each part is a
  complete PS3.10 DICOM instance (`Content-Type: application/dicom`). This is what OHIF/dcm4chee/Orthanc
  send. Source: Azure conformance — supported store types are `multipart/related; type="application/dicom"`,
  `application/dicom`.
- **`multipart/related; type="application/dicom+json"`** with bulkdata parts — metadata-as-JSON plus
  separate binary bulkdata parts referenced by `BulkDataURI`. Rarely used; out of CUBE MVP scope.

`Accept` for the **response** should be `application/dicom+json` (the Store Instances Response, §4.4).

### 4.3 Status codes (PS3.18 §10.5.3)

Verbatim from the standard / Azure conformance statement:

| Code | Meaning |
|---|---|
| `200 OK` | "The origin server successfully stored **all** Instances." |
| `202 Accepted` | "The origin server stored **some** of the Instances but warnings or failures exist for others." (Inspect `FailedSOPSequence`.) |
| `400 Bad Request` | "The origin server was unable to store any Instances due to bad syntax." |
| `409 Conflict` | "The request was formed correctly but the origin server was unable to store any Instances due to a conflict in the request (e.g., unsupported SOP Class or Study Instance UID mismatch)." |
| `415 Unsupported Media Type` | "The origin server does not support the Media Type specified in the Content-Type Header Field of the request." |

Source: https://learn.microsoft.com/en-us/azure/healthcare-apis/dicom/dicom-services-conformance-statement-v2
(matching PS3.18 §10.5.3).

### 4.4 The Store Instances Response (PS3.18 §10.5.3.2, "Store Instances Response Module")

The response body is a **single** DICOM JSON Model object (not an array) with these top-level attributes:

| Tag | Keyword | Meaning |
|---|---|---|
| `(0008,1190)` | `RetrieveURL` | WADO-RS URL of the **study**, if a `StudyInstanceUID` was in the request and ≥1 instance stored. |
| `(0008,1198)` | `FailedSOPSequence` | Sequence of instances that **failed** to store. Absent if none failed. |
| `(0008,1199)` | `ReferencedSOPSequence` | Sequence of instances that were **successfully** stored. |

Each item in **`FailedSOPSequence` (0008,1198)**:

| Tag | Keyword | Meaning |
|---|---|---|
| `(0008,1150)` | `ReferencedSOPClassUID` | SOP Class UID of the failed instance. |
| `(0008,1155)` | `ReferencedSOPInstanceUID` | SOP Instance UID of the failed instance. |
| `(0008,1197)` | `FailureReason` | Numeric failure reason code (`US`). The standard reuses the C-STORE / Storage Service status codes from PS3.4 Annex C, e.g. `0x0110` Processing failure, `0x0122` Referenced SOP Class not supported, `0xA700` Out of resources, `0xA900` Dataset does not match SOP Class, `0xC000` Cannot understand. **Watch the radix**: in the DICOM JSON body the value is the **decimal** form of that hex code (`0xA900` → `43264`). Implementations may also define their own codes in this space — Azure uses **`43264`** = "instance failed validation" and **`43265`** (`0xA901`) = "instance `StudyInstanceUID` didn't match the one in the request URL". Source for the Azure codes: https://learn.microsoft.com/en-us/azure/healthcare-apis/dicom/dicom-services-conformance-statement-v2 (Store failure reason codes); standard codes: PS3.4 Annex C. |
| `(0008,1196)` | `WarningReason` | Numeric warning reason code (`US`); validation issues not severe enough to fail the store. |
| `(0074,1048)` | `FailedAttributesSequence` | Per-attribute `ErrorComment`s. |

Each item in **`ReferencedSOPSequence` (0008,1199)**:

| Tag | Keyword | Meaning |
|---|---|---|
| `(0008,1150)` | `ReferencedSOPClassUID` | SOP Class UID of the stored instance. |
| `(0008,1155)` | `ReferencedSOPInstanceUID` | SOP Instance UID of the stored instance. |
| `(0008,1190)` | `RetrieveURL` | WADO-RS URL of **this stored instance**. |

Source (table + examples, verbatim):
https://learn.microsoft.com/en-us/azure/healthcare-apis/dicom/dicom-services-conformance-statement-v2 ,
cross-referencing PS3.18 §10.5.3.2 and Annex I.

### 4.5 Example STOW-RS response body (`200 OK`, `application/dicom+json`)

This is verbatim from the Azure conformance statement (a conformant Store Instances Response):

```json
{
  "00081190": {
    "vr": "UR",
    "Value": ["http://localhost/studies/d09e8215-e1e1-4c7a-8496-b4f6641ed232"]
  },
  "00081198": {
    "vr": "SQ",
    "Value": [{
      "00081150": { "vr": "UI", "Value": ["cd70f89a-05bc-4dab-b6b8-1f3d2fcafeec"] },
      "00081155": { "vr": "UI", "Value": ["22c35d16-11ce-43fa-8f86-90ceed6cf4e7"] },
      "00081197": { "vr": "US", "Value": [43265] }
    }]
  },
  "00081199": {
    "vr": "SQ",
    "Value": [{
      "00081150": { "vr": "UI", "Value": ["d246deb5-18c8-4336-a591-aeb6f8596664"] },
      "00081155": { "vr": "UI", "Value": ["4a858cbb-a71f-4c01-b9b5-85f88b031365"] },
      "00081190": {
        "vr": "UR",
        "Value": ["http://localhost/studies/d09e8215.../series/8c4915f5.../instances/4a858cbb..."]
      }
    }]
  }
}
```

Here one instance stored successfully (in `00081199`) and one failed (in `00081198`) with reason `43265`
— Azure's code for "the instance's `StudyInstanceUID` didn't match the `{study}` in the request URL" →
the HTTP status would be **`202 Accepted`** (partial). If only the success sequence were present, it
would be `200 OK`; if no instance could be stored, `409 Conflict`. Source: Azure conformance statement
(https://learn.microsoft.com/en-us/azure/healthcare-apis/dicom/dicom-services-conformance-statement-v2).

### 4.6 `curl` examples

```bash
# Store one DICOM file. NOTE: curl's -F/--form would emit multipart/FORM-DATA, which STOW-RS does NOT
# accept — you must hand-build a multipart/RELATED body (below) and send it with --data-binary, setting
# the part Content-Type to application/dicom explicitly.
curl -u chris:chris1234 -X POST \
  'http://localhost:8000/dicom-web/pacs/BCH/studies' \
  -H 'Accept: application/dicom+json' \
  -H 'Content-Type: multipart/related; type="application/dicom"; boundary=MESSAGEBOUNDARY' \
  --data-binary @- <<'EOF'
--MESSAGEBOUNDARY
Content-Type: application/dicom

<...raw bytes of slice1.dcm...>
--MESSAGEBOUNDARY--
EOF

# In practice use a real DICOMweb client. dcm4che's storescu equivalent for the web is `stowrs`:
#   stowrs --url http://localhost:8000/dicom-web/pacs/BCH/studies ./*.dcm
# Or Python dicomweb-client:
#   from dicomweb_client.api import DICOMwebClient
#   c = DICOMwebClient("http://localhost:8000/dicom-web/pacs/BCH", session=requests.Session())
#   c.store_instances([pydicom.dcmread("slice1.dcm")])
```

> Building a correct `multipart/related; type="application/dicom"` body by hand is error-prone (boundary
> handling, no trailing newline issues, `Content-Type: application/dicom` on each part — and note `curl`'s
> `-F`/`--form` does **not** produce `multipart/related`, it produces `multipart/form-data`, so the body
> above must be assembled literally as shown). For testing CUBE's STOW-RS, prefer a real DICOMweb client:
> the [dcm4che](https://github.com/dcm4che/dcm4che) `stowrs` CLI, the Python
> [`dicomweb-client`](https://github.com/ImagingDataCommons/dicomweb-client) library
> (`DICOMwebClient.store_instances(...)`), or Orthanc's own DICOMweb plugin acting as a client. Orthanc's
> plugin docs note WADO-RS answers are "a multipart stream of `application/dicom` DICOM instances ... [that]
> a Web browser will not be able to display" — the same parsing burden applies to a hand-rolled STOW body.
> Source: https://orthanc.uclouvain.be/book/plugins/dicomweb.html (multipart behavior);
> dcm4che / dicomweb-client are the de-facto STOW test clients (their own project docs).

---

## 5. How the three services compose (the OHIF round-trip)

```
                         OHIF / 3D Slicer / GH indexer
                                     |
   1. QIDO  GET /studies            v
   --------------------------> [ CUBE DICOMweb ]  ---> Postgres (PACSStudy/PACSSeries/PACSInstance)
   <-- application/dicom+json --  (catalog;  each obj carries RetrieveURL = WADO URL)
                                     |
   2. WADO  GET .../series/{s}/metadata
   --------------------------> [ CUBE DICOMweb ]  ---> reads .dcm headers (BulkDataURI -> frames)
   <-- application/dicom+json --
                                     |
   3. WADO  GET .../instances/{i}/frames/1
   --------------------------> [ CUBE DICOMweb ]  ---> core.storage stream of pixels
   <-- multipart/octet-stream --
                                     |
   4. STOW  POST /studies  (push results back / ingest)
   --------------------------> [ CUBE DICOMweb ]  ---> writes to PACS tree, indexes PACSInstance
   <-- application/dicom+json (ReferencedSOPSequence) --
```

- The **`RetrieveURL` (0008,1190)** returned by QIDO is the literal WADO-RS URL of the object — this is the
  contractual glue. If WADO-RS isn't implemented yet, QIDO can still populate `RetrieveURL` with the URL
  that *will* exist; OHIF then lists studies but fails to render pixels (the expected MVP intermediate
  state — see `QIDO_PLAN.md` §8).
- All three services in CUBE share the existing DRF auth chain (Token / Basic / Session / LDAP) and the
  `pacs_users` permission group; no new auth code is required (`CURRENT_API.md` §"Authorization model").

---

## 6. Conformance / Capabilities

PS3.18 §8.9 defines a **Retrieve Capabilities** request. The normative request form is **`OPTIONS SP / SP
HTTP/1.1`** against the service base (a `GET` form is also defined as a WADO-RS resource at the base URL);
it returns a machine-readable capabilities description, negotiated via `Accept` — the standard's response
media types are `application/vnd.sun.wadl+xml` (a WADL document), `application/dicom+json`, and `text/html`.
Most clients tolerate its absence; OHIF does not require it. Worth adding a minimal capabilities document
in a later phase so strict clients (and GH's indexer) can self-configure. Source: PS3.18 §8.9,
https://dicom.nema.org/medical/dicom/current/output/chtml/part18/sect_8.9.html

---

## 7. Quick reference: tags used in this document

| Tag | Keyword | VR |
|---|---|---|
| (0008,0016) | SOPClassUID | UI |
| (0008,0018) | SOPInstanceUID | UI |
| (0008,0020) | StudyDate | DA |
| (0008,0030) | StudyTime | TM |
| (0008,0050) | AccessionNumber | SH |
| (0008,0060) | Modality | CS |
| (0008,0061) | ModalitiesInStudy | CS |
| (0008,0090) | ReferringPhysicianName | PN |
| (0008,1030) | StudyDescription | LO |
| (0008,103E) | SeriesDescription | LO |
| (0008,1090) | ManufacturerModelName | LO |
| (0008,1150) | ReferencedSOPClassUID | UI |
| (0008,1155) | ReferencedSOPInstanceUID | UI |
| (0008,1190) | RetrieveURL | UR |
| (0008,1196) | WarningReason | US |
| (0008,1197) | FailureReason | US |
| (0008,1198) | FailedSOPSequence | SQ |
| (0008,1199) | ReferencedSOPSequence | SQ |
| (0010,0010) | PatientName | PN |
| (0010,0020) | PatientID | LO |
| (0010,0030) | PatientBirthDate | DA |
| (0010,0040) | PatientSex | CS |
| (0020,000D) | StudyInstanceUID | UI |
| (0020,000E) | SeriesInstanceUID | UI |
| (0020,0011) | SeriesNumber | IS |
| (0020,0013) | InstanceNumber | IS |
| (0020,1206) | NumberOfStudyRelatedSeries | IS |
| (0020,1208) | NumberOfStudyRelatedInstances | IS |
| (0020,1209) | NumberOfSeriesRelatedInstances | IS |
| (0028,0008) | NumberOfFrames | IS |
| (0028,0010) | Rows | US |
| (0028,0011) | Columns | US |
| (0028,0100) | BitsAllocated | US |
| (0040,0244) | PerformedProcedureStepStartDate | DA |
| (0074,1048) | FailedAttributesSequence | SQ |
| (7FE0,0010) | PixelData | OW/OB |

---

## Sources

- DICOM PS3.18 (Web Services), current: https://dicom.nema.org/medical/dicom/current/output/html/part18.html
  - STOW-RS §10.5: https://dicom.nema.org/medical/dicom/current/output/chtml/part18/sect_10.5.html
  - STOW-RS Behavior §10.5.2: https://dicom.nema.org/medical/dicom/current/output/chtml/part18/sect_10.5.2.html
  - DICOM JSON Model §F.2: https://dicom.nema.org/medical/dicom/current/output/chtml/part18/sect_F.2.html
- DICOMweb overview & service pages: https://www.dicomstandard.org/using/dicomweb
  - QIDO-RS: https://www.dicomstandard.org/using/dicomweb/query-qido-rs
  - WADO-RS: https://www.dicomstandard.org/using/dicomweb/retrieve-wado-rs-and-wado-uri
  - STOW-RS: https://www.dicomstandard.org/using/dicomweb/store-stow-rs
- Microsoft Azure DICOM service conformance statement v2 (concrete examples, matching tables, response
  bodies): https://learn.microsoft.com/en-us/azure/healthcare-apis/dicom/dicom-services-conformance-statement-v2
- Orthanc DICOMweb plugin (reference implementation behavior): https://orthanc.uclouvain.be/book/plugins/dicomweb.html
- pydicom (VR lookup, dataset parsing): https://pydicom.github.io/
- CUBE-specific seams: `proposal-to-bch/CURRENT_API.md`, `proposal-to-bch/QIDO_PLAN.md`,
  `proposal-to-bch/RESEARCH_TICKET_OUTPUT.md` (this repo).
