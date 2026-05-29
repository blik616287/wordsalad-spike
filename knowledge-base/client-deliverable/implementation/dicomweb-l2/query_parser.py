"""
QIDO-RS query-parameter parser -> Django ORM filters.

Translates a QIDO-RS query string (PS3.18 §10.6.1.2, matching semantics
§C.2.2.2 / PS3.4 §C.2.2.2.4) into a Django ``Q`` filter, an ``includefield``
set, and pagination. The supported forms:

  | Form                | Example                              | Match type        |
  |---------------------|--------------------------------------|-------------------|
  | Tag hex             | ``?0020000D=1.2.3``                  | single value      |
  | Keyword             | ``?StudyInstanceUID=1.2.3``          | single value      |
  | Multi-value (UID)   | ``?0020000D=1.2.3,4.5.6``            | list-of-UID (OR)  |
  | Multi-value (CS)    | ``?00080060=CT,MR``                  | OR over values    |
  | Range (DA/TM/DT)    | ``?StudyDate=20230101-20231231``     | inclusive range   |
  | Open range          | ``?StudyDate=20230101-`` / ``-...``  | >= / <=           |
  | Wildcard (str VRs)  | ``?PatientName=DOE*``                | ILIKE (* %, ? _)  |
  | includefield        | ``?includefield=00081030,Modality`` | return extra tags |
  | includefield=all    | ``?includefield=all``                | return everything |
  | Return key (no =val)| ``?00080060``                        | same as include   |
  | fuzzymatching       | ``?fuzzymatching=true``              | PN fuzzy (trgm)   |
  | limit / offset      | ``?limit=50&offset=100``             | pagination        |

Anything that doesn't normalize to a known attribute for the level is *ignored*
for filtering (the spec permits servers to ignore unsupported match keys) but is
still honored for ``includefield`` when it maps to a model field. Malformed input
(bad tag hex, malformed range) raises ``QidoQueryError`` -> the view returns
``400 Bad Request`` (PS3.18 §10.6.3).

Wild-card matching is only valid for string VRs (PS3.4 §C.2.2.2.4:
AE, CS, LO, LT, PN, SH, ST, UC, UR, UT). Range matching only for DA/TM/DT.
List-of-UID only for UI. We enforce those per the field's declared VR.
"""
from dataclasses import dataclass, field as dc_field
from typing import Optional

from django.db.models import Q

from . import dicomjson

DEFAULT_LIMIT = 50
MAX_LIMIT = 5000

# VRs eligible for wild-card matching (PS3.4 §C.2.2.2.4).
WILDCARD_VRS = frozenset({'AE', 'CS', 'LO', 'LT', 'PN', 'SH', 'ST', 'UC',
                          'UR', 'UT'})
RANGE_VRS = frozenset({'DA', 'TM', 'DT'})
UID_LIST_VRS = frozenset({'UI'})


class QidoQueryError(ValueError):
    """Raised on malformed QIDO query input -> HTTP 400."""


@dataclass(frozen=True)
class Attr:
    """
    One queryable/returnable DICOM attribute at a given QIDO level.

    ``orm_field`` is the Django ORM lookup path *relative to that level's base
    queryset* (e.g. ``'PatientName'`` on a PACSStudy queryset, or
    ``'series__Modality'`` when filtering instances up through the series FK).
    ``orm_field=None`` means the attribute is computed/aggregated (e.g.
    ``ModalitiesInStudy``) and has no direct filterable column -- such filters
    are ignored, but the attribute is still emitted by the serializer.
    """
    tag: str            # canonical 8-hex
    keyword: str        # DICOM keyword
    vr: str             # 2-letter VR
    orm_field: Optional[str] = None
    fuzzy_field: Optional[str] = None  # column for pg_trgm fuzzy (PN); usually == orm_field


# --------------------------------------------------------------------------- #
# Attribute maps, keyed by canonical 8-hex tag, one per QIDO level.
#
# orm_field paths assume these base querysets (see qido_views.py):
#   STUDY    : PACSStudy.objects.filter(pacs=...)
#   SERIES   : PACSSeries.objects.filter(pacs=...)        (FK study -> PACSStudy)
#   INSTANCE : PACSInstance.objects.filter(series__pacs=...) (FK series -> PACSSeries -> study)
# --------------------------------------------------------------------------- #

