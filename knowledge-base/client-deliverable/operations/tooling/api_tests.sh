#!/usr/bin/env bash
# =============================================================================
# api_tests.sh -- explicit API-level validation of the deployed DICOMweb stack.
#
# Goes beyond verify/smoke.sh: exercises QIDO-RS, WADO-RS, STOW-RS and the
# relevant CUBE REST endpoints with real requests, asserting HTTP status AND
# response-body shape (DICOM JSON Model tags, multipart content types, STOW
# Referenced/Failed sequences). Covers BOTH authenticated paths (HTTP Basic and
# Token), unauthenticated negatives, and malformed-input negatives.
#
# Runs two ways:
#   - automatically at the end of the deploy (the `verify` role invokes it), and
#   - independently, against a live deployment:
#         operations/tooling/api_tests.sh
#         CUBE_USER=chris CUBE_PASSWORD=chris1234 PACS_ID=SPIKEORTHANC \
#           operations/tooling/api_tests.sh
#
# Exits non-zero if any test fails.
# =============================================================================
set -uo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Prefer the bootstrapped venv python (has requests + pydicom); fall back to system.
if [ -x "${OPS_DIR}/.venv/bin/python" ]; then PYBIN="${OPS_DIR}/.venv/bin/python"; else PYBIN="$(command -v python3)"; fi

BASE="${CUBE_BASE_URL:-http://localhost:8000}"
USER="${CUBE_USER:-chris}"; PW="${CUBE_PASSWORD:-chris1234}"
PACS_ID="${PACS_ID:-SPIKEORTHANC}"
ORTHANC_BASE_URL="${ORTHANC_BASE_URL:-http://localhost:8142}"
DW="${BASE}/dicom-web/pacs/${PACS_ID}"
AUTH=(-u "${USER}:${PW}")
PASS=0; FAIL=0; N=0

ok() { N=$((N+1)); PASS=$((PASS+1)); printf '  %-7s %-7s %-52s %s\n' "[PASS]" "$1" "$2" "$3"; }
no() { N=$((N+1)); FAIL=$((FAIL+1)); printf '  %-7s %-7s %-52s %s\n' "[FAIL]" "$1" "$2" "$3"; }
code() { local u="$1"; shift; curl -s -o /dev/null -w '%{http_code}' "$@" "$u" 2>/dev/null; }
body() { local u="$1"; shift; curl -s "$@" "$u" 2>/dev/null; }
hdr()  { printf '\n== %s ==\n' "$1"; }

echo "=============================================================================="
echo " DICOMweb-on-CUBE :: API test suite   (PACS=${PACS_ID})"
echo " base: ${BASE}    python: ${PYBIN}"
echo "=============================================================================="
printf '  %-7s %-7s %-52s %s\n' "RESULT" "METHOD" "ENDPOINT" "ASSERT"

hdr "CUBE REST API + auth"
c=$(code "${BASE}/api/v1/" "${AUTH[@]}"); [ "$c" = 200 ] && ok GET "/api/v1/ (basic auth)" "200" || no GET "/api/v1/ (basic)" "got $c"
# /api/v1/ is an intentionally public hypermedia root; assert auth on a PROTECTED resource.
c=$(code "${BASE}/api/v1/pacs/series/"); { [ "$c" = 401 ] || [ "$c" = 403 ]; } && ok GET "/pacs/series/ (no auth)" "$c (protected)" || no GET "/pacs/series/ (no auth)" "got $c want 401/403"
c=$(code "${BASE}/api/v1/auth-token/" -X POST -H 'Content-Type: application/json' --data "{\"username\":\"${USER}\",\"password\":\"${PW}\"}"); [ "$c" = 200 ] && ok POST "/auth-token/" "200 (issues token)" || no POST "/auth-token/" "got $c"
sj=$(body "${BASE}/api/v1/pacs/series/" "${AUTH[@]}" -H 'Accept: application/json')
n=$(printf '%s' "$sj" | grep -o 'SeriesInstanceUID' | wc -l | tr -d ' ')
[ "${n:-0}" -gt 0 ] && ok GET "/pacs/series/ (basic auth)" ">=1 ingested series (n=$n)" || no GET "/pacs/series/" "no series"

