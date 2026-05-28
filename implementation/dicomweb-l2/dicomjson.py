"""
DICOM JSON Model (PS3.18 Annex F) encoding helpers.

The DICOM JSON Model is the on-the-wire format for QIDO-RS results, WADO-RS
metadata, and STOW-RS responses. Media type ``application/dicom+json``, UTF-8.
Spec: https://dicom.nema.org/medical/dicom/current/output/chtml/part18/sect_F.2.html

Rules implemented here (PS3.18 F.2.2):

  * A dataset is a JSON object keyed by the attribute's 8-char uppercase hex tag
    (``StudyInstanceUID`` (0020,000D) -> ``"0020000D"``; no comma, no ``0x``).
  * Each value is ``{"vr": <2-letter VR>, ...}`` with *at most one* of
    ``"Value"`` / ``"BulkDataURI"`` / ``"InlineBinary"``. "At most one", not
    "exactly one": an empty attribute is ``{"vr": "PN"}`` with none of the three.
  * Multiple datasets -> a top-level JSON *array* of these objects (F.2.1).

VR-specific encoding (the parts clients break on):

  * ``PN``  -> ``Value`` is an array of *objects*: ``[{"Alphabetic": "DOE^JANE"}]``
              (``Alphabetic``/``Ideographic``/``Phonetic`` = the three
              ``=``-separated component groups; ``^`` separators inside a group
              are preserved literally).
  * ``IS``  -> emitted as a JSON integer.
  * ``DS/FL/FD`` -> JSON number.
  * ``US/SS/UL/SL`` -> JSON integer.
  * ``DA/TM/DT`` -> the DICOM *string* form (``"20230102"``), NOT ISO-8601, even
              though the DB stores Python ``date``/``time``.
  * ``UI/SH/LO/CS/UR/...`` -> plain string in ``Value``.
  * Empty / ``None`` values -> the tag is *omitted* entirely (spec permits omit
    or ``{"vr": ...}``; omitting is smaller and OHIF handles it).

This module is intentionally framework-free (no Django, no DRF imports) so it is
unit-testable in isolation -- see ``tests/test_dicomjson.py``.
"""
from datetime import date, time, datetime

# VR groups for value coercion.
_INT_VRS = frozenset({'IS', 'US', 'SS', 'UL', 'SL', 'AT'})
_FLOAT_VRS = frozenset({'DS', 'FL', 'FD'})
_DATE_VRS = frozenset({'DA'})
_TIME_VRS = frozenset({'TM'})
_DATETIME_VRS = frozenset({'DT'})
_PN_VRS = frozenset({'PN'})

# VRs that carry bulk binary -- never inlined at the QIDO/metadata surface.
BULK_VRS = frozenset({'OB', 'OW', 'OF', 'OD', 'OL', 'OV', 'UN'})


def normalize_tag(tag):
    """
    Normalize a tag to canonical 8-char uppercase hex.

    Accepts ``"0020000D"``, ``"0020,000D"``, ``"(0020,000D)"``, ``"0020000d"``,
    or an int (``0x0020000D``). Raises ``ValueError`` on anything that is not a
    valid 8-hex tag -- callers translate that into a QIDO ``400 Bad Request``.
    """
    if isinstance(tag, int):
        return f'{tag:08X}'
    s = str(tag).strip().upper()
    s = s.replace('(', '').replace(')', '').replace(',', '').replace(' ', '')
    if len(s) != 8:
        raise ValueError(f'invalid DICOM tag: {tag!r}')
    int(s, 16)  # raises ValueError if not hex
    return s


def encode_pn(value):
    """Encode a Person Name value into the PN JSON object form."""
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    # A DICOM PN has up to three '='-separated component groups:
    # Alphabetic = Ideographic = Phonetic. The '^' inside a group
    # (family^given^middle^prefix^suffix) is preserved verbatim.
    groups = text.split('=')
    keys = ('Alphabetic', 'Ideographic', 'Phonetic')
    obj = {}
    for key, group in zip(keys, groups):
        if group != '':
            obj[key] = group
    return obj or None


def _coerce_date(value):
    if isinstance(value, datetime):
        return value.strftime('%Y%m%d')
    if isinstance(value, date):
        return value.strftime('%Y%m%d')
    s = str(value).strip()
    return s or None


def _coerce_time(value):
    if isinstance(value, (datetime, time)):
        # DICOM TM is HHMMSS(.FFFFFF); emit microseconds only if present.
        if getattr(value, 'microsecond', 0):
            return value.strftime('%H%M%S.%f')
        return value.strftime('%H%M%S')
    s = str(value).strip()
    return s or None


def _coerce_datetime(value):
    if isinstance(value, datetime):
        return value.strftime('%Y%m%d%H%M%S')
    s = str(value).strip()
    return s or None


def _coerce_scalar(vr, value):
    """Coerce a single (already non-None) scalar to its JSON-Model form."""
    if vr in _PN_VRS:
        return encode_pn(value)
    if vr in _INT_VRS:
        try:
            return int(value)
        except (TypeError, ValueError):
            return str(value)
    if vr in _FLOAT_VRS:
        try:
            return float(value)
        except (TypeError, ValueError):
            return str(value)
    if vr in _DATE_VRS:
        return _coerce_date(value)
    if vr in _TIME_VRS:
        return _coerce_time(value)
    if vr in _DATETIME_VRS:
        return _coerce_datetime(value)
    # UI, SH, LO, CS, UR, ST, LT, UT, AE, AS, ... -> plain string
    return str(value)


def element(vr, value):
    """
    Build a single DICOM-JSON attribute object from a VR and a raw Python value.

    ``value`` may be a scalar, a list/tuple (multi-valued), or ``None``/empty.
    Returns ``None`` when the attribute should be *omitted* (empty value), or a
    dict ``{"vr": vr}`` / ``{"vr": vr, "Value": [...]}``.

    Sequence (``SQ``) values are passed through assuming each item is already a
    DICOM-JSON dataset dict (see ``dataset()`` / ``sequence()``).
    """
    vr = vr.upper()

    if vr == 'SQ':
        items = value or []
        if not items:
            return None
        return {'vr': 'SQ', 'Value': list(items)}

    if vr in BULK_VRS:
        # Bulk binary is never inlined here. Callers that need pixel data use
        # BulkDataURI (see bulkdata_element); a bare bulk VR is emitted empty.
        return {'vr': vr}

    if value is None:
        return None

    # Normalize to a list of non-empty scalars.
    if isinstance(value, (list, tuple)):
        raw_values = list(value)
    else:
        raw_values = [value]

    coerced = []
    for v in raw_values:
        if v is None or v == '':
            continue
        c = _coerce_scalar(vr, v)
        if c is None or c == '':
            continue
        coerced.append(c)

    if not coerced:
        return None
    return {'vr': vr, 'Value': coerced}


def bulkdata_element(vr, uri):
    """A bulk attribute encoded as a ``BulkDataURI`` reference (WADO metadata)."""
    return {'vr': vr.upper(), 'BulkDataURI': uri}


def sequence(items):
    """Wrap a list of DICOM-JSON dataset dicts as an ``SQ`` Value list."""
    return {'vr': 'SQ', 'Value': list(items)}


def dataset(pairs):
    """
    Assemble a DICOM-JSON dataset object from ``(tag, vr, value)`` triples.

    Empty attributes (``element`` returns ``None``) are omitted. ``tag`` is
    normalized to canonical 8-hex. Later triples for the same tag win.
    """
    out = {}
    for tag, vr, value in pairs:
        key = normalize_tag(tag)
        el = element(vr, value)
        if el is not None:
            out[key] = el
    return out