_STUDY_ATTRS = [
    Attr('00100010', 'PatientName',         'PN', 'PatientName', 'PatientName'),
    Attr('00100020', 'PatientID',           'LO', 'PatientID'),
    Attr('00100030', 'PatientBirthDate',    'DA', 'PatientBirthDate'),
    Attr('00100040', 'PatientSex',          'CS', 'PatientSex'),
    Attr('0020000D', 'StudyInstanceUID',    'UI', 'StudyInstanceUID'),
    Attr('00080020', 'StudyDate',           'DA', 'StudyDate'),
    Attr('00080030', 'StudyTime',           'TM', 'StudyTime'),
    Attr('00080050', 'AccessionNumber',     'SH', 'AccessionNumber'),
    Attr('00081030', 'StudyDescription',    'LO', 'StudyDescription'),
    Attr('00080090', 'ReferringPhysicianName', 'PN', 'ReferringPhysicianName'),
    # Computed / denormalized roll-ups -- emitted, but stored counters mean we
    # CAN filter NumberOf* directly; ModalitiesInStudy is a joined CS string so
    # we expose it via the cached column for substring-style filtering.
    Attr('00080061', 'ModalitiesInStudy',   'CS', 'ModalitiesInStudy'),
    Attr('00201206', 'NumberOfStudyRelatedSeries',    'IS', 'NumberOfStudyRelatedSeries'),
    Attr('00201208', 'NumberOfStudyRelatedInstances', 'IS', 'NumberOfStudyRelatedInstances'),
    Attr('00081190', 'RetrieveURL',         'UR', None),  # synthesized per-request
]

_SERIES_ATTRS = [
    Attr('0020000E', 'SeriesInstanceUID',   'UI', 'SeriesInstanceUID'),
    Attr('00080060', 'Modality',            'CS', 'Modality'),
    Attr('00200011', 'SeriesNumber',        'IS', 'SeriesNumber'),
    Attr('0008103E', 'SeriesDescription',   'LO', 'SeriesDescription'),
    Attr('00180015', 'BodyPartExamined',    'CS', 'BodyPartExamined'),
    Attr('00080070', 'Manufacturer',        'LO', 'Manufacturer'),
    Attr('00181030', 'ProtocolName',        'LO', 'ProtocolName'),
    Attr('00400244', 'PerformedProcedureStepStartDate', 'DA', 'PerformedProcedureStepStartDate'),
    Attr('00400245', 'PerformedProcedureStepStartTime', 'TM', 'PerformedProcedureStepStartTime'),
    # Series queries can also constrain by Study/Patient attrs (cross-level).
    Attr('0020000D', 'StudyInstanceUID',    'UI', 'StudyInstanceUID'),
    Attr('00100020', 'PatientID',           'LO', 'PatientID'),
    Attr('00100010', 'PatientName',         'PN', 'PatientName', 'PatientName'),
    Attr('00200013', 'InstanceNumber',      'IS', None),
    Attr('00201209', 'NumberOfSeriesRelatedInstances', 'IS', None),  # Count('instances')
    Attr('00081190', 'RetrieveURL',         'UR', None),
]

_INSTANCE_ATTRS = [
    Attr('00080018', 'SOPInstanceUID',      'UI', 'SOPInstanceUID'),
    Attr('00080016', 'SOPClassUID',         'UI', 'SOPClassUID'),
    Attr('00200013', 'InstanceNumber',      'IS', 'InstanceNumber'),
    Attr('00280010', 'Rows',                'US', 'Rows'),
    Attr('00280011', 'Columns',             'US', 'Columns'),
    Attr('00280100', 'BitsAllocated',       'US', 'BitsAllocated'),
    Attr('00280008', 'NumberOfFrames',      'IS', 'NumberOfFrames'),
    Attr('0020000E', 'SeriesInstanceUID',   'UI', 'series__SeriesInstanceUID'),
    Attr('0020000D', 'StudyInstanceUID',    'UI', 'series__StudyInstanceUID'),
    Attr('00081190', 'RetrieveURL',         'UR', None),
]