hdr "Authenticated paths on DICOMweb (Basic + Token)"
# Basic auth on QIDO
c=$(code "${DW}/studies" "${AUTH[@]}" -H 'Accept: application/dicom+json'); [ "$c" = 200 ] && ok GET "/studies (Basic auth)" "200" || no GET "/studies (Basic)" "got $c"
# Token auth: obtain a token, then drive QIDO + WADO with `Authorization: Token <t>`
TOK=$(body "${BASE}/api/v1/auth-token/" -X POST -H 'Content-Type: application/json' --data "{\"username\":\"${USER}\",\"password\":\"${PW}\"}" | "$PYBIN" -c "import sys,json;print(json.load(sys.stdin).get('token',''))" 2>/dev/null)
if [ -n "$TOK" ]; then
  c=$(code "${DW}/studies" -H "Authorization: Token ${TOK}" -H 'Accept: application/dicom+json'); [ "$c" = 200 ] && ok GET "/studies (Token auth)" "200" || no GET "/studies (Token)" "got $c"
else
  no GET "/studies (Token auth)" "could not obtain token"
fi
# Unauthenticated DICOMweb -> 401/403
c=$(code "${DW}/studies" -H 'Accept: application/dicom+json'); { [ "$c" = 401 ] || [ "$c" = 403 ]; } && ok GET "/studies (no auth)" "$c (rejected)" || no GET "/studies (no auth)" "got $c want 401/403"
# Bad credentials -> 401
c=$(code "${DW}/studies" -u "${USER}:wrongpw" -H 'Accept: application/dicom+json'); { [ "$c" = 401 ] || [ "$c" = 403 ]; } && ok GET "/studies (bad creds)" "$c (rejected)" || no GET "/studies (bad creds)" "got $c want 401/403"

hdr "QIDO-RS (query)"
c=$(code "${DW}/studies" "${AUTH[@]}" -H 'Accept: application/dicom+json'); [ "$c" = 200 ] && ok GET "/studies" "200 dicom+json" || no GET "/studies" "got $c"
SUID=$(body "${DW}/studies" "${AUTH[@]}" -H 'Accept: application/dicom+json' | "$PYBIN" -c "import sys,json;d=json.load(sys.stdin);print(d[0]['0020000D']['Value'][0])" 2>/dev/null)
[ -n "$SUID" ] && ok GET "/studies" "body: 0020000D present" || no GET "/studies" "no StudyInstanceUID"
c=$(code "${DW}/studies" "${AUTH[@]}" -H 'Accept: application/json'); [ "$c" = 200 ] && ok GET "/studies (Accept json)" "200 (json==dicom+json)" || no GET "/studies (json)" "got $c"
c=$(code "${DW}/studies?ModalitiesInStudy=MR&limit=5" "${AUTH[@]}" -H 'Accept: application/dicom+json'); [ "$c" = 200 ] && ok GET "/studies?ModalitiesInStudy=MR" "200 filtered" || no GET "/studies?Modalities" "got $c"
c=$(code "${DW}/studies?PatientName=*" "${AUTH[@]}" -H 'Accept: application/dicom+json'); { [ "$c" = 200 ] || [ "$c" = 400 ]; } && ok GET "/studies?PatientName=*" "$c (wildcard)" || no GET "/studies?PatientName=*" "got $c"
c=$(code "${DW}/studies/${SUID}/series" "${AUTH[@]}" -H 'Accept: application/dicom+json'); [ "$c" = 200 ] && ok GET "/studies/<uid>/series" "200" || no GET "/studies/<uid>/series" "got $c"
c=$(code "${DW}/studies/${SUID}/instances" "${AUTH[@]}" -H 'Accept: application/dicom+json'); [ "$c" = 200 ] && ok GET "/studies/<uid>/instances" "200" || no GET "/studies/<uid>/instances" "got $c"
c=$(code "${DW}/series" "${AUTH[@]}" -H 'Accept: application/dicom+json'); [ "$c" = 200 ] && ok GET "/series (cross-study)" "200" || no GET "/series" "got $c"
c=$(code "${DW}/instances" "${AUTH[@]}" -H 'Accept: application/dicom+json'); [ "$c" = 200 ] && ok GET "/instances (cross-study)" "200" || no GET "/instances" "got $c"
c=$(code "${DW}/studies?StudyDate=-" "${AUTH[@]}" -H 'Accept: application/dicom+json'); [ "$c" = 400 ] && ok GET "/studies?StudyDate=- (bad)" "400" || no GET "/studies?StudyDate=-" "got $c want 400"
c=$(code "${BASE}/dicom-web/pacs/NOPE/studies" "${AUTH[@]}" -H 'Accept: application/dicom+json'); [ "$c" = 404 ] && ok GET "/pacs/NOPE/studies" "404 (unknown PACS)" || no GET "/pacs/NOPE/studies" "got $c want 404"

