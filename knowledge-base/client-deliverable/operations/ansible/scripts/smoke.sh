#!/usr/bin/env bash
# =============================================================================
# smoke.sh -- verify the DICOMweb-on-CUBE spike deployment.
#
# Curls the core CUBE endpoints, the test Orthanc, and the QIDO/WADO/STOW
# endpoints, reporting PASS / FAIL / SKIP per check. Exits non-zero iff any
# REQUIRED check FAILs. DICOMweb checks are SKIP (not FAIL) when the L2 overlay
# is not yet applied (EXPECT_DICOMWEB != true).
#
# Run standalone:
#   CUBE_USER=chris CUBE_PASSWORD=chris1234 ./scripts/smoke.sh
# or via Ansible: ansible-playbook -i inventory.ini site.yml --tags verify
#
# Endpoint shapes (DICOM PS3.18):
#   QIDO-RS  GET  /dicom-web/pacs/<id>/studies          Accept: application/dicom+json   (§10.6)
#   WADO-RS  GET  /dicom-web/pacs/<id>/studies/<S>       Accept: multipart/related;type="application/dicom" (§10.4)
#   STOW-RS  POST /dicom-web/pacs/<id>/studies           Content-Type: multipart/related;type="application/dicom" (§10.5)
# =============================================================================
set -uo pipefail

CUBE_BASE_URL="${CUBE_BASE_URL:-http://localhost:8000}"
CUBE_API_URL="${CUBE_API_URL:-${CUBE_BASE_URL}/api/v1/}"
CUBE_USER="${CUBE_USER:-chris}"
CUBE_PASSWORD="${CUBE_PASSWORD:-chris1234}"
PACS_ID="${PACS_ID:-ChRIS}"
DICOMWEB_PREFIX="${DICOMWEB_PREFIX:-/dicom-web}"
EXPECT_DICOMWEB="${EXPECT_DICOMWEB:-false}"
ORTHANC_BASE_URL="${ORTHANC_BASE_URL:-http://localhost:8142}"

AUTH=(-u "${CUBE_USER}:${CUBE_PASSWORD}")
DICOMWEB_BASE="${CUBE_BASE_URL}${DICOMWEB_PREFIX}/pacs/${PACS_ID}"

PASS=0; FAIL=0; SKIP=0

green()  { printf '\033[32m%s\033[0m' "$1"; }
red()    { printf '\033[31m%s\033[0m' "$1"; }
yellow() { printf '\033[33m%s\033[0m' "$1"; }

pass() { PASS=$((PASS+1)); printf '  [%s] %s\n' "$(green PASS)" "$1"; }
fail() { FAIL=$((FAIL+1)); printf '  [%s] %s\n' "$(red FAIL)" "$1"; }
skip() { SKIP=$((SKIP+1)); printf '  [%s] %s\n' "$(yellow SKIP)" "$1"; }

# http_status URL [curl-args...] -> echoes the numeric HTTP status code
http_status() {
  local url="$1"; shift
  curl -s -o /dev/null -w '%{http_code}' "$@" "$url" 2>/dev/null
}

echo "=============================================================="
echo " DICOMweb-on-CUBE spike :: smoke test"
echo " CUBE    : ${CUBE_BASE_URL}"
echo " Orthanc : ${ORTHANC_BASE_URL}"
echo " PACS id : ${PACS_ID}"
echo " DICOMweb base: ${DICOMWEB_BASE}"
echo " expect dicomweb endpoints: ${EXPECT_DICOMWEB}"
echo "=============================================================="

echo "-- Core CUBE --------------------------------------------------"
code=$(http_status "${CUBE_API_URL}" "${AUTH[@]}")
[ "$code" = "200" ] && pass "CUBE API root /api/v1/ (200)" \
                    || fail "CUBE API root /api/v1/ (got ${code}, want 200)"

# Token auth path (used by miniChRIS test.sh).
tok_code=$(http_status "${CUBE_BASE_URL}/api/v1/auth-token/" \
            -X POST -H 'Content-Type: application/json' \
            --data "{\"username\":\"${CUBE_USER}\",\"password\":\"${CUBE_PASSWORD}\"}")
[ "$tok_code" = "200" ] && pass "CUBE auth-token (200)" \
                        || fail "CUBE auth-token (got ${tok_code})"

# Existing PACS listing. NOTE: /api/v1/pacs/ proxies pfdcm to list *queryable*
# PACS services and returns 500 when pfdcm is unreachable -- a documented upstream
# miniChRIS quirk (see KB 09) that is unrelated to the DICOMweb spike (the
# ingested-data check below uses /api/v1/pacs/series/). Treat that specific 500
# as a non-fatal WARN/SKIP rather than failing the whole deployment on it.
pacs_json=$(curl -s "${AUTH[@]}" -H 'Accept: application/json' "${CUBE_BASE_URL}/api/v1/pacs/")
pacs_code=$(http_status "${CUBE_BASE_URL}/api/v1/pacs/" "${AUTH[@]}" -H 'Accept: application/json')
case "$pacs_code" in
  200) pass "CUBE /api/v1/pacs/ (200)" ;;
  500) skip "CUBE /api/v1/pacs/ (500 -- pfdcm unreachable; upstream quirk, not DICOMweb)" ;;
  *)   fail "CUBE /api/v1/pacs/ (got ${pacs_code})" ;;