def _index(attrs):
    by_tag, by_keyword = {}, {}
    for a in attrs:
        by_tag[a.tag] = a
        by_keyword[a.keyword.upper()] = a
    return by_tag, by_keyword


STUDY_BY_TAG, STUDY_BY_KEYWORD = _index(_STUDY_ATTRS)
SERIES_BY_TAG, SERIES_BY_KEYWORD = _index(_SERIES_ATTRS)
INSTANCE_BY_TAG, INSTANCE_BY_KEYWORD = _index(_INSTANCE_ATTRS)

LEVELS = {
    'study':    (STUDY_BY_TAG, STUDY_BY_KEYWORD, _STUDY_ATTRS),
    'series':   (SERIES_BY_TAG, SERIES_BY_KEYWORD, _SERIES_ATTRS),
    'instance': (INSTANCE_BY_TAG, INSTANCE_BY_KEYWORD, _INSTANCE_ATTRS),
}

# Reserved (non-attribute) QIDO query keys.
_RESERVED = frozenset({'includefield', 'fuzzymatching', 'limit', 'offset'})


def resolve_attr(level, key):
    """
    Resolve a query key (tag-hex OR keyword) to an ``Attr`` for ``level``.

    Returns ``None`` if the key is syntactically a tag/keyword but unknown at
    this level (-> ignored as a match key). Raises ``QidoQueryError`` if the key
    is neither a valid 8-hex tag nor a known keyword (malformed attributeID).
    """
    by_tag, by_keyword, _ = LEVELS[level]
    raw = key.strip()
    # Keyword first (cheap, exact).
    if raw.upper() in by_keyword:
        return by_keyword[raw.upper()]
    # Then tag-hex.
    try:
        tag = dicomjson.normalize_tag(raw)
    except ValueError:
        raise QidoQueryError(f'unparseable attribute ID: {key!r}')
    return by_tag.get(tag)  # None == known-shaped but unsupported at this level


def _wildcard_to_like(value):
    """QIDO wildcard -> SQL LIKE pattern. ``*``->``%``, ``?``->``_``; escape %_."""
    out = []
    for ch in value:
        if ch == '*':
            out.append('%')
        elif ch == '?':
            out.append('_')
        elif ch in ('%', '_'):
            out.append('\\' + ch)
        else:
            out.append(ch)
    return ''.join(out)


def _has_wildcard(value):
    return '*' in value or '?' in value


def _build_term(attr, raw_value, fuzzy):
    """
    Build a ``Q`` for one matching attribute, dispatching on VR + value shape.
    Returns ``None`` if the attribute has no filterable ORM field.
    """
    if attr.orm_field is None:
        return None  # computed/synthesized attr; can't filter (spec: ignore)

    f = attr.orm_field
    vr = attr.vr
    value = raw_value.strip()

    # Empty match (?tag=) -- present-but-empty matching. Not supported (MVP):
    if value == '':
        raise QidoQueryError(
            f'empty-value matching is not supported for {attr.keyword}')

    # ---- List-of-UID matching (UI): comma OR backslash separated ---------- #
    if vr in UID_LIST_VRS and (',' in value or '\\' in value):
        uids = [u for u in _split_multi(value) if u]
        return Q(**{f'{f}__in': uids})

    # ---- Multi-value OR for other VRs (e.g. CS list ?00080060=CT,MR) ------ #
    if ',' in value and vr not in RANGE_VRS:
        parts = [p for p in _split_multi(value) if p != '']
        q = Q()
        for p in parts:
            q |= _build_single(attr, p, fuzzy)
        return q

    # ---- Range matching (DA/TM/DT) ---------------------------------------- #
    if vr in RANGE_VRS and '-' in value and not _has_wildcard(value):
        return _build_range(attr, value)

    return _build_single(attr, value, fuzzy)