hdr "WADO-RS (retrieve)"
c=$(code "${DW}/studies/${SUID}/metadata" "${AUTH[@]}" -H 'Accept: application/dicom+json'); [ "$c" = 200 ] && ok GET "/studies/<uid>/metadata" "200 dicom+json" || no GET "/studies/<uid>/metadata" "got $c"
ninst=$(body "${DW}/studies/${SUID}/metadata" "${AUTH[@]}" -H 'Accept: application/dicom+json' | "$PYBIN" -c "import sys,json;print(len(json.load(sys.stdin)))" 2>/dev/null)
[ "${ninst:-0}" -gt 0 ] && ok GET "/studies/<uid>/metadata" "body: $ninst instances" || no GET "/studies/<uid>/metadata" "empty"
read -r SER SOP < <(body "${DW}/studies/${SUID}/instances" "${AUTH[@]}" -H 'Accept: application/dicom+json' | "$PYBIN" -c "import sys,json;d=json.load(sys.stdin);print(d[0]['0020000E']['Value'][0], d[0]['00080018']['Value'][0])" 2>/dev/null)
if [ -n "${SOP:-}" ]; then
  c=$(code "${DW}/studies/${SUID}/series/${SER}/metadata" "${AUTH[@]}" -H 'Accept: application/dicom+json'); [ "$c" = 200 ] && ok GET ".../series/<s>/metadata" "200" || no GET "series metadata" "got $c"
  c=$(code "${DW}/studies/${SUID}/series/${SER}/instances/${SOP}/metadata" "${AUTH[@]}" -H 'Accept: application/dicom+json'); [ "$c" = 200 ] && ok GET ".../instances/<sop>/metadata" "200" || no GET "instance metadata" "got $c"
  ct=$(curl -s -D - -o /dev/null "${DW}/studies/${SUID}/series/${SER}/instances/${SOP}" "${AUTH[@]}" -H 'Accept: multipart/related; type="application/dicom"' 2>/dev/null | tr -d '\r' | awk -F': ' 'tolower($1)=="content-type"{print $2}')
  echo "$ct" | grep -qi 'multipart/related' && ok GET ".../instances/<sop>" "multipart/related" || no GET "instance retrieve" "ct=$ct"
  c=$(code "${DW}/studies/${SUID}/series/${SER}/instances/${SOP}/frames/1" "${AUTH[@]}" -H 'Accept: multipart/related; type="application/octet-stream"'); { [ "$c" = 200 ] || [ "$c" = 501 ]; } && ok GET ".../frames/1" "$c (native 200 / encap 501)" || no GET "frames/1" "got $c"