esac

series_code=$(http_status "${CUBE_BASE_URL}/api/v1/pacs/series/" "${AUTH[@]}" -H 'Accept: application/json')
series_json=$(curl -s "${AUTH[@]}" -H 'Accept: application/json' "${CUBE_BASE_URL}/api/v1/pacs/series/")
if [ "$series_code" = "200" ]; then
  # crude count of series rows -- works whether the body is DRF or collection+json
  n=$(printf '%s' "$series_json" | grep -o 'SeriesInstanceUID' | wc -l | tr -d ' ')
  if [ "${n:-0}" -gt 0 ]; then
    pass "CUBE has ingested PACS series (>=1 SeriesInstanceUID seen)"
  else
    skip "CUBE PACS series list empty (sample push may still be ingesting)"
  fi
else
  fail "CUBE /api/v1/pacs/series/ (got ${series_code})"
fi

echo "-- Test Orthanc -----------------------------------------------"
o_code=$(http_status "${ORTHANC_BASE_URL}/system")
[ "$o_code" = "200" ] && pass "Orthanc /system (200)" \
                      || fail "Orthanc /system (got ${o_code})"

odw_code=$(http_status "${ORTHANC_BASE_URL}/plugins/dicom-web")
[ "$odw_code" = "200" ] && pass "Orthanc DicomWeb plugin loaded (200)" \
                        || fail "Orthanc DicomWeb plugin (got ${odw_code})"

# Orthanc's OWN QIDO-RS (sanity that the source PACS speaks DICOMweb)
oqido_code=$(http_status "${ORTHANC_BASE_URL}/dicom-web/studies" \
              -H 'Accept: application/dicom+json')
[ "$oqido_code" = "200" ] && pass "Orthanc QIDO-RS /dicom-web/studies (200)" \
                          || fail "Orthanc QIDO-RS /dicom-web/studies (got ${oqido_code})"

echo "-- CUBE DICOMweb (L2 endpoints) -------------------------------"
if [ "${EXPECT_DICOMWEB}" != "true" ]; then
  skip "QIDO-RS  ${DICOMWEB_BASE}/studies  (L2 overlay not applied)"
  skip "WADO-RS  ${DICOMWEB_BASE}/studies/<S>  (L2 overlay not applied)"
  skip "STOW-RS  POST ${DICOMWEB_BASE}/studies  (L2 overlay not applied)"
else
  # QIDO-RS: list studies, DICOM JSON Model. 200 (results) or 204 (no content).
  q_code=$(http_status "${DICOMWEB_BASE}/studies" "${AUTH[@]}" \
            -H 'Accept: application/dicom+json')
  case "$q_code" in
    200|204) pass "QIDO-RS ${DICOMWEB_BASE}/studies (${q_code})" ;;
    *)       fail "QIDO-RS ${DICOMWEB_BASE}/studies (got ${q_code}, want 200/204)" ;;
  esac

  # Grab a StudyInstanceUID from QIDO to drive WADO (tag 0020000D).
  study_uid=$(curl -s "${AUTH[@]}" -H 'Accept: application/dicom+json' \
                "${DICOMWEB_BASE}/studies" \
              | grep -o '"0020000D"[^]]*]' | grep -o '1\.[0-9.]*' | head -n1)

  if [ -n "${study_uid}" ]; then
    # WADO-RS: retrieve study metadata (cheaper than the full multipart object).
    w_code=$(http_status "${DICOMWEB_BASE}/studies/${study_uid}/metadata" "${AUTH[@]}" \
              -H 'Accept: application/dicom+json')
    case "$w_code" in
      200) pass "WADO-RS ${DICOMWEB_BASE}/studies/<uid>/metadata (200)" ;;
      *)   fail "WADO-RS metadata (got ${w_code}, want 200)" ;;
    esac
  else
    skip "WADO-RS (no StudyInstanceUID returned by QIDO to retrieve)"
  fi

  # STOW-RS: we don't push a real object in the smoke test (needs a multipart
  # body); we assert the endpoint exists and rejects an empty body cleanly
  # rather than 404. 400/415 = endpoint present but body invalid (expected);
  # 404 = endpoint missing (FAIL).
  s_code=$(http_status "${DICOMWEB_BASE}/studies" "${AUTH[@]}" -X POST \
            -H 'Content-Type: multipart/related; type="application/dicom"; boundary=X' \
            --data-binary '')
  case "$s_code" in
    400|415|409|200) pass "STOW-RS endpoint present (POST returned ${s_code})" ;;
    404)             fail "STOW-RS endpoint missing (404)" ;;
    *)               fail "STOW-RS unexpected status ${s_code}" ;;
  esac
fi

echo "=============================================================="
printf ' Result: %s pass, %s fail, %s skip\n' \
  "$(green "$PASS")" "$(red "$FAIL")" "$(yellow "$SKIP")"
echo "=============================================================="

[ "$FAIL" -eq 0 ]