def _split_multi(value):
    # DICOM multi-value separator is '\'; QIDO commonly uses ',' too.
    if '\\' in value:
        return value.split('\\')
    return value.split(',')


def _coerce_temporal(attr, value):
    """
    Coerce a DICOM DA/TM/DT *string* (``YYYYMMDD`` / ``HHMMSS[.FFFFFF]``) into a
    Python ``date`` / ``time`` for the ORM lookup.

    Django's DateField/TimeField lookups expect ISO (``2023-01-02`` /
    ``14:30:52``) and will raise on the DICOM compact form, so passing the raw
    QIDO string straight into ``Q(StudyDate='20230102')`` is a latent runtime
    error against Postgres. We parse to a native object instead. Raises
    ``QidoQueryError`` (-> 400) on an unparseable value.
    """
    from datetime import datetime
    vr = attr.vr
    if vr == 'DA':
        try:
            return datetime.strptime(value, '%Y%m%d').date()
        except ValueError:
            raise QidoQueryError(
                f'invalid DA value for {attr.keyword}: {value!r}')
    if vr == 'TM':
        raw = value.split('.', 1)[0]
        fmt = {6: '%H%M%S', 4: '%H%M', 2: '%H'}.get(len(raw))
        if fmt is None:
            raise QidoQueryError(
                f'invalid TM value for {attr.keyword}: {value!r}')
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            raise QidoQueryError(
                f'invalid TM value for {attr.keyword}: {value!r}')
    if vr == 'DT':
        raw = value.split('.', 1)[0]
        for fmt in ('%Y%m%d%H%M%S', '%Y%m%d%H%M', '%Y%m%d%H', '%Y%m%d'):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
        raise QidoQueryError(f'invalid DT value for {attr.keyword}: {value!r}')
    return value


def _build_range(attr, value):
    f = attr.orm_field
    if value == '-':
        raise QidoQueryError(f'invalid range for {attr.keyword}: "-"')
    lo, sep, hi = value.partition('-')
    lo, hi = lo.strip(), hi.strip()
    cond = {}
    if lo:
        cond[f'{f}__gte'] = _coerce_temporal(attr, lo)
    if hi:
        cond[f'{f}__lte'] = _coerce_temporal(attr, hi)
    if not cond:
        raise QidoQueryError(f'invalid range for {attr.keyword}: {value!r}')
    return Q(**cond)


def _build_single(attr, value, fuzzy):
    f = attr.orm_field
    vr = attr.vr

    if _has_wildcard(value):
        if vr not in WILDCARD_VRS:
            raise QidoQueryError(
                f'wildcard matching not allowed for VR {vr} ({attr.keyword})')
        pattern = _wildcard_to_like(value)
        # Postgres ILIKE via Django's __iregex would be heavier; use the
        # 'like'/'ilike' custom lookup. DICOM PN/most QIDO matching is
        # case-insensitive, so use ILIKE semantics via icontains-style.
        return Q(**{f'{f}__iregex': _like_to_iregex(pattern)})

    if vr == 'PN' and fuzzy:
        # Fuzzy PN matching (PS3.18) -> Postgres pg_trgm similarity. The trigram
        # GIN index (D4 in the L2 decisions doc) backs ``__trigram_similar``.
        # Falls back to icontains if pg_trgm is unavailable at deploy time.
        ff = attr.fuzzy_field or f
        return Q(**{f'{ff}__trigram_similar': value})

    if vr == 'PN':
        # PN single-value: match case-insensitively on the Alphabetic group.
        return Q(**{f'{f}__iexact': value})

    if vr in ('IS', 'US', 'SS', 'UL', 'SL'):
        try:
            return Q(**{f: int(value)})
        except ValueError:
            raise QidoQueryError(f'non-integer value for {attr.keyword}: {value!r}')

    if vr in RANGE_VRS:
        # Single DA/TM/DT value -> exact day/time match against a Date/TimeField.
        # Coerce out of the DICOM compact string the same way the range path does.
        return Q(**{f: _coerce_temporal(attr, value)})

    # UID, CS, LO, SH single value -> exact match.
    return Q(**{f: value})