else
  no GET "WADO instance-level" "could not extract SOPInstanceUID"
fi

hdr "Data integrity (full ingested set processed + stored correctly)"
# Source of truth = the test Orthanc the sample set was loaded into. Verify the
# WHOLE set flowed source -> oxidicom ingest -> index -> retrievable, by comparing
# counts across the pipeline (not hard-coded to 384 -- derived from the source so
# it holds for any dataset), then byte-verify one retrieved object is valid DICOM.
SRC_N=$(body "${ORTHANC_BASE_URL}/instances" | "$PYBIN" -c "import sys,json;print(len(json.load(sys.stdin)))" 2>/dev/null)
# QIDO paginates (default page) -- pass an explicit limit to count the full set.
QIDO_N=$(body "${DW}/studies/${SUID}/instances?limit=100000" "${AUTH[@]}" -H 'Accept: application/dicom+json' | "$PYBIN" -c "import sys,json;print(len(json.load(sys.stdin)))" 2>/dev/null)
WADO_N=$(body "${DW}/studies/${SUID}/metadata" "${AUTH[@]}" -H 'Accept: application/dicom+json' | "$PYBIN" -c "import sys,json;print(len(json.load(sys.stdin)))" 2>/dev/null)
if [ -n "${SRC_N:-}" ] && [ "${SRC_N:-0}" -gt 0 ]; then
  ok GET "source Orthanc /instances" "source set = ${SRC_N} instances"
  [ "${QIDO_N:-0}" = "${SRC_N}" ] && ok GET "QIDO instance count (limit)" "${QIDO_N} == source ${SRC_N} (all ingested+indexed)" || no GET "QIDO instance count" "got ${QIDO_N} want ${SRC_N}"
  [ "${WADO_N:-0}" = "${SRC_N}" ] && ok GET "WADO metadata count" "${WADO_N} == source ${SRC_N} (all retrievable)" || no GET "WADO metadata count" "got ${WADO_N} want ${SRC_N}"
else
  no GET "source Orthanc /instances" "could not read source count"
