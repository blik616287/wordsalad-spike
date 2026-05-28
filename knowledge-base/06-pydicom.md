# pydicom: Reading & Writing DICOM in Python

> Practical reference for the DICOMweb implementation work in CUBE. Oriented to the three
> places we touch DICOM bytes on the Python side: **indexing** instance metadata for QIDO-RS,
> **parsing** uploaded objects in a STOW-RS handler, and **generating** synthetic `.dcm`
> fixtures for tests. Anchored to the patterns already shipped in Phase A
> (`chris_backend/dicomweb/tasks.py`).
>
> Primary source: pydicom 3.x documentation — <https://pydicom.github.io/>
> CUBE pins `pydicom>=3.0,<4.0` (`requirements/base.txt`); the installed version during
> Phase A validation was **3.0.2** (`proposal-to-bch/PHASE_A_IMPLEMENTATION.md` §8).

---

## 0. Why pydicom is in CUBE at all

CUBE historically did **no** DICOM parsing in Python: ingest is handled by **oxidicom** (a Rust
C-STORE SCP, <https://github.com/FNNDSC/oxidicom>) which writes `.dcm` files and pushes a small
amount of metadata. pydicom entered the dependency tree in Phase A purely to read `.dcm` *headers*
for the QIDO-RS metadata index. The DICOMweb work extends that to two more uses:

| Use | Where | What pydicom does | Reads pixels? |
|---|---|---|---|
| **Index** (Phase A, shipped) | `dicomweb/tasks.py::index_pacs_instance` | Read header → upsert `PACSInstance` row | No (`stop_before_pixels=True`) |
| **STOW-RS** (this spike) | new `dicomweb` view | Parse each `multipart/related` part → validate → store | Header only for validation |
| **Test fixtures** | `dicomweb/tests/` | Synthesize `FileDataset` objects → `.dcm` bytes | Optional |

WADO-RS *instance/series retrieval* (`multipart/related; type="application/dicom"`) streams the
stored bytes back unchanged and does **not** need pydicom (no re-encoding); it only needs the byte
stream and the stored `TransferSyntaxUID`. The WADO-RS **metadata** variant
(`/metadata`, `application/dicom+json`) is different: it must emit the DICOM JSON Model
(tag-keyed `{ "vr": ..., "Value": [...] }`), which means reading the header — that can be served
from the `PACSInstance`/`PACSSeries` index built in Phase A, or by re-reading the header with
pydicom. (<https://www.dicomlibrary.com/dicom/>, PS3.18 §10.4 / DICOM JSON Model.)

---

## 1. Reading: `dcmread`

`pydicom.dcmread(fp, defer_size=None, stop_before_pixels=False, force=False, specific_tags=None)`
reads a DICOM file and returns a **`FileDataset`** object.
(<https://pydicom.github.io/pydicom/stable/tutorials/dataset_basics.html>)

`fp` may be a path string, a `pathlib.Path`, or **any binary file-like object** — including an
in-memory `io.BytesIO`, which is exactly how Phase A feeds it bytes pulled from CUBE's storage
abstraction:

```python
import io, pydicom
raw = storage.download_obj(fname)            # bytes from fslink / swift / s3
ds = pydicom.dcmread(io.BytesIO(raw),
                     stop_before_pixels=True,  # header only
                     force=True)               # tolerate missing preamble
```
(verbatim shape from `chris_backend/dicomweb/tasks.py` lines 112-114; the surrounding
`try/except` that logs and drops on parse failure is lines 112-117.)

### 1.1 The parameters that matter for us

| Param | Effect | Why we use it |
|---|---|---|
| `stop_before_pixels=True` | Stop reading at the `(7FE0,0010) PixelData` element; pixels are **not** loaded. | A multi-MB `.dcm` has its full header in the first few KB. On cold S3 reads this is the difference between a few KB and several MB transferred. (`PHASE_A_IMPLEMENTATION.md` §1, design note on `stop_before_pixels`.) |
| `force=True` | Read files that lack the 128-byte preamble + `DICM` magic, and attempt Implicit VR Little Endian if no transfer syntax is present. | Some legacy modalities skip the preamble; oxidicom's output is not normalized for it. Without `force`, such files raise `InvalidDicomError`. (<https://pydicom.github.io/pydicom/stable/tutorials/dataset_basics.html>) |
| `defer_size` | Elements whose value exceeds this size are **not** read into memory until accessed; pydicom re-reads from `fp` on demand. | Useful if you keep the file handle open and only sometimes need large elements. Not used in Phase A because we read whole bytes into memory first (the `BytesIO` is discarded after the header is parsed). |
| `specific_tags` | Read **only** the listed tags (plus group-length and the tags needed to locate them). | A future optimization for the indexer: pass `specific_tags=['SOPInstanceUID','SOPClassUID','InstanceNumber','Rows','Columns','BitsAllocated','NumberOfFrames']` to skip everything else. |

> **Gotcha — `force=True` masks corruption.** With `force=True`, a non-DICOM blob (e.g. a JSON
> sidecar in the PACS tree) won't raise on `dcmread` itself but will produce a `Dataset` with no
> usable tags. Phase A defends against this twice: it skips files not ending in `.dcm`
> (`tasks.py` line 94), and it bails if `SOPInstanceUID` is absent (`tasks.py` lines 119-122).

### 1.2 What you get back: `FileDataset` vs `Dataset`

```
dcmread(...) ──► FileDataset
                 ├── .file_meta : FileMetaDataset   # group (0002,xxxx) — transfer syntax etc.
                 ├── .preamble  : bytes | None       # 128-byte preamble (None if force-read w/o it)
                 ├── .filename  : str | None
                 └── (inherits everything from Dataset)
                         ├── element access by keyword / tag
                         ├── Sequences (SQ) → list[Dataset]
                         └── .pixel_array (lazy, needs NumPy)
```
(<https://pydicom.github.io/pydicom/stable/tutorials/dataset_basics.html>)

- **`Dataset`** is the in-memory container of `DataElement`s (the actual image/patient/study tags).
- **`FileDataset`** is a `Dataset` subclass that *also* carries `file_meta`, `preamble`, and the
  source filename. `dcmread` always returns a `FileDataset`. A `Dataset` you build from scratch in
  a test is a plain `Dataset` until you wrap it (see §6).
- **`file_meta`** is itself a `FileMetaDataset` holding the group-`0002` elements, most importantly
  `TransferSyntaxUID (0002,0010)`. It is metadata *about the encoding*, not part of the image data
  set. Note that when `force=True` is used on a raw dataset with no preamble, `file_meta` may be
  empty or absent — which is why Phase A reads it defensively:

```python
transfer_syntax = ''
try:
    if ds.file_meta is not None:
        transfer_syntax = str(ds.file_meta.TransferSyntaxUID)
except AttributeError:
    pass
```
(`tasks.py` lines 124-129.)

---

## 2. Accessing elements: keyword vs tag

Every DICOM element has a **tag** `(group, element)` (two 16-bit hex numbers), a **VR** (Value
Representation — the type), and a **value**.
(<https://pydicom.github.io/pydicom/stable/tutorials/dataset_basics.html>)

```python
# By keyword — only works for standard (known) elements:
name = ds.PatientName                 # returns the value
elem = ds['PatientName']              # returns the DataElement (tag, VR, value)

# By tag — works for any element, including private ones:
elem  = ds[0x0010, 0x0010]            # DataElement
value = ds[0x0010, 0x0010].value      # the value itself

# Presence test (both forms work):
if 'PatientName' in ds: ...
if (0x0010, 0x0010) in ds: ...
```

### 2.1 `getattr` / `ds.get` — the safe accessors we rely on

Bare attribute access (`ds.PatientName`) **raises `AttributeError`** when the element is missing.
Real-world DICOM is wildly inconsistent about which optional tags are present, so Phase A never
uses bare access — it uses `getattr(ds, 'Keyword', default)` everywhere:

```python
SOPClassUID    = str(getattr(ds, 'SOPClassUID', '') or '')
InstanceNumber = _as_int(getattr(ds, 'InstanceNumber', None))
Rows           = _as_int(getattr(ds, 'Rows', None))
```
(`tasks.py` lines 137-141.)

`Dataset.get('Keyword', default)` is the dict-style equivalent and behaves the same. Prefer one of
these two over `ds.X` for **any optional tag**. Reserve bare `ds.X` for tags you have already
proven are present (e.g. right after an `if 'X' in ds` guard).

### 2.2 Iterating

```python
for elem in ds:                       # iterates DataElements in tag order
    print(elem.tag, elem.VR, elem.keyword, elem.value)
```

---

## 3. Sequences (VR `SQ`)

A Sequence element's value is a **list of nested `Dataset`s**. This is how DICOM models repeating
structures (referenced images, procedure steps, structured-report content, anonymization-relevant
`OtherPatientIDsSequence`, etc.).
(<https://pydicom.github.io/pydicom/stable/tutorials/dataset_basics.html>)

```python
seq = ds.ReferencedImageSequence       # list-like of Dataset items
for item in seq:                       # each item is a full Dataset
    print(item.ReferencedSOPInstanceUID)

len(seq)                               # number of items
seq[0].SomeNestedTag                   # drill into an item

# Build one from scratch (used in fixtures / anonymization):
from pydicom.dataset import Dataset
ds.OtherPatientIDsSequence = [Dataset(), Dataset()]
ds.OtherPatientIDsSequence[0].PatientID = 'X'
ds.OtherPatientIDsSequence.append(Dataset())
```

> **STOW/anonymization relevance:** PHI can hide *inside* sequences (e.g. `(0040,A730)
> ContentSequence` in SRs, `OtherPatientIDsSequence`). A flat tag pass misses them — you must
> recurse. See §7.

---

## 4. Pixel data: `pixel_array`

`Dataset.pixel_array` returns a NumPy `ndarray` of the decoded pixels. **It requires NumPy**, and
for *compressed* transfer syntaxes it requires a decoder plugin
(`pylibjpeg-libjpeg`, `pylibjpeg-openjpeg`, or `gdcm`) whose dependencies must be installed.
(<https://pydicom.github.io/pydicom/stable/guides/user/working_with_pixel_data.html>)

```python
arr = ds.pixel_array          # ndarray, shape (Rows, Columns) or (frames, Rows, Columns, ...)
arr[arr < 300] = 0            # manipulate
ds.PixelData = arr.tobytes()  # write back (uncompressed only — see warning)
ds.save_as("temp.dcm")
```

Key facts for our scope:

- **CUBE does not decode pixels** in the indexer or in WADO-RS retrieval. Phase A deliberately did
  **not** add `pylibjpeg`/`gdcm` (`PHASE_A_IMPLEMENTATION.md` §6, "deps considered and rejected"),
  because we only read headers and we stream stored bytes back unmodified.
- `pixel_array` is incompatible with `stop_before_pixels=True` (the pixels weren't read). If a
  future need arises (e.g. WADO-RS `/rendered` JPEG output), re-read **without** `stop_before_pixels`
  and add a decoder plugin.
- Writing pixels back from an `ndarray` is **not** round-trip-safe for compressed or multi-planar
  data — pydicom warns it "may not be as straightforward." If you change `arr` dimensions you must
  also update `Rows`/`Columns` yourself; pydicom does not.
  (<https://pydicom.github.io/pydicom/stable/guides/user/working_with_pixel_data.html>)

---

## 5. Writing: `save_as` / `dcmwrite`

Two equivalent entry points
(<https://pydicom.github.io/pydicom/stable/reference/generated/pydicom.filewriter.dcmwrite.html>):

```python
ds.save_as(dst, enforce_file_format=...)        # method on the Dataset
pydicom.dcmwrite(dst, ds, enforce_file_format=...)  # module-level function
```

`dst` may be a path or any writable binary file-like (`open(..., 'wb')`, or `io.BytesIO()` to get
the bytes back without touching disk — exactly what a STOW round-trip test or an in-memory store
needs).

### 5.1 `enforce_file_format` — the parameter to understand

| `enforce_file_format` | Behavior |
|---|---|
| `True` | Write a **conformant DICOM File Format** stream: 128-byte preamble, `DICM` magic, a complete File Meta Information group. **Raises `AttributeError` if `TransferSyntaxUID (0002,0010)` is missing or empty.** |
| `False` (default) | Write the dataset **as-is** after minimal validation; the result may or may not include the preamble / full file-meta, depending on what's already on the object. |
(<https://pydicom.github.io/pydicom/stable/reference/generated/pydicom.filewriter.dcmwrite.html>)

> pydicom 3.x replaced the old `write_like_original` argument with `enforce_file_format`
> (inverted sense: `enforce_file_format=True` ≈ `write_like_original=False`). Because CUBE pins
> `pydicom>=3.0,<4.0`, use `enforce_file_format`; do not copy 2.x snippets that pass
> `write_like_original`.

### 5.2 Minimum file_meta to produce a valid file

```python
from pydicom.dataset import FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

ds.file_meta = FileMetaDataset()
ds.file_meta.MediaStorageSOPClassUID    = ds.SOPClassUID
ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
ds.file_meta.TransferSyntaxUID          = ExplicitVRLittleEndian  # '1.2.840.10008.1.2.1'
ds.file_meta.ImplementationClassUID     = generate_uid()
ds.save_as('out.dcm', enforce_file_format=True)
```

Common transfer-syntax UIDs (from `pydicom.uid`):

| Constant | UID | Notes |
|---|---|---|
| `ImplicitVRLittleEndian` | `1.2.840.10008.1.2` | DICOM default; what `force` falls back to. |
| `ExplicitVRLittleEndian` | `1.2.840.10008.1.2.1` | Most common uncompressed. |
| `JPEGBaseline8Bit` | `1.2.840.10008.1.2.4.50` | Compressed; needs a decoder for `pixel_array`. |
| `JPEG2000` | `1.2.840.10008.1.2.4.90` | Compressed. |
(<https://pydicom.github.io/pydicom/>)

---

## 6. Snippet A — indexing instance metadata (the shipped Phase A pattern)

This is the canonical "read header, pull QIDO-relevant tags" flow. It is the template every other
DICOM-reading path in the DICOMweb work should follow.

```python
import io, pydicom
from datetime import datetime

def _as_int(value):
    if value in (None, ''):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

raw = storage.download_obj(fname)
try:
    ds = pydicom.dcmread(io.BytesIO(raw), stop_before_pixels=True, force=True)
except Exception:
    return  # parse failure won't get better — log and drop, do NOT retry

sop_instance_uid = getattr(ds, 'SOPInstanceUID', None)
if not sop_instance_uid:
    return  # not a usable instance

transfer_syntax = ''
try:
    if ds.file_meta is not None:
        transfer_syntax = str(ds.file_meta.TransferSyntaxUID)
except AttributeError:
    pass

PACSInstance.objects.update_or_create(
    series=series, SOPInstanceUID=str(sop_instance_uid),
    defaults=dict(
        SOPClassUID    = str(getattr(ds, 'SOPClassUID', '') or ''),
        InstanceNumber = _as_int(getattr(ds, 'InstanceNumber', None)),
        Rows           = _as_int(getattr(ds, 'Rows', None)),
        Columns        = _as_int(getattr(ds, 'Columns', None)),
        BitsAllocated  = _as_int(getattr(ds, 'BitsAllocated', None)),
        NumberOfFrames = _as_int(getattr(ds, 'NumberOfFrames', None)) or 1,
        TransferSyntaxUID = transfer_syntax,
    ),
)
```
(condensed from `chris_backend/dicomweb/tasks.py`; the `PACSInstance` model fields are in
`chris_backend/dicomweb/models.py`.)

Note the deliberate `str(...)` coercions: pydicom returns rich types (`PersonName`, `UID`,
`DSfloat`, `IS`), not plain `str`/`int` — see §8. The Django `CharField` columns want strings;
the `IntegerField` columns want ints, hence `_as_int`.

### 6.1 The DA / TM parsing gotcha (the bug Phase A's tests caught)

DICOM `DA` (Date) is `YYYYMMDD`; `TM` (Time) is `HHMMSS.FFFFFF` but **truncation is legal** — a
conformant value may be just `HH`, `HHMM`, or `HHMMSS` with optional fractional seconds.
(<https://www.dicomlibrary.com/dicom/>, PS3.5 VR definitions.)

The naive approach — looping over `'%H%M%S'`, `'%H%M'`, `'%H'` with `strptime` and taking the first
that doesn't raise — is **wrong**, because `strptime` matches each of `%H/%M/%S` as a 1-2 digit
greedy field: `datetime.strptime('1430', '%H%M%S')` parses as `H=14, M=3, S=0` (i.e. 14:03), not
14:30. Phase A's test suite caught exactly this regression
(`PHASE_A_IMPLEMENTATION.md` §7). The fix is to **dispatch the format on the input length**:

```python
def _parse_dicom_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value), '%Y%m%d').date()
    except (TypeError, ValueError):
        return None

def _parse_dicom_time(value):
    if not value:
        return None
    raw = str(value).split('.', 1)[0]          # strip fractional seconds
    fmt = {6: '%H%M%S', 4: '%H%M', 2: '%H'}.get(len(raw))
    if fmt is None:
        return None
    try:
        return datetime.strptime(raw, fmt).time()
    except ValueError:
        return None
```
(verbatim from `chris_backend/dicomweb/tasks.py` lines 39-62.)

> pydicom can do this for you with `pydicom.valuerep.DA` / `TM` (and
> `config.datetime_conversion = True` to get `datetime` objects directly), but Phase A keeps a
> hand-rolled parser to avoid a global config flag and to control the "return `None` on garbage"
> contract precisely. Match the existing helpers rather than introducing a parallel one.

---

## 7. Snippet B — a STOW-RS upload handler that parses DICOM

STOW-RS (`POST .../studies`, PS3.18) accepts a `multipart/related; type="application/dicom"` body:
one or more parts, each part the raw bytes of one DICOM instance.
(<https://www.dicomlibrary.com/dicom/>, PS3.18 Web Services.) The handler must split the multipart
body, parse each part with pydicom, validate, persist the bytes, and build the STOW-RS response
dataset — a `(0008,1199) ReferencedSOPSequence` of accepted instances and a `(0008,1198)
FailedSOPSequence` of rejected ones; each ReferencedSOP item carries a `(0008,1190) RetrieveURL`,
each FailedSOP item a `(0008,1197) FailureReason`.
(<https://dcm4chee-arc-cs.readthedocs.io/en/latest/networking/specs/stow-rs/stow-rs.html>) Skeleton:

```python
import io, pydicom

def handle_stow(request, pacs):
    """Parse a multipart/related DICOM upload; store each conformant instance."""
    parts = _split_multipart_related(request.body, request.content_type)  # boundary parse

    accepted, failed = [], []
    for part_bytes in parts:
        try:
            ds = pydicom.dcmread(io.BytesIO(part_bytes),
                                 stop_before_pixels=True,  # validate header; keep raw bytes intact
                                 force=False)              # STOW input MUST be conformant
        except Exception:
            failed.append(('—', 0xC000))                  # 0xC000 "Cannot understand" (unparseable)
            continue

        sop_class    = getattr(ds, 'SOPClassUID', None)
        sop_instance = getattr(ds, 'SOPInstanceUID', None)
        study_uid    = getattr(ds, 'StudyInstanceUID', None)
        if not (sop_class and sop_instance and study_uid):
            # 0xA900 "Data Set does not match SOP Class" — missing mandatory identifying attrs.
            # (0xA700 is "Out of Resources" — a storage/capacity failure, not a missing-attr error.)
            failed.append((str(sop_instance or '—'), 0xA900))
            continue

        # Persist the ORIGINAL bytes (do NOT re-encode — preserves transfer syntax & private tags)
        fname = f'{pacs.name}/{study_uid}/{sop_instance}.dcm'
        storage.upload_obj(fname, part_bytes)

        # Reuse the Phase A indexer so the new instance is immediately QIDO-queryable
        # (create PACSFile + PACSSeries as needed, then:)
        index_pacs_instance.delay(pacs_file.pk)

        accepted.append(sop_instance)

    return _build_stow_response(accepted, failed)  # application/dicom+json: ReferencedSOPSequence
                                                   # (0008,1199) + FailedSOPSequence (0008,1198)
```

Design points specific to STOW:

- **`force=False` on STOW input.** Unlike the indexer (which tolerates legacy files already in our
  store), a *new* upload should be rejected if it isn't conformant — that's a client error, and the
  failure should appear in the STOW Failed-SOP response, not be silently coerced.
- **Store the original bytes, never re-encode.** Round-tripping through `save_as` would re-serialize
  and could drop/alter private tags or change the transfer syntax. WADO-RS must return what was
  stored. Keep `part_bytes` verbatim; only *read* with pydicom for validation/indexing.
- **`stop_before_pixels=True` is fine for validation** — STOW doesn't require us to decode pixels,
  only to confirm the SOP Class/Instance/Study UIDs and basic conformance.
- **Reuse `index_pacs_instance`.** A STOW upload that lands a `PACSFile` should fan out to the same
  Celery indexer used by the existing PACS ingest path, so the instance is QIDO-discoverable without
  a second code path (`PHASE_A_IMPLEMENTATION.md` §"agnostic between QIDO, WADO, STOW").

> pydicom does not parse the HTTP multipart envelope for you — that's `_split_multipart_related`'s
> job (parse the `boundary=` from the `Content-Type`, split, strip each part's MIME sub-headers).
> Feed only the DICOM payload of each part to `dcmread`.

---

## 8. Value-representation (VR) gotchas

pydicom returns **typed** values, not plain Python primitives. Subtle bugs come from assuming
otherwise. (<https://pydicom.github.io/pydicom/stable/tutorials/dataset_basics.html>)

| VR | Meaning | pydicom type | Gotcha |
|---|---|---|---|
| `PN` | Person Name | `PersonName` | `str(ds.PatientName)` gives the raw `Family^Given^Middle^Prefix^Suffix`. Components via `.family_name`, `.given_name`, etc. Comparing a `PersonName` to a `str` works, but storing it needs `str(...)`. |
| `DA` | Date `YYYYMMDD` | `str` (or `DA`) | Truncation/empty allowed — parse defensively (§6.1). |
| `TM` | Time `HHMMSS.FFFFFF` | `str` (or `TM`) | Greedy-`strptime` bug (§6.1). |
| `DT` | DateTime | `str` (or `DT`) | Carries an optional `±ZZZZ` timezone suffix. |
| `UI` | Unique Identifier | `UID` (str subclass) | `.name` gives the human label (e.g. `'CT Image Storage'`); always `str()` before DB write. May have a trailing NUL — pydicom strips it, raw bytes may not. |
| `DS` | Decimal String | `DSfloat`/`DSdecimal` | Float-ish but string-backed; `_as_int`/`float()` to normalize. |
| `IS` | Integer String | `IS` (int subclass) | `InstanceNumber`, `SeriesNumber` — coerce via `_as_int` (handles `''`/`None`). |
| `SQ` | Sequence | `list[Dataset]` | See §3 — recurse for PHI/anonymization. |
| multi-valued | any VR with `\` | `MultiValue` (list-like) | e.g. `ImageType`. Indexing/`.insert()` work; `str()` of the whole thing joins oddly — handle per-element. |

The practical rule used throughout Phase A: **coerce at the boundary.** Wrap string-bound values in
`str(...)`, integer-bound values in `_as_int(...)`, and dates/times through the length-dispatched
parsers, before they hit the Django ORM.

### 8.1 Anonymization basics

There is no single "anonymize" call that is safe for all use cases; the documented building blocks
are direct element edits plus `remove_private_tags` and a recursive callback
(<https://pydicom.github.io/pydicom/>):

```python
ds.PatientName = 'Anonymous'
ds.PatientID   = 'ANON-0001'
for kw in ('PatientBirthDate', 'PatientAddress', 'OtherPatientIDs'):
    if kw in ds:
        delattr(ds, kw)        # or: ds[kw].value = ''

ds.remove_private_tags()       # drop all odd-group (private) elements

# Recurse into sequences — PHI hides in nested datasets:
def scrub(dataset):
    for elem in dataset:
        if elem.VR == 'SQ':
            for item in elem.value:
                scrub(item)
        elif elem.tag in PHI_TAGS:
            elem.value = ''
ds.walk(lambda dataset, elem: ...)   # pydicom's built-in recursive walker
```

> Anonymization is **out of scope** for the core DICOMweb endpoints but will come up at BCH
> (clinical data). Flag it as a follow-on, not a Phase requirement. UIDs that are referenced across
> instances (`StudyInstanceUID`, `SeriesInstanceUID`, frame-of-reference) must be *remapped
> consistently*, not blanked, or you break series/study grouping — a common anonymization footgun.

---

## 9. Snippet C — synthetic datasets for test fixtures

Tests should not depend on real PHI-bearing `.dcm` files. Build minimal synthetic instances in code.
This is the recommended approach for exercising the indexer and the STOW handler end-to-end.
(<https://pydicom.github.io/pydicom/stable/tutorials/dataset_basics.html>)

```python
import io
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid, CTImageStorage

def make_dicom_bytes(*, study_uid=None, series_uid=None, sop_uid=None,
                     modality='CT', instance_number=1,
                     rows=4, cols=4) -> bytes:
    """Return the bytes of a minimal but conformant single-frame DICOM instance."""
    study_uid  = study_uid  or generate_uid()
    series_uid = series_uid or generate_uid()
    sop_uid    = sop_uid    or generate_uid()

    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID    = CTImageStorage
    file_meta.MediaStorageSOPInstanceUID = sop_uid
    file_meta.TransferSyntaxUID          = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID     = generate_uid()

    ds = FileDataset(None, {}, file_meta=file_meta, preamble=b'\x00' * 128)

    # Patient / Study / Series / Instance identity
    ds.PatientName       = 'Test^Patient'
    ds.PatientID         = 'TEST-001'
    ds.StudyInstanceUID  = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.SOPInstanceUID    = sop_uid
    ds.SOPClassUID       = CTImageStorage
    ds.Modality          = modality
    ds.InstanceNumber    = instance_number
    ds.SeriesNumber      = 1
    ds.StudyDate         = '20260528'        # DA — 8 chars
    ds.StudyTime         = '1430'            # TM — 4 chars: exercises the truncation parser!
    ds.AccessionNumber   = 'ACC-001'

    # Pixel geometry (so the indexer fills Rows/Columns/BitsAllocated)
    ds.Rows = rows
    ds.Columns = cols
    ds.BitsAllocated = 16
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = 'MONOCHROME2'
    ds.PixelData = (b'\x00\x00' * (rows * cols))

    buf = io.BytesIO()
    ds.save_as(buf, enforce_file_format=True)   # pydicom 3.x arg (not write_like_original)
    return buf.getvalue()
```

Use it in a test:

```python
def test_index_extracts_metadata():
    raw = make_dicom_bytes(modality='MR', instance_number=7)
    ds  = pydicom.dcmread(io.BytesIO(raw), stop_before_pixels=True, force=True)
    assert ds.SOPInstanceUID
    assert _as_int(getattr(ds, 'InstanceNumber', None)) == 7
    assert _parse_dicom_time('1430').strftime('%H%M') == '1430'   # 14:30, not 14:03
```

Fixture design notes:

- Set `StudyTime = '1430'` in at least one fixture — it directly exercises the `_parse_dicom_time`
  truncation bug-class from §6.1. This is the single most valuable fixture for this codebase.
- `FileDataset(filename_or_obj, {}, file_meta=..., preamble=b'\x00'*128)` is the canonical
  constructor for a *file*-style dataset; the `{}` is the (empty) initial element dict.
- `generate_uid()` produces a valid, unique pydicom-rooted UID per call — perfect for distinct
  study/series/instance identities across fixture rows.
- `save_as(buf, enforce_file_format=True)` to an `io.BytesIO` gives you the bytes with **no disk
  I/O** — feed them straight to a fake storage backend or the STOW handler.
- pydicom also ships ready-made example datasets (`from pydicom import examples; examples.ct`,
  `examples.mr`) — handy for a quick read but they carry fixed UIDs, so prefer the synthetic builder
  when a test needs control over identity or the truncation edge cases.

---

## 10. Quick reference card

```
READ      pydicom.dcmread(fp, stop_before_pixels=True, force=True)  -> FileDataset
ACCESS    getattr(ds, 'Keyword', default)   ds[0x0010,0x0010].value   'X' in ds
META      ds.file_meta.TransferSyntaxUID  (guard with try/AttributeError)
SEQUENCE  ds.SomeSequence -> list[Dataset];  recurse for PHI
PIXELS    ds.pixel_array  (NumPy; compressed needs pylibjpeg/gdcm; CUBE does NOT decode)
WRITE     ds.save_as(dst, enforce_file_format=True)   # pydicom 3.x; needs file_meta.TransferSyntaxUID
DATES     length-dispatch strptime ({8:'%Y%m%d'}, {6/4/2:'%H%M%S'/'%H%M'/'%H'}) — NOT a try-loop
COERCE    str(...) for CharFields, _as_int(...) for IntegerFields — VR types aren't primitives
```

| Concern | Indexer (Phase A) | STOW handler | Fixtures |
|---|---|---|---|
| `force` | `True` (legacy tolerance) | `False` (reject non-conformant) | n/a (we write) |
| `stop_before_pixels` | `True` | `True` (validation only) | n/a |
| Re-encode bytes? | Never | **Never** (store original) | Yes (`save_as`) |
| Pixel decode? | No | No | Optional |

---

### Sources

- pydicom documentation home — <https://pydicom.github.io/>
- Dataset basics (read / access / modify / write) — <https://pydicom.github.io/pydicom/stable/tutorials/dataset_basics.html>
- `dcmread` reference (full signature: `defer_size`, `stop_before_pixels`, `force`, `specific_tags`) — <https://pydicom.github.io/pydicom/stable/reference/generated/pydicom.filereader.dcmread.html>
- `dcmwrite` / `enforce_file_format` reference — <https://pydicom.github.io/pydicom/stable/reference/generated/pydicom.filewriter.dcmwrite.html>
- STOW-RS Failure Reason codes (0xA900 / 0xC000 / 0xA700), ReferencedSOPSequence / FailedSOPSequence — <https://dcm4chee-arc-cs.readthedocs.io/en/latest/networking/specs/stow-rs/stow-rs.html>
- Working with pixel data — <https://pydicom.github.io/pydicom/stable/guides/user/working_with_pixel_data.html>
- DICOM standard (VRs, PS3.5/PS3.18) — <https://www.dicomlibrary.com/dicom/>
- CUBE Phase A code: `proposal-to-bch/code/source/chris_backend/dicomweb/tasks.py`, `.../dicomweb/models.py`
- CUBE Phase A writeup (pydicom pin, `stop_before_pixels`/`force` rationale, the DA/TM bug) — `proposal-to-bch/PHASE_A_IMPLEMENTATION.md`
- oxidicom (Rust ingest, why Python had no DICOM parsing pre-Phase A) — <https://github.com/FNNDSC/oxidicom>