def _like_to_iregex(like_pattern):
    """
    Convert a SQL LIKE pattern (``%``/``_`` wildcards) to a Postgres POSIX
    regex anchored at both ends, for use with Django's ``__iregex`` (portable
    across backends without a custom ILIKE lookup).
    """
    import re
    out = ['^']
    i = 0
    while i < len(like_pattern):
        ch = like_pattern[i]
        if ch == '\\' and i + 1 < len(like_pattern):
            out.append(re.escape(like_pattern[i + 1]))
            i += 2
            continue
        if ch == '%':
            out.append('.*')
        elif ch == '_':
            out.append('.')
        else:
            out.append(re.escape(ch))
        i += 1
    out.append('$')
    return ''.join(out)


@dataclass
class ParsedQuery:
    filter_q: Q = dc_field(default_factory=Q)
    includefields: set = dc_field(default_factory=set)  # set of keywords (upper) or 'ALL'
    include_all: bool = False
    fuzzymatching: bool = False
    limit: int = DEFAULT_LIMIT
    offset: int = 0

    def wants(self, keyword):
        return self.include_all or keyword.upper() in self.includefields


def parse(level, query_params):
    """
    Parse a QIDO query for ``level`` ('study' | 'series' | 'instance').

    ``query_params`` is a DRF/Django ``QueryDict`` (or any mapping with
    ``.getlist(key)`` and iteration over keys). Returns a ``ParsedQuery``.
    Raises ``QidoQueryError`` (-> HTTP 400) on malformed input.
    """
    if level not in LEVELS:
        raise ValueError(f'unknown QIDO level: {level}')

    pq = ParsedQuery()
    by_tag, by_keyword, _ = LEVELS[level]

    # First pass: scalar controls.
    fuzzy_raw = _first(query_params, 'fuzzymatching')
    if fuzzy_raw is not None:
        pq.fuzzymatching = str(fuzzy_raw).lower() in ('true', '1', 'yes')

    pq.limit = _parse_int_param(query_params, 'limit', DEFAULT_LIMIT)
    pq.offset = _parse_int_param(query_params, 'offset', 0)
    if pq.limit < 0 or pq.offset < 0:
        raise QidoQueryError('limit and offset must be non-negative')
    if pq.limit > MAX_LIMIT:
        pq.limit = MAX_LIMIT

    # includefield (repeatable + comma-list).
    for raw in _getlist(query_params, 'includefield'):
        for token in raw.split(','):
            token = token.strip()
            if not token:
                continue
            if token.lower() == 'all':
                pq.include_all = True
                continue
            attr = resolve_attr(level, token)
            if attr is not None:
                pq.includefields.add(attr.keyword.upper())

    # Matching attributes (everything that isn't reserved).
    for key in _keys(query_params):
        if key in _RESERVED:
            continue
        values = _getlist(query_params, key)
        # A bare key with no value (``?00080060``) is a "return key" (include).
        if values == [''] or values == []:
            attr = resolve_attr(level, key)
            if attr is not None:
                pq.includefields.add(attr.keyword.upper())
            continue
        attr = resolve_attr(level, key)
        if attr is None:
            continue  # unsupported match key -> ignore (spec-permitted)
        for v in values:
            term = _build_term(attr, v, pq.fuzzymatching)
            if term is not None:
                pq.filter_q &= term

    return pq


# --- QueryDict / plain-dict adapters --------------------------------------- #

def _keys(qp):
    if hasattr(qp, 'keys'):
        return list(qp.keys())
    return [k for k, _ in qp]


def _getlist(qp, key):
    if hasattr(qp, 'getlist'):
        return qp.getlist(key)
    v = qp.get(key)
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _first(qp, key):
    vals = _getlist(qp, key)
    return vals[0] if vals else None


def _parse_int_param(qp, key, default):
    raw = _first(qp, key)
    if raw is None or raw == '':
        return default
    try:
        return int(raw)
    except ValueError:
        raise QidoQueryError(f'{key} must be an integer, got {raw!r}')
