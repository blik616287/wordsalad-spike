"""
DRF renderers for the DICOMweb surface.

  * ``DicomJsonRenderer``  -> ``application/dicom+json`` (QIDO results, WADO
                              metadata, STOW response). The view hands it data
                              that is *already* in DICOM JSON Model shape (built
                              via ``dicomjson.dataset`` / ``.sequence``), so the
                              renderer only serializes -- it does not transform.
  * ``DicomJsonAsJsonRenderer`` -> the same bytes under ``application/json``,
                              because QIDO clients (OHIF) may send
                              ``Accept: application/json`` and the spec says to
                              treat it as equivalent to ``application/dicom+json``
                              (PS3.18 §10.6.2; Azure conformance statement).
  * ``MultipartRelatedRenderer`` -> a passthrough for WADO-RS retrieval. The
                              view builds the ``multipart/related`` body itself
                              (it must set a per-response ``boundary`` in the
                              Content-Type), so this renderer just emits the
                              already-assembled bytes and is registered so DRF
                              content-negotiation accepts the media type.

DICOM JSON Model spec: PS3.18 Annex F,
https://dicom.nema.org/medical/dicom/current/output/chtml/part18/sect_F.2.html
"""
import json

from rest_framework.renderers import BaseRenderer


class DicomJsonRenderer(BaseRenderer):
    """Emit DICOM JSON Model as ``application/dicom+json`` (UTF-8)."""
    media_type = 'application/dicom+json'
    format = 'dicom+json'
    charset = 'utf-8'

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if data is None:
            return b''
        # ensure_ascii=False: DICOM JSON is UTF-8 (ISO_IR 192); keep names intact.
        return json.dumps(data, ensure_ascii=False).encode('utf-8')


class DicomJsonAsJsonRenderer(DicomJsonRenderer):
    """
    Same DICOM JSON Model bytes, advertised as ``application/json``.

    QIDO-RS requires that ``Accept: application/json`` be treated as equivalent
    to ``application/dicom+json`` (PS3.18 §10.6.2). Registering this lets DRF
    content-negotiation satisfy such clients without a 406.
    """
    media_type = 'application/json'
    format = 'json'


class MultipartRelatedRenderer(BaseRenderer):
    """
    Passthrough renderer for WADO-RS ``multipart/related`` retrieval bodies.

    The view assembles the full multipart body (and sets the real
    ``Content-Type`` with the chosen ``boundary`` on the response), then returns
    raw ``bytes``; this renderer emits them unchanged. ``media_type`` is the
    wildcard subtype so DRF negotiation matches any ``multipart/related; ...``
    Accept the client sends; the view enforces the concrete ``type=`` /
    ``transfer-syntax=`` parameters itself.
    """
    media_type = 'multipart/related'
    format = 'multipart'
    charset = None
    render_style = 'binary'

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if data is None:
            return b''
        if isinstance(data, bytes):
            return data
        # A dict here means the view fell through to an error payload; serialize
        # it as JSON so the error is at least legible.
        return json.dumps(data, ensure_ascii=False).encode('utf-8')
