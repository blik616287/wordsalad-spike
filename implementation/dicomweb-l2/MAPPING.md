# DICOMweb attribute → CUBE model field map

Per-hierarchy-level mapping of DICOM attributes (keyword + 8-hex tag + VR) to the
Django model field they read from or write to. This is the authoritative table
behind `query_parser.py` (the filter map) and `serializers.py` (the response
builders). Tags and VRs are per PS3.6 / PS3.18; the QIDO required-attribute sets
are PS3.18 §10.6.3 Tables 10.6.3-3/-4/-5.

Models referenced:
- `pacsfiles.PACS` — one row per upstream PACS source (`identifier`, e.g. `BCH`).
- `dicomweb.PACSStudy` — **NEW (this spike)**, one row per `(pacs, StudyInstanceUID)`.
- `pacsfiles.PACSSeries` — existing central row; gains nullable FK `study → PACSStudy`.
- `dicomweb.PACSInstance` — Phase A, one row per `.dcm`, 1-to-1 with `pacsfiles.PACSFile`.

Legend for "Source": `col` = stored column; `denorm` = denormalized/cached
counter maintained at ingest; `agg` = aggregated at query time (`Count`,
`ArrayAgg`); `synth` = synthesized per-request (not stored).

---

## Patient level (implicit — tags ride on `PACSStudy`)

| Attribute | Tag | VR | Model field | Source |
|---|---|---|---|---|
| PatientName | (0010,0010) | PN | `PACSStudy.PatientName` | col |
| PatientID | (0010,0020) | LO | `PACSStudy.PatientID` | col (db_index) |
| PatientBirthDate | (0010,0030) | DA | `PACSStudy.PatientBirthDate` | col |
| PatientSex | (0010,0040) | CS | `PACSStudy.PatientSex` | col |

Patient is deliberately not its own model — see `models.py` rationale and D2 in
`knowledge-base/08-l2-architecture-decisions.md`. (`PACSSeries` keeps its own
copies of the Patient columns too, populated by the existing ingest path; the
authoritative Study-level read uses `PACSStudy`.)

## Study level → `dicomweb.PACSStudy`

