# DICOM Standard Fundamentals

> Knowledge base for the ATLAS DICOMweb-on-CUBE spike. This file teaches DICOM from
> zero so you can speak fluently about the data model that QIDO-RS, WADO-RS, and
> STOW-RS all sit on top of. The DICOMweb HTTP services (PS3.18) are covered in a
> sibling doc; **this doc is the underlying data standard** (PS3.3 information model,
> PS3.5 encoding, PS3.6 dictionary, PS3.10 file format).
>
> Primary sources cited inline:
> - DICOM Library overview — https://www.dicomlibrary.com/dicom/
> - NEMA DICOM Standard, Part 5 (Data Structures and Encoding) — https://dicom.nema.org/medical/dicom/current/output/chtml/part05/
> - NEMA DICOM Standard, Part 18 (Web Services / DICOMweb) — https://dicom.nema.org/medical/dicom/current/output/chtml/part18/
> - Transfer syntax UID registry — https://www.medicalconnections.co.uk/kb/Transfer-Syntax
> - pydicom (the library Phase A uses to read headers) — https://pydicom.github.io/

---

## 0. What DICOM is (in one paragraph)

DICOM (**Digital Imaging and Communications in Medicine**) is *both* a file format
**and** a network protocol for "handling, storing, printing, and transmitting
information in medical imaging" (https://www.dicomlibrary.com/dicom/). It is maintained
by NEMA as a multi-part standard (PS3.1 ... PS3.20). A `.dcm` file is one half of it;
the wire protocols (classic DIMSE over TCP/IP, and the modern HTTP-based **DICOMweb**)
are the other. The reason DICOM matters for this project: **the same data model and the
same attribute tags** appear in the file on disk, in the DIMSE messages CUBE's PACS stack
exchanges with a remote PACS, and in the JSON that QIDO-RS returns. (Division of labor:
`oxidicom` is purely a **C-STORE SCP receiver** — it accepts pushed instances and registers
them in CUBE; it does *not* issue C-FIND. The C-FIND/C-MOVE *query-and-pull* against a
remote PACS is driven by `pfdcm`. See https://github.com/FNNDSC/oxidicom.)
Learn the model once and it applies everywhere.

---

## 1. The DICOM Information Model (Patient → Study → Series → Instance)

This four-level hierarchy is the single most important thing to internalize. Every
DICOMweb resource path and every QIDO query level maps directly onto it.

```
Patient                       (a person; identified by PatientID)
  └── Study                   (one imaging exam/visit; StudyInstanceUID)
        └── Series            (one acquisition w/ one Modality; SeriesInstanceUID)
              └── Instance    (one image/object = one SOP Instance; SOPInstanceUID)
                    └── (pixels live inside the Instance; multi-frame = N frames)
```

| Level        | Real-world meaning                                                                 | Primary identifier (UID)   | DICOMweb path segment |
|--------------|------------------------------------------------------------------------------------|----------------------------|-----------------------|
| **Patient**  | The person being imaged.                                                            | `PatientID` (an ID, *not* a UID) | (no own path; matched by query) |
| **Study**    | A single examination — e.g. "CT abdomen, 2026-05-28". Can contain multiple series. | `StudyInstanceUID` (0020,000D) | `/studies/{StudyInstanceUID}` |
| **Series**   | One set of images from one acquisition; exactly one `Modality` per series.          | `SeriesInstanceUID` (0020,000E) | `/series/{SeriesInstanceUID}` |
| **Instance** | One *SOP Instance* = one object (usually one image, possibly multi-frame).           | `SOPInstanceUID` (0008,0018) | `/instances/{SOPInstanceUID}` |

Source: PS3.3 information model; summarized at https://www.dicomlibrary.com/dicom/.

Key facts to say out loud tomorrow:
- A **Study** can mix modalities only across series (e.g. a PET/CT study has a PET
  series and a CT series); within a single **Series** the `Modality` is fixed.
- "**Instance**" and "**SOP Instance**" are the same thing. SOP = **Service-Object Pair**.
- **CUBE today** stores at the **Series** level (`PACSSeries`) and keeps the raw `.dcm`
  files on disk; it has *no* per-instance row. The spike adds a `PACSInstance` model so
  QIDO-RS can answer `/instances` queries (see `proposal-to-bch/QIDO_PLAN.md` §3.1 and
  `proposal-to-bch/code/source/chris_backend/dicomweb/`).

---

## 2. SOP Class vs SOP Instance (why `SOPClassUID` matters)

- A **SOP Class** says *what kind of object this is* — "CT Image Storage", "MR Image
  Storage", "Secondary Capture", "Encapsulated PDF", etc. Identified by **`SOPClassUID`
  (0008,0016)**.
- A **SOP Instance** is one concrete object of that class — identified by
  **`SOPInstanceUID` (0008,0018)**.

Analogy: SOP Class is the *type/class*; SOP Instance is the *object*. The SOP Class also
determines which **Storage SOP Class** is negotiated on a C-STORE association, and which
IOD (Information Object Definition) attributes are mandatory.

Common SOP Class UIDs (all under the `1.2.840.10008.5.1.4.1.1.*` "Storage" root):

| SOP Class                         | SOPClassUID                          |
|-----------------------------------|--------------------------------------|
| CT Image Storage                  | `1.2.840.10008.5.1.4.1.1.2`          |
| MR Image Storage                  | `1.2.840.10008.5.1.4.1.1.4`          |
| Computed Radiography Image        | `1.2.840.10008.5.1.4.1.1.1`          |
| Digital X-Ray (for presentation)  | `1.2.840.10008.5.1.4.1.1.1.1`        |
| Ultrasound Image Storage          | `1.2.840.10008.5.1.4.1.1.6.1`        |
| Secondary Capture Image           | `1.2.840.10008.5.1.4.1.1.7`          |
| Enhanced CT / Enhanced MR (multi-frame) | `...1.1.2.1` / `...1.1.4.1`    |

Source: PS3.4/PS3.6 UID registry (https://dicom.nema.org/medical/dicom/current/output/chtml/part06/).

---

## 3. Data Elements: the building block of every DICOM object

A DICOM dataset is an ordered list of **Data Elements**. Each Data Element has four
parts (PS3.5 §7, https://dicom.nema.org/medical/dicom/current/output/chtml/part05/):

```
+-----------+--------+----------------+-------------------------------+
|   Tag     |  VR    |  Value Length  |          Value Field          |
| (4 bytes) | (2 B)* |  (2 or 4 B)*   |  (Value Length bytes, even)   |
+-----------+--------+----------------+-------------------------------+
   group,elem  type     # of bytes            the actual data
* VR is present only in Explicit-VR transfer syntaxes; absent in Implicit VR.
```

### 3.1 The Tag = (group, element)

A **tag** is a pair of 16-bit hex numbers written `(gggg,eeee)`:

- `(0010,0010)` → **PatientName**
- `(0020,000D)` → **StudyInstanceUID**
- `(0008,0060)` → **Modality**

Conventions you must know:
- **Even group numbers are standard** (defined in the dictionary). **Odd group numbers
  are private** (vendor-specific; no guaranteed VR in the public dictionary).
- Group `0002` is special: the **File Meta Information** group (see §6).
- In DICOMweb/QIDO query strings the tag is written as **8 concatenated hex digits with
  no comma and no parentheses**, uppercase: `(0010,0010)` → `00100010`. So
  `?00100010=DOE^JANE` and `?PatientName=DOE^JANE` are equivalent (see
  `proposal-to-bch/QIDO_PLAN.md` §5.1).

### 3.2 The Data Dictionary

The **data dictionary** (PS3.6, https://dicom.nema.org/medical/dicom/current/output/chtml/part06/)
maps every standard tag → its **keyword** (`PatientName`), its **VR**, and its **Value
Multiplicity (VM)**. This is exactly what `pydicom.datadict.dictionary_VR(tag)` and
`dictionary_keyword(tag)` look up. In **Implicit VR** transfer syntax the VR is *not* in
the file, so the reader must consult the dictionary — which is why an outdated dictionary
breaks implicit-VR parsing (https://www.medicalconnections.co.uk/kb/Transfer-Syntax).

### 3.3 Value Multiplicity (VM)

A single Data Element can hold multiple values, delimited by backslash `\` in string VRs.
Example: `ImageType` (0008,0008) is often `ORIGINAL\PRIMARY\AXIAL`. The dictionary
records the legal VM (e.g. `1`, `1-n`, `2`, `3`).

---

## 4. Value Representations (VR) — the data type of each element

The **VR** is a two-letter code giving the data type. In Explicit-VR encodings it is
stored in the element; in Implicit VR it is looked up from the dictionary. Full table from
PS3.5 §6.2 (https://dicom.nema.org/medical/dicom/current/output/chtml/part05/sect_6.2.html):

| VR | Meaning                  | Format / notes                                                  | Max length |
|----|--------------------------|-----------------------------------------------------------------|------------|
| **AE** | Application Entity   | 16-char AE title (e.g. a PACS node name)                         | 16 B |
| **AS** | Age String           | `nnnD`/`nnnW`/`nnnM`/`nnnY`, e.g. `045Y`                          | 4 B fixed |
| **AT** | Attribute Tag        | a tag value (4 bytes); not emitted at the QIDO surface           | 4 B fixed |
| **CS** | Code String          | UPPERCASE + digits + space + `_` only; e.g. `Modality=CT`        | 16 B |
| **DA** | Date                 | `YYYYMMDD` (Gregorian), e.g. `20260528`                          | 8 B fixed |
| **DS** | Decimal String       | fixed/float as text, e.g. `1.5\1.5`                              | 16 B |
| **DT** | Date Time            | `YYYYMMDDHHMMSS.FFFFFF&ZZXX` with optional UTC offset            | 26 B |
| **FL** | Float (single)       | 32-bit IEEE                                                      | 4 B fixed |
| **FD** | Float (double)       | 64-bit IEEE                                                      | 8 B fixed |
| **IS** | Integer String       | decimal integer as text, e.g. `InstanceNumber=42`               | 12 B |
| **LO** | Long String          | up to 64 chars, no `\`; e.g. `StudyDescription`                  | 64 chars |
| **LT** | Long Text            | up to 10,240 chars                                               | 10,240 |
| **OB** | Other Byte           | raw byte stream (e.g. pixel data 8-bit); QIDO does **not** emit  | 2³²-2 |
| **OW** | Other Word           | raw 16-bit word stream (e.g. pixel data 16-bit)                  | 2³²-2 |
| **OF/OD/OL/OV** | Other Float/Double/Long/Very-long | binary streams                          | large |
| **PN** | Person Name          | `Family^Given^Middle^Prefix^Suffix`; `=` separates alphabetic/ideographic/phonetic | 64/group |
| **SH** | Short String         | up to 16 chars; e.g. `AccessionNumber`                          | 16 chars |
| **SL/SS** | Signed Long/Short | 32/16-bit signed int                                            | 4 / 2 B |
| **SQ** | Sequence of Items    | nested datasets (a list of sub-datasets)                        | — |
| **ST** | Short Text           | up to 1,024 chars                                               | 1,024 |
| **TM** | Time                 | `HHMMSS.FFFFFF`, e.g. `143012`                                   | 14 B |
| **UI** | Unique Identifier    | digits `0-9` and `.` only, NULL-padded to even length; **all UIDs** | 64 B |
| **UL/US** | Unsigned Long/Short | 32/16-bit unsigned int; e.g. `Rows`/`Columns` are US             | 4 / 2 B |
| **UN** | Unknown              | unknown VR (fallback, e.g. private tags)                        | variable |
| **UT** | Unlimited Text       | very long text                                                  | 2³²-2 |
| **UC/UR/UV/SV** | Unlimited Chars / URI / unsigned & signed 64-bit | newer VRs                | varies |

VR gotchas that bite implementers (all relevant to the DICOM-JSON renderer in
`proposal-to-bch/QIDO_PLAN.md` §6):
- **PN** in DICOM JSON is NOT a plain string — it serializes as
  `{"Alphabetic": "DOE^JANE"}` inside the `Value` array.
- **DA/TM/DT** serialize as their **DICOM string form** (`"20260528"`, `"143012"`), not
  ISO-8601 — even though the DB stores Python `date`/`time`.
- **US/UL/SS/SL/FL/FD** → JSON **Numbers** (required by PS3.18 §F.2.3 —
  https://dicom.nema.org/medical/dicom/current/output/chtml/part18/sect_f.2.3.html).
  **IS/DS** (and SV/UV) → JSON Number *or* String are both permitted; a String is allowed
  to preserve original format / avoid precision loss. Pick one and be consistent.
- **UI** values are always strings.
- **OB/OW/AT/UN** carry binary/pixel data and are **not** returned by QIDO (metadata only).
- **SQ** is a nested dataset; QIDO levels in this spike do not emit SQ, but a robust
  helper should error loudly rather than silently mangle one.

VR also affects QIDO **matching semantics** (the rules are inherited from C-FIND,
PS3.4 §C.2.2.2 — QIDO-RS defers to them via PS3.18 §8.3.4): wildcard matching (`*`,`?`)
is permitted on the VRs **AE, CS, LO, LT, PN, SH, ST, UC, UR, UT** (PS3.4 §C.2.2.2.4:
"the AE, LO, LT, PN, SH, ST, UC, UR and UT VRs ... allow ... wild card", plus CS) — note
this includes **CS, ST,** and **AE/UR**, and **excludes UI** (UIDs use exact / UID-list
matching, not wildcards). **Range matching** (`20230101-20231231`, delimiter `-`) applies *only* to
**DA, TM, DT**; for these VRs `-` is reserved for ranges and is *not* a wildcard, so
wildcards are not allowed on date/time VRs. (PS3.4 §C.2.2.2,
https://dicom.nema.org/medical/dicom/current/output/chtml/part04/sect_C.2.2.2.html;
PS3.18 §8.3.4, https://dicom.nema.org/medical/dicom/current/output/chtml/part18/.)

---

## 5. UIDs — globally unique identifiers

A **UID** (VR = **UI**) is a globally unique, dotted-decimal OID-style string, max 64
chars, characters limited to `0-9` and `.`. They are the join keys of the entire model.

| UID                | Tag         | Identifies                          | Notes |
|--------------------|-------------|-------------------------------------|-------|
| **StudyInstanceUID**  | (0020,000D) | one Study                        | generated by the modality/PACS at acquisition |
| **SeriesInstanceUID** | (0020,000E) | one Series                       | unique within (and effectively across) studies |
| **SOPInstanceUID**    | (0008,0018) | one Instance (image/object)      | unique per object, globally |
| **SOPClassUID**       | (0008,0016) | the *type* of object             | from the registry, e.g. CT Image Storage |
| **TransferSyntaxUID** | (0002,0010) | the encoding of the dataset      | lives in File Meta group (§6) |

The DICOM root `1.2.840.10008` belongs to NEMA and prefixes all *standard* UIDs (SOP
Classes, transfer syntaxes, well-known instances). **Instance** UIDs your modality
generates use *its own* registered root, not the NEMA root. Source: PS3.5/PS3.6
(https://dicom.nema.org/medical/dicom/current/output/chtml/part05/).

Why this matters here: QIDO returns these as `UI`-typed values; WADO-RS retrieval paths
are built from `StudyInstanceUID/SeriesInstanceUID/SOPInstanceUID`; the spike's
`PACSInstance` model stores `SOPClassUID`, `SOPInstanceUID`, and `TransferSyntaxUID` as
`CharField(max_length=100)` (QIDO_PLAN.md §3.1).

---

## 6. The DICOM File Format (PS3.10) — preamble + DICM + file meta

A `.dcm` file on disk has a fixed wrapper around the dataset (PS3.10,
https://dicom.nema.org/medical/dicom/current/output/chtml/part10/):

```
Byte 0                                   Byte 127  128  131  132 ...
+---------------------------------------+--------+--------+----------------------+
|        128-byte preamble (zeros)      |  "DICM"|  File  |   Data Set           |
|        (application-defined)          |  magic |  Meta  |   (the actual        |
|                                       |        |  Info  |    attributes)       |
+---------------------------------------+--------+--------+----------------------+
                                          4 bytes  group     encoded per the
                                                   0002      Transfer Syntax UID
```

1. **128-byte preamble** — usually all zeros; lets a non-DICOM viewer skip a header.
2. **`DICM`** — 4-byte magic at offset 128. Its presence is how you confirm a file is
   DICOM Part-10. (`pydicom.dcmread(..., force=True)` will read even when it's missing.)
3. **File Meta Information** — group **`0002`**, *always* encoded as **Explicit VR Little
   Endian** regardless of the dataset's transfer syntax. Key elements:
   - `(0002,0010) TransferSyntaxUID` — how the rest of the file is encoded (§7).
   - `(0002,0002) MediaStorageSOPClassUID` — equals the dataset's `SOPClassUID`.
   - `(0002,0003) MediaStorageSOPInstanceUID` — equals the dataset's `SOPInstanceUID`.
   - `(0002,0012) ImplementationClassUID`, `(0002,0013) ImplementationVersionName`.
4. **Data Set** — the patient/study/series/instance attributes and (if an image) the
   `(7FE0,0010) PixelData`, encoded per the transfer syntax in (0002,0010).

Reading just the header is exactly what the spike's indexer does:
`pydicom.dcmread(f, stop_before_pixels=True, force=True)` — it parses the file meta + the
dataset attributes but skips PixelData, so indexing is fast (~5–20 ms/file, QIDO_PLAN.md
§12.4). Source: pydicom docs https://pydicom.github.io/.

> Note: DICOMweb STOW-RS and WADO-RS transmit instances *with* the file-meta/transfer-syntax
> context but inside a `multipart/related` body with `type="application/dicom"`, not as a
> bare `.dcm` on disk (PS3.18, https://dicom.nema.org/medical/dicom/current/output/chtml/part18/).

---

## 7. Transfer Syntaxes — how the dataset bytes are encoded

A **Transfer Syntax** specifies three things at once: (a) explicit vs implicit VR,
(b) byte order (endianness), (c) whether/how PixelData is compressed. It is named by a
UID stored in `(0002,0010)`. Canonical UIDs
(https://www.medicalconnections.co.uk/kb/Transfer-Syntax,
https://dicom.nema.org/medical/dicom/current/output/chtml/part05/sect_a.2.html):

| Transfer Syntax                         | UID                          | VR       | Endian | Pixel compression |
|-----------------------------------------|------------------------------|----------|--------|-------------------|
| **Implicit VR Little Endian** (DEFAULT) | `1.2.840.10008.1.2`          | implicit | little | none |
| **Explicit VR Little Endian**           | `1.2.840.10008.1.2.1`        | explicit | little | none |
| Deflated Explicit VR Little Endian      | `1.2.840.10008.1.2.1.99`     | explicit | little | whole stream deflated |
| **Explicit VR Big Endian** (retired)    | `1.2.840.10008.1.2.2`        | explicit | big    | none |
| RLE Lossless                            | `1.2.840.10008.1.2.5`        | explicit | little | RLE |
| JPEG Baseline (Process 1, 8-bit lossy)  | `1.2.840.10008.1.2.4.50`     | explicit | little | JPEG lossy |
| JPEG Extended (Process 2&4, 12-bit)     | `1.2.840.10008.1.2.4.51`     | explicit | little | JPEG lossy |
| JPEG Lossless (Process 14, SV1)         | `1.2.840.10008.1.2.4.70`     | explicit | little | JPEG lossless |
| JPEG Lossless (Process 14)              | `1.2.840.10008.1.2.4.57`     | explicit | little | JPEG lossless |
| JPEG-LS Lossless                        | `1.2.840.10008.1.2.4.80`     | explicit | little | JPEG-LS lossless |
| JPEG-LS Near-Lossless                   | `1.2.840.10008.1.2.4.81`     | explicit | little | JPEG-LS |
| JPEG 2000 Lossless                      | `1.2.840.10008.1.2.4.90`     | explicit | little | JPEG 2000 lossless |
| JPEG 2000 (lossy or lossless)           | `1.2.840.10008.1.2.4.91`     | explicit | little | JPEG 2000 |

Facts to have ready:
- **Implicit VR Little Endian (`1.2.840.10008.1.2`) is the only mandatory/default
  transfer syntax** — every DICOM system must support it
  (https://www.medicalconnections.co.uk/kb/Transfer-Syntax). It has **no VR in the file**,
  so the reader needs the dictionary.
- **Explicit VR carries the VR in each element** → self-describing, robust to unknown tags.
- **All compressed transfer syntaxes use Explicit VR Little Endian** for the non-pixel
  attributes; only the PixelData is encapsulated/compressed.
- **Big Endian is retired** and "very little used nowadays."
- When a DICOMweb client retrieves with WADO-RS it can request a specific transfer syntax
  via the `transfer-syntax` parameter in the Accept header; STOW-RS preserves whatever
  the file already uses.

---

## 8. Pixel-describing attributes (the "instance" tags QIDO surfaces)

For image instances, these attributes describe the pixel buffer and are exactly what the
spike's `PACSInstance` model indexes (QIDO_PLAN.md §3.1):

| Tag         | Keyword                     | VR | Meaning |
|-------------|-----------------------------|----|---------|
| (0028,0010) | `Rows`                      | US | image height in pixels |
| (0028,0011) | `Columns`                   | US | image width in pixels |
| (0028,0100) | `BitsAllocated`             | US | bits per pixel allocated (8/16) |
| (0028,0101) | `BitsStored`                | US | bits actually used |
| (0028,0002) | `SamplesPerPixel`           | US | 1 (mono) or 3 (RGB) |
| (0028,0004) | `PhotometricInterpretation` | CS | `MONOCHROME2`, `RGB`, `YBR_FULL`, ... |
| (0028,0008) | `NumberOfFrames`            | IS | multi-frame count (1 if absent) |
| (0020,0013) | `InstanceNumber`            | IS | ordering within the series |
| (7FE0,0010) | `PixelData`                 | OW/OB | the actual pixels (not in QIDO) |

---

## 9. Modalities (the `Modality` (0008,0060) code, VR = CS)

`Modality` is a **CS** code string, fixed per series. Common values you should recognize:

| Code | Modality                         | Code | Modality                          |
|------|----------------------------------|------|-----------------------------------|
| CT   | Computed Tomography              | MR   | Magnetic Resonance                |
| US   | Ultrasound                       | XA   | X-Ray Angiography                 |
| CR   | Computed Radiography             | DX   | Digital Radiography               |
| MG   | Mammography                      | PT   | Positron Emission Tomography (PET)|
| NM   | Nuclear Medicine                 | RF   | Radio Fluoroscopy                 |
| OT   | Other                            | SC   | Secondary Capture                 |
| SR   | Structured Report                | PR   | Presentation State                |
| RTSTRUCT/RTPLAN/RTDOSE | Radiotherapy objects   | SEG  | Segmentation                      |

Related study-level rollup: `(0008,0061) ModalitiesInStudy` lists the distinct modality
codes across all series in a study — QIDO computes this by aggregating `Modality` across
the study's series (QIDO_PLAN.md §7.2). Source: PS3.3 / DICOM Library
(https://www.dicomlibrary.com/dicom/).

---

## 10. Cheat-sheet: the most important tags

Memorize these — they are the attributes that show up in QIDO queries, the `PACSSeries`
columns CUBE already stores (CURRENT_API.md), and the required QIDO return attributes
(PS3.18 §10.6). `*` marks a tag CUBE already stores on `PACSSeries`.

| Tag (group,elem) | Keyword                          | VR | Level    | Notes |
|------------------|----------------------------------|----|----------|-------|
| (0010,0010)      | `PatientName`* | PN | Patient | `Family^Given^...`; wildcard-matchable |
| (0010,0020)      | `PatientID`* | LO | Patient | the patient identifier (not a UID) |
| (0010,0030)      | `PatientBirthDate`* | DA | Patient | `YYYYMMDD` |
| (0010,0040)      | `PatientSex`* | CS | Patient | `M`/`F`/`O` |
| (0010,1010)      | `PatientAge` | AS | Patient | `045Y` (CUBE stores computed int) |
| (0020,000D)      | `StudyInstanceUID`* | UI | Study | study join key |
| (0008,0020)      | `StudyDate`* | DA | Study | `YYYYMMDD`; range-matchable |
| (0008,0030)      | `StudyTime` | TM | Study | `HHMMSS`; **not yet on `PACSSeries`** (QIDO_PLAN §14.1) |
| (0008,0050)      | `AccessionNumber`* | SH | Study | order/accession id |
| (0008,1030)      | `StudyDescription`* | LO | Study | free text; icontains in CUBE |
| (0008,0061)      | `ModalitiesInStudy` | CS | Study | computed aggregate (1-n) |
| (0008,0090)      | `ReferringPhysicianName` | PN | Study | required in QIDO study result |
| (0020,1206)      | `NumberOfStudyRelatedSeries` | IS | Study | computed |
| (0020,1208)      | `NumberOfStudyRelatedInstances` | IS | Study | computed |
| (0008,0060)      | `Modality`* | CS | Series | one per series; `CT`,`MR`,... |
| (0020,000E)      | `SeriesInstanceUID`* | UI | Series | series join key |
| (0020,0011)      | `SeriesNumber` | IS | Series | required in QIDO series result; spike adds column |
| (0008,103E)      | `SeriesDescription`* | LO | Series | free text |
| (0018,1030)      | `ProtocolName`* | LO | Series | acquisition protocol |
| (0018,0015)      | `BodyPartExamined` | CS | Series | spike adds column |
| (0008,0070)      | `Manufacturer` | LO | Series | spike adds column |
| (0040,0244)      | `PerformedProcedureStepStartDate` | DA | Series | spike adds column |
| (0040,0245)      | `PerformedProcedureStepStartTime` | TM | Series | spike adds column |
| (0008,0016)      | `SOPClassUID` | UI | Instance | object type; spike `PACSInstance` |
| (0008,0018)      | `SOPInstanceUID` | UI | Instance | instance join key; spike `PACSInstance` |
| (0020,0013)      | `InstanceNumber` | IS | Instance | ordering |
| (0028,0010)/(0028,0011) | `Rows`/`Columns`          | US | Instance | image dims |
| (0028,0100)      | `BitsAllocated` | US | Instance | bit depth |
| (0028,0008)      | `NumberOfFrames` | IS | Instance | multi-frame |
| (0002,0010)      | `TransferSyntaxUID` | UI | (file meta) | encoding of the instance |
| (0008,1190)      | `RetrieveURL` | UR | all | WADO-RS URL of the resource (QIDO return) |

---

## 11. Worked example — DICOM JSON Model (what QIDO returns)

QIDO-RS responses use the **DICOM JSON Model** (PS3.18 Annex F,
https://dicom.nema.org/medical/dicom/current/output/chtml/part18/), content-type
`application/dicom+json`. Each instance is an object keyed by **8-hex-digit tags**, each
value carrying its **`vr`** plus a **`Value`** array:

```json
[
  {
    "00080020": { "vr": "DA", "Value": ["20260528"] },
    "00080050": { "vr": "SH", "Value": ["ACC-00042"] },
    "00080061": { "vr": "CS", "Value": ["CT", "MR"] },
    "00081030": { "vr": "LO", "Value": ["CT ABDOMEN W/ CONTRAST"] },
    "0020000D": { "vr": "UI", "Value": ["1.2.840.113619.2.55.3.604688.971.143.926"] },
    "00100010": { "vr": "PN", "Value": [{ "Alphabetic": "DOE^JANE" }] },
    "00100020": { "vr": "LO", "Value": ["MRN-12345"] },
    "00100040": { "vr": "CS", "Value": ["F"] },
    "00201206": { "vr": "IS", "Value": ["3"] },
    "00201208": { "vr": "IS", "Value": ["540"] },
    "00081190": { "vr": "UR", "Value": ["https://cube/dicom-web/pacs/BCH/studies/1.2.840..."] }
  }
]
```

Note the **PN** value is a nested `{"Alphabetic": ...}` object, not a bare string — the
single most common bug when hand-rolling a DICOM-JSON renderer.

### Example QIDO query (tag-hex vs keyword forms are equivalent)

```sh
# By keyword
curl -H 'Accept: application/dicom+json' \
  'https://cube/dicom-web/pacs/BCH/studies?PatientName=DOE*&StudyDate=20260101-20260528&limit=5'

# Identical first two params in tag-hex form; UID-list match on SeriesInstanceUID
curl -H 'Accept: application/dicom+json' \
  'https://cube/dicom-web/pacs/BCH/series?00100010=DOE*&0020000E=1.2.3,1.2.4'
```

- `DOE*` → wildcard (PN VR): `*`→`%`, `?`→`_`, translated to SQL `ILIKE`.
- `20260101-20260528` → range match (DA VR).
- `0020000E=1.2.3,1.2.4` → **UID List matching** — a comma-separated value list is
  permitted *only* for attributes that allow UID List matching (UID VRs), per
  PS3.18 §8.3.4.2 / PS3.4 §C.2.2.2.2. It is **not** a general OR syntax: comma lists are
  **not** valid on CS attributes like `ModalitiesInStudy` (0008,0061) in baseline QIDO-RS —
  to OR over modalities you would issue separate queries. CUBE may offer a non-standard
  extension here, but the standard does not.
- `limit` / `offset` are the QIDO paging parameters (PS3.18 §8.3.4.2).
(Matching rules per PS3.18 §8.3.4 + PS3.4 §C.2.2.2; JSON shape per PS3.18 Annex F;
see QIDO_PLAN.md §5.1.)

---

## 12. How this maps onto the CUBE work (quick orientation)

| DICOM concept                  | Where it lives in CUBE today / after the spike |
|--------------------------------|------------------------------------------------|
| Patient/Study/Series tags      | `pacsfiles.models.PACSSeries` columns (CURRENT_API.md) |
| Instance tags (SOP*, Rows, ...) | **new** `dicomweb.models.PACSInstance` (QIDO_PLAN §3.1) |
| Reading tags from a `.dcm`     | `pydicom.dcmread(..., stop_before_pixels=True)` in `index_pacs_instance` Celery task |
| Tag → ORM field map            | `dicomweb/query.py` `TAG_MAP_STUDY/SERIES/INSTANCE` |
| DICOM JSON Model output        | `dicomweb/dicomjson.py` + `DicomJsonRenderer` (`application/dicom+json`) |
| Raw `.dcm` retrieval           | today `GET /api/v1/pacs/files/{id}/.…`; WADO-RS adds `multipart/related` packaging |
| Ingest of new instances        | `oxidicom` C-STORE (port 11111) → `POST /api/v1/pacs/series/`; STOW-RS will be a second ingest path |

---

## 13. One-line glossary

- **IOD** — Information Object Definition: the attribute schema for a given object type.
- **SOP Class / SOP Instance** — the *type* / the *object*; `SOPClassUID` / `SOPInstanceUID`.
- **DIMSE** — the classic message service (C-STORE, C-FIND, C-MOVE) over TCP/IP.
- **DICOMweb** — the HTTP/REST family: **QIDO-RS** (query), **WADO-RS** (retrieve),
  **STOW-RS** (store) — all defined in PS3.18.
- **AE Title** — Application Entity title; the 16-char name of a DICOM network node.
- **Transfer Syntax** — the encoding rules (VR mode + endianness + pixel compression).
- **VR / VM** — Value Representation (datatype) / Value Multiplicity (count of values).

---

### Sources

- DICOM Library overview — https://www.dicomlibrary.com/dicom/
- NEMA PS3.5 §6.2 Value Representations — https://dicom.nema.org/medical/dicom/current/output/chtml/part05/sect_6.2.html
- NEMA PS3.5 §A.2 Explicit VR Little Endian — https://dicom.nema.org/medical/dicom/current/output/chtml/part05/sect_a.2.html
- NEMA PS3.18 Web Services (QIDO/WADO/STOW, DICOM JSON Model) — https://dicom.nema.org/medical/dicom/current/output/chtml/part18/
- NEMA PS3.6 Data Dictionary / UID registry — https://dicom.nema.org/medical/dicom/current/output/chtml/part06/
- NEMA PS3.10 Media Storage / File Format — https://dicom.nema.org/medical/dicom/current/output/chtml/part10/
- Transfer syntax UID registry — https://www.medicalconnections.co.uk/kb/Transfer-Syntax
- pydicom documentation — https://pydicom.github.io/
- Prior spike artifacts (this repo): `proposal-to-bch/CURRENT_API.md`, `proposal-to-bch/QIDO_PLAN.md`, `proposal-to-bch/code/source/chris_backend/dicomweb/`