fi
# 3D-volume completeness: the 384 instances are the slices of ONE volume. Verify
# they form a single series of single-frame instances with a complete (gap-free)
# InstanceNumber set -- i.e. the whole 3D volume reconstructs with no missing slice.
vol=$(body "${DW}/studies/${SUID}/metadata" "${AUTH[@]}" -H 'Accept: application/dicom+json' | "$PYBIN" -c "
import sys, json
d = json.load(sys.stdin)
def val(o, tag):
    v = o.get(tag, {}).get('Value', [None]); return v[0] if v else None
series = {val(o,'0020000E') for o in d}
nframes = {int(val(o,'00280008') or 1) for o in d}     # NumberOfFrames
nums = sorted(int(val(o,'00200013')) for o in d if val(o,'00200013') is not None)  # InstanceNumber
n = len(d); uniq = len(set(nums))
contiguous = bool(nums) and uniq == n and (max(nums) - min(nums) + 1) == n
single_frame = (nframes <= {1})
print(f'{len(series)} {single_frame and 1 or 0} {n} {uniq} {min(nums) if nums else 0} {max(nums) if nums else 0} {contiguous and 1 or 0}')
" 2>/dev/null)
read -r nser sframe ninst nuniq imin imax contig <<<"$vol"
[ "${nser:-0}" = 1 ] && ok GET "3D volume: single series" "1 series holds all ${ninst} slices" || no GET "3D volume: single series" "got ${nser} series"
[ "${sframe:-0}" = 1 ] && ok GET "3D volume: single-frame slices" "every instance NumberOfFrames<=1 (slice stack, not multi-frame)" || no GET "3D volume: single-frame" "mixed/multi-frame"
{ [ "${contig:-0}" = 1 ] && [ "${nuniq:-0}" = "${ninst:-0}" ]; } && ok GET "3D volume: complete slice set" "InstanceNumbers ${imin}..${imax}, ${nuniq} unique, gap-free (no missing slice)" || no GET "3D volume: complete slice set" "uniq=$nuniq n=$ninst range=$imin..$imax contiguous=$contig"
# Byte-level integrity: WADO-retrieve one full object, parse it as DICOM, confirm
# the SOPInstanceUID round-trips and PixelData is present (not truncated).
if [ -n "${SOP:-}" ]; then
  integ=$("$PYBIN" - "${DW}/studies/${SUID}/series/${SER}/instances/${SOP}" "$USER" "$PW" "$SOP" <<'PY'
import sys, io, requests, pydicom
url,u,p,want = sys.argv[1],sys.argv[2],sys.argv[3],sys.argv[4]
r = requests.get(url, auth=(u,p), headers={'Accept':'multipart/related; type="application/dicom"'})
raw = r.content
# strip multipart/related framing: take bytes between the first blank line and the closing boundary
b = raw.split(b'\r\n\r\n',1)[1]
b = b.rsplit(b'\r\n--',1)[0]
try:
    ds = pydicom.dcmread(io.BytesIO(b))
    sop_ok = (str(ds.SOPInstanceUID) == want)
    has_px = 'PixelData' in ds and len(ds.PixelData) > 0
    print(f"{r.status_code} {int(sop_ok)} {int(has_px)} {len(b)}")
except Exception as e:
    print(f"{r.status_code} 0 0 parse-error:{e}")
PY
)
  read -r rc sop_ok has_px blen <<<"$integ"
  { [ "$rc" = 200 ] && [ "${sop_ok:-0}" = 1 ] && [ "${has_px:-0}" = 1 ]; } \
    && ok GET "WADO object byte-integrity" "valid DICOM, SOPInstanceUID matches, PixelData present (${blen}B)" \
    || no GET "WADO object byte-integrity" "rc=$rc sop_ok=$sop_ok has_px=$has_px ($blen)"
else
  no GET "WADO object byte-integrity" "no SOPInstanceUID available"
fi

hdr "STOW-RS (store)"
# Mint a FRESH-UID object (new Study/Series/SOP) so the store path is exercised
# cleanly, without colliding with already-ingested data. Then confirm it queries.
DCM=$(find "$HOME/.cache/dicomweb-spike/sample-data" -name '*.dcm' 2>/dev/null | head -1)
if [ -n "$DCM" ]; then
  res=$("$PYBIN" - "$DCM" "$DW" "$USER" "$PW" <<'PY'
import sys, requests, pydicom
from pydicom.uid import generate_uid
src, dw, u, p = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
ds = pydicom.dcmread(src)
new_study = generate_uid()
ds.StudyInstanceUID = new_study
ds.SeriesInstanceUID = generate_uid()
ds.SOPInstanceUID = generate_uid()
ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
import io; buf = io.BytesIO(); ds.save_as(buf); dcm = buf.getvalue()
b = 'STOWFRESH'
body = (f'--{b}\r\n'.encode() + b'Content-Type: application/dicom\r\n\r\n' + dcm + b'\r\n' + f'--{b}--\r\n'.encode())
r = requests.post(dw + '/studies', data=body, auth=(u, p),
                  headers={'Content-Type': f'multipart/related; type="application/dicom"; boundary={b}',
                           'Accept': 'application/dicom+json'})
j = {}
try: j = r.json()
except Exception: pass
refs = len(j.get('00081199', {}).get('Value', []))
fails = len(j.get('00081198', {}).get('Value', []))
# round-trip: query the newly-stored study back via QIDO
q = requests.get(f'{dw}/studies', params={'StudyInstanceUID': new_study}, auth=(u, p),
                 headers={'Accept': 'application/dicom+json'})
found = 0
try: found = sum(1 for s in q.json() if s.get('0020000D', {}).get('Value', [None])[0] == new_study)
except Exception: pass
print(f"{r.status_code} {refs} {fails} {q.status_code} {found}")
PY
)
  read -r scode refs fails qcode found <<<"$res"
  { [ "$scode" = 200 ] || [ "$scode" = 202 ]; } && [ "${refs:-0}" -ge 1 ] && [ "${fails:-1}" = 0 ] \
    && ok POST "/studies (fresh object)" "$scode, ReferencedSOP=$refs Failed=$fails" \
    || no POST "/studies (fresh object)" "code=$scode refs=$refs fails=$fails"
  { [ "$qcode" = 200 ] && [ "${found:-0}" -ge 1 ]; } \
    && ok GET "/studies?StudyInstanceUID=<new>" "round-trip: stored study queryable" \
    || no GET "STOW round-trip query" "qcode=$qcode found=$found"
else
  no POST "/studies (fresh object)" "no sample .dcm found"
fi
# Multipart series ingestion: POST N instances of ONE new series in a SINGLE
# multipart/related body (one request) -> all stored -> one series with N
# instances. Validates multi-part-body STOW + multiple-series handling.
if [ -n "$DCM" ]; then
  ms=$("$PYBIN" - "$DW" "$USER" "$PW" "$HOME/.cache/dicomweb-spike/sample-data" <<'PY'
import sys, io, glob, requests, pydicom
from pydicom.uid import generate_uid
dw,u,p,root = sys.argv[1],sys.argv[2],sys.argv[3],sys.argv[4]
files = sorted(glob.glob(root+'/**/*.dcm', recursive=True))[:4]
study, series = generate_uid(), generate_uid()
b='MSERIES'; parts=[]
for f in files:
    ds = pydicom.dcmread(f)
    ds.StudyInstanceUID = study; ds.SeriesInstanceUID = series
    ds.SOPInstanceUID = generate_uid(); ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    buf=io.BytesIO(); ds.save_as(buf); parts.append(buf.getvalue())
body=b''
for blob in parts:
    body += f'--{b}\r\n'.encode()+b'Content-Type: application/dicom\r\n\r\n'+blob+b'\r\n'
body += f'--{b}--\r\n'.encode()
r=requests.post(dw+'/studies', data=body, auth=(u,p),
    headers={'Content-Type':f'multipart/related; type="application/dicom"; boundary={b}','Accept':'application/dicom+json'})
refs=len(r.json().get('00081199',{}).get('Value',[])) if r.ok else 0
# QIDO: the new series should report N instances
q=requests.get(f'{dw}/studies/{study}/series', auth=(u,p), headers={'Accept':'application/dicom+json'})
nser = len(q.json()) if q.ok else 0
qi=requests.get(f'{dw}/studies/{study}/instances?limit=1000', auth=(u,p), headers={'Accept':'application/dicom+json'})
ninst = len(qi.json()) if qi.ok else 0
print(f"{r.status_code} {refs} {len(parts)} {nser} {ninst}")
PY
)
  read -r sc refs sent nser ninst <<<"$ms"
  { { [ "$sc" = 200 ] || [ "$sc" = 202 ]; } && [ "${refs:-0}" = "${sent:-X}" ]; } \
    && ok POST "STOW multipart series (${sent} parts/1 request)" "$sc, ReferencedSOP=$refs (all stored)" \
    || no POST "STOW multipart series" "code=$sc refs=$refs sent=$sent"
  { [ "${nser:-0}" = 1 ] && [ "${ninst:-0}" = "${sent:-X}" ]; } \
    && ok GET "QIDO new series" "1 series, ${ninst} instances (multipart series ingested)" \
    || no GET "QIDO new series" "series=$nser instances=$ninst want 1/$sent"
fi
# endpoint present: empty body -> 400/409/415 (not 404)
c=$(code "${DW}/studies" "${AUTH[@]}" -X POST -H 'Content-Type: multipart/related; type="application/dicom"; boundary=X' --data-binary ''); case "$c" in 400|409|415) ok POST "/studies (empty body)" "$c (rejects cleanly)";; 404) no POST "/studies (empty)" "404 missing";; *) no POST "/studies (empty)" "got $c";; esac
# wrong content-type -> 415
c=$(code "${DW}/studies" "${AUTH[@]}" -X POST -H 'Content-Type: application/json' --data '{}'); [ "$c" = 415 ] && ok POST "/studies (bad media type)" "415" || no POST "/studies (bad media)" "got $c want 415"