| Attribute | Tag | VR | Model field | Source |
|---|---|---|---|---|
| StudyInstanceUID | (0020,000D) | UI | `StudyInstanceUID` | col (db_index) |
| StudyDate | (0008,0020) | DA | `StudyDate` | col (db_index) |
| StudyTime | (0008,0030) | TM | `StudyTime` | col |
| AccessionNumber | (0008,0050) | SH | `AccessionNumber` | col (db_index) |
| StudyDescription | (0008,1030) | LO | `StudyDescription` | col |
| ReferringPhysicianName | (0008,0090) | PN | `ReferringPhysicianName` | col |
| ModalitiesInStudy | (0008,0061) | CS | `ModalitiesInStudy` (`\`-joined) | denorm |
| NumberOfStudyRelatedSeries | (0020,1206) | IS | `NumberOfStudyRelatedSeries` | denorm |
| NumberOfStudyRelatedInstances | (0020,1208) | IS | `NumberOfStudyRelatedInstances` | denorm |
| RetrieveURL | (0008,1190) | UR | — (WADO study URL) | synth |

> Under the original MVP (GROUP BY) plan, `ModalitiesInStudy` /
> `NumberOfStudyRelated*` were `ArrayAgg('Modality')` / `Count(...)` at query time.
> With the explicit `PACSStudy` (D2 recommendation) they are denormalized
> counters maintained at ingest (`stow_views._refresh_study_rollups`, and the
> find-or-create in `PACSSeriesSerializer.create` in a real checkout).

## Series level → `pacsfiles.PACSSeries`

| Attribute | Tag | VR | Model field | Source |
|---|---|---|---|---|
| SeriesInstanceUID | (0020,000E) | UI | `SeriesInstanceUID` | col (db_index) |
| Modality | (0008,0060) | CS | `Modality` | col (db_index, Phase A) |
| SeriesNumber | (0020,0011) | IS | `SeriesNumber` | col (Phase A) |
| SeriesDescription | (0008,103E) | LO | `SeriesDescription` | col |
| BodyPartExamined | (0018,0015) | CS | `BodyPartExamined` | col (Phase A) |
| Manufacturer | (0008,0070) | LO | `Manufacturer` | col (Phase A) |
| ProtocolName | (0018,1030) | LO | `ProtocolName` | col |
| PerformedProcedureStepStartDate | (0040,0244) | DA | `PerformedProcedureStepStartDate` | col (Phase A) |
| PerformedProcedureStepStartTime | (0040,0245) | TM | `PerformedProcedureStepStartTime` | col (Phase A) |
| StudyInstanceUID | (0020,000D) | UI | `StudyInstanceUID` | col (db_index, Phase A) |
| NumberOfSeriesRelatedInstances | (0020,1209) | IS | `Count('instances')` | agg |
| RetrieveURL | (0008,1190) | UR | — (WADO series URL) | synth |

"Phase A" marks the columns / indexes added by
`pacsfiles/migrations/0009_*` in the prior spike phase.

## Instance level → `dicomweb.PACSInstance`

| Attribute | Tag | VR | Model field | Source |
|---|---|---|---|---|
| SOPClassUID | (0008,0016) | UI | `SOPClassUID` | col (db_index) |
| SOPInstanceUID | (0008,0018) | UI | `SOPInstanceUID` | col (db_index) |
| InstanceNumber | (0020,0013) | IS | `InstanceNumber` | col |
| Rows | (0028,0010) | US | `Rows` | col |
| Columns | (0028,0011) | US | `Columns` | col |
| BitsAllocated | (0028,0100) | US | `BitsAllocated` | col |
| NumberOfFrames | (0028,0008) | IS | `NumberOfFrames` | col |
| TransferSyntaxUID | (0002,0010) | UI | `TransferSyntaxUID` | col |
| SeriesInstanceUID | (0020,000E) | UI | `series__SeriesInstanceUID` | join |
| StudyInstanceUID | (0020,000D) | UI | `series__StudyInstanceUID` | join |
| RetrieveURL | (0008,1190) | UR | — (WADO instance URL) | synth |
| PixelData (metadata) | (7FE0,0010) | OW/OB | — `BulkDataURI` → frames URL | synth |

## STOW-RS Store Instances Response attributes (PS3.18 §10.5.3.2)

| Attribute | Tag | VR | Built from |
|---|---|---|---|
| RetrieveURL (study) | (0008,1190) | UR | `RetrieveURLBuilder.study()` when ≥1 stored |
| FailedSOPSequence | (0008,1198) | SQ | per-failed-part items |
| ReferencedSOPSequence | (0008,1199) | SQ | per-stored-part items |
| ReferencedSOPClassUID | (0008,1150) | UI | parsed `ds.SOPClassUID` |
| ReferencedSOPInstanceUID | (0008,1155) | UI | parsed `ds.SOPInstanceUID` |
| FailureReason | (0008,1197) | US | `0xA700` / `0xC000` / `0xA901` (decimal in body) |
| RetrieveURL (instance) | (0008,1190) | UR | `RetrieveURLBuilder.instance()` per stored item |

---

## Matching semantics by VR (PS3.4 §C.2.2.2.4 / PS3.18 §10.6)

| VR class | VRs | Match types accepted by the parser |
|---|---|---|
| String (wildcard-eligible) | PN, LO, SH, CS, LT, ST, UT, UC, UR, AE | single value, wildcard (`*`→`%`, `?`→`_`, via `__iregex`), multi-value OR; PN also fuzzy (`pg_trgm __trigram_similar`) |
| UID | UI | single value, list-of-UID (`__in`) |
| Date/Time | DA, TM, DT | single value, inclusive range `v1-v2` (open-ended allowed) |
| Numeric | IS, US, SS, UL, SL, DS, FL, FD | single value (exact) |

Unsupported match keys (tags not in the level's map) are **ignored** for
filtering per the spec's allowance, but still honored for `includefield` when
they map to a returnable column. Malformed input (bad tag hex, bare-`-` range,
non-integer for an integer VR, wildcard on a numeric VR) → `QidoQueryError` →
HTTP 400.