hdr "Multi-frame representation (1x384: the SAME volume as one object)"
# The 384-slice volume has two valid DICOM encodings; we already validated the
# 384x1 (slice-per-instance) form above. Here we prove the 1x384 (multi-frame)
# form: assemble the slices into ONE instance with NumberOfFrames=384, STOW it,
# and retrieve individual frames via WADO-RS /frames/{n}. Same model + same WADO
# slicer serve both. Set API_TESTS_MULTIFRAME=0 to skip (it builds a large object).
if [ "${API_TESTS_MULTIFRAME:-1}" = 1 ] && [ -d "$HOME/.cache/dicomweb-spike/sample-data" ]; then
  mf=$("$PYBIN" - "$DW" "$USER" "$PW" "$HOME/.cache/dicomweb-spike/sample-data" <<'PY'
import sys, io, glob, requests, pydicom
from pydicom.uid import generate_uid, ExplicitVRLittleEndian
dw,u,p,root = sys.argv[1],sys.argv[2],sys.argv[3],sys.argv[4]
files = sorted(glob.glob(root+'/**/*.dcm', recursive=True))
slices = [pydicom.dcmread(f) for f in files]
slices = [s for s in slices if 'PixelData' in s]
slices.sort(key=lambda s: int(getattr(s,'InstanceNumber',0) or 0))
t = slices[0]
rows,cols = int(t.Rows), int(t.Columns)
ba = int(t.BitsAllocated); spp = int(getattr(t,'SamplesPerPixel',1))
fsz = rows*cols*spp*((ba+7)//8)
frames = [bytes(s.PixelData)[:fsz] for s in slices if len(bytes(s.PixelData))>=fsz]
nf = len(frames)
ds = pydicom.Dataset()
for tag in ['PatientID','PatientName','StudyDate','Modality','Rows','Columns','BitsAllocated','BitsStored','HighBit','PixelRepresentation','SamplesPerPixel','PhotometricInterpretation']:
    if tag in t: setattr(ds, tag, getattr(t, tag))
ds.StudyInstanceUID = generate_uid(); ds.SeriesInstanceUID = generate_uid(); ds.SOPInstanceUID = generate_uid()
ds.SOPClassUID = '1.2.840.10008.5.1.4.1.1.4.1'   # Enhanced MR Image Storage (multi-frame)
ds.NumberOfFrames = nf
ds.PixelData = b''.join(frames)
ds.file_meta = pydicom.dataset.FileMetaDataset()
ds.file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
ds.is_little_endian = True; ds.is_implicit_VR = False
buf = io.BytesIO(); ds.save_as(buf, enforce_file_format=True); blob = buf.getvalue()
b='MF'; body=(f'--{b}\r\n'.encode()+b'Content-Type: application/dicom\r\n\r\n'+blob+b'\r\n'+f'--{b}--\r\n'.encode())
r = requests.post(dw+'/studies', data=body, auth=(u,p),
    headers={'Content-Type':f'multipart/related; type="application/dicom"; boundary={b}','Accept':'application/dicom+json'})
stored = len(r.json().get('00081199',{}).get('Value',[])) if r.ok else 0
# QIDO: the new study has exactly ONE instance, NumberOfFrames == nf
meta = requests.get(f'{dw}/studies/{ds.StudyInstanceUID}/metadata', auth=(u,p), headers={'Accept':'application/dicom+json'})
md = meta.json() if meta.ok else []
ninst = len(md)
mf_frames = int(md[0].get('00280008',{}).get('Value',[0])[0]) if md else 0
# WADO frames: frame 1 and last frame -> 200 + exactly fsz bytes; out-of-range -> 404
def frame_len(n):
    fr = requests.get(f'{dw}/studies/{ds.StudyInstanceUID}/series/{ds.SeriesInstanceUID}/instances/{ds.SOPInstanceUID}/frames/{n}',
                      auth=(u,p), headers={'Accept':'multipart/related; type="application/octet-stream"'})
    if fr.status_code!=200: return fr.status_code,-1
    raw=fr.content; part=raw.split(b'\r\n\r\n',1)[1].rsplit(b'\r\n--',1)[0]
    return 200, len(part)
c1,l1 = frame_len(1); cN,lN = frame_len(nf); cOOB,_ = frame_len(nf+1)
print(f"{r.status_code} {stored} {ninst} {mf_frames} {nf} {fsz} {c1} {l1} {cN} {lN} {cOOB}")
PY
)
  read -r sc stored ninst mfn nf fsz c1 l1 cN lN coob <<<"$mf"
  { { [ "$sc" = 200 ] || [ "$sc" = 202 ]; } && [ "${stored:-0}" -ge 1 ]; } \
    && ok POST "STOW multi-frame object" "$sc, stored 1 instance carrying $nf frames" \
    || no POST "STOW multi-frame object" "code=$sc stored=$stored"
  { [ "${ninst:-0}" = 1 ] && [ "${mfn:-0}" = "${nf:-X}" ]; } \
    && ok GET "QIDO: 1x$nf (one instance)" "1 instance, NumberOfFrames=$mfn (whole volume in one object)" \
    || no GET "QIDO multi-frame" "ninst=$ninst NumberOfFrames=$mfn want $nf"
  { [ "${c1:-0}" = 200 ] && [ "${l1:-0}" = "${fsz:-X}" ] && [ "${cN:-0}" = 200 ] && [ "${lN:-0}" = "${fsz:-X}" ]; } \
    && ok GET "WADO /frames/1 + /frames/$nf" "200, each frame = $fsz bytes (native slice extracted)" \
    || no GET "WADO multi-frame frames" "f1=$c1/$l1 fN=$cN/$lN want 200/$fsz"
  [ "${coob:-0}" = 404 ] && ok GET "WADO /frames/$((nf+1)) (out of range)" "404" || no GET "WADO frame OOB" "got $coob want 404"
else
  printf '  %-7s %-7s %-52s %s\n' "[skip]" "--" "multi-frame (1x384) section" "disabled or no sample data"
fi

hdr "Multiple series (cross-study/series handling)"
# After the stores above, CUBE holds several series (the ingested 384x1 volume +
# the single STOW object + the multipart series + the 1x384 multi-frame object).
# Confirm QIDO surfaces multiple series and studies across the catalog.
nser=$(body "${DW}/series?limit=1000" "${AUTH[@]}" -H 'Accept: application/dicom+json' | "$PYBIN" -c "import sys,json;print(len(json.load(sys.stdin)))" 2>/dev/null)
nstd=$(body "${DW}/studies?limit=1000" "${AUTH[@]}" -H 'Accept: application/dicom+json' | "$PYBIN" -c "import sys,json;print(len(json.load(sys.stdin)))" 2>/dev/null)
[ "${nser:-0}" -ge 2 ] && ok GET "/series (cross-study)" "${nser} series in catalog (multiple-series handling)" || no GET "/series multiple" "got ${nser} want >=2"
[ "${nstd:-0}" -ge 2 ] && ok GET "/studies (catalog)" "${nstd} studies in catalog" || no GET "/studies catalog" "got ${nstd} want >=2"

echo
echo "=============================================================================="
printf ' API tests: %s passed, %s failed (of %s)\n' "$PASS" "$FAIL" "$N"
echo "=============================================================================="
[ "$FAIL" -eq 0 ]
