"""
WADO-RS retrieve endpoints (PS3.18 §10.4).

Implemented:
    GET  studies/{study}                                  -> multipart/related; application/dicom
    GET  studies/{study}/series/{series}                  -> multipart/related; application/dicom
    GET  studies/{study}/series/{series}/instances/{sop}  -> multipart/related; application/dicom
    GET  .../metadata  (study | series | instance)        -> application/dicom+json
    GET  .../instances/{sop}/frames/{frameList}           -> multipart/related; octet-stream (native; 501 if compressed)
    GET  .../bulkdata  reference                          -> multipart/related; octet-stream (native; 501 if compressed)

Object retrieval streams the stored ``.dcm`` bytes from CUBE storage
(``core.storage.connect_storage``) into ``multipart/related`` parts, one part
per instance, ``Content-Type: application/dicom``. We do NOT transcode: the
stored Transfer Syntax is returned as-is, which is the behavior selected by
``transfer-syntax=*`` and the only behavior we support. A request demanding a
specific, different ``transfer-syntax=`` we cannot produce yields 406.

Metadata retrieval emits the DICOM JSON Model built from the
PACSStudy/PACSSeries/PACSInstance index (no file re-read needed for the indexed
attributes), with ``PixelData (7FE00010)`` represented as a ``BulkDataURI``
pointing at the (stubbed) frames/bulkdata URL -- this is how OHIF lazily fetches
pixels.

Storage abstraction: ``StorageManager.download_obj(path) -> bytes`` over
fslink / swift / s3 (CUBE's ``core/storage/storagemanager.py``). Bytes resolve
via ``PACSInstance.pacs_file.fname`` (the Phase A 1-to-1).

Spec: https://dicom.nema.org/medical/dicom/current/output/chtml/part18/sect_10.4.html
multipart/related wire format cross-checked against Orthanc's DICOMweb plugin
(https://orthanc.uclouvain.be/book/plugins/dicomweb.html).
"""
import io
import logging
import uuid

import pydicom
from django.conf import settings
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.authentication import (TokenAuthentication,
                                            BasicAuthentication,
                                            SessionAuthentication)

from core.storage import connect_storage
from pacsfiles.models import PACS, PACSSeries
from pacsfiles.permissions import IsChrisOrIsPACSUserReadOnly

from . import dicomjson, serializers as dcm_serializers
from .models import PACSStudy, PACSInstance
from .renderers import (DicomJsonRenderer, DicomJsonAsJsonRenderer,
                        MultipartRelatedRenderer)

logger = logging.getLogger(__name__)

DICOM_MEDIA_TYPE = 'application/dicom'
DEFAULT_TRANSFER_SYNTAX = '1.2.840.10008.1.2.1'  # Explicit VR Little Endian


class WadoBaseView(generics.GenericAPIView):
    http_method_names = ['get']
    authentication_classes = (TokenAuthentication, BasicAuthentication,
                              SessionAuthentication)
    permission_classes = (permissions.IsAuthenticated,
                          IsChrisOrIsPACSUserReadOnly)

    def get_pacs(self):
        return get_object_or_404(PACS,
                                 identifier=self.kwargs['pacs_identifier'])

    def urls_builder(self):
        return dcm_serializers.RetrieveURLBuilder(
            self.request, self.kwargs['pacs_identifier'])


# --------------------------------------------------------------------------- #
# Object retrieval (multipart/related; type=application/dicom)
# --------------------------------------------------------------------------- #
class _RetrieveBase(WadoBaseView):
    renderer_classes = (MultipartRelatedRenderer,)

    def instances_queryset(self, pacs):
        """Return the ordered PACSInstance queryset to package."""
        raise NotImplementedError

    def _accept_ok(self):
        """
        Validate the Accept header. We only produce
        ``multipart/related; type="application/dicom"`` with the stored transfer
        syntax. ``transfer-syntax=*`` (or absent) is accepted; a specific,
        different syntax -> 406.
        """
        accept = self.request.headers.get('Accept', '*/*')
        if '*/*' in accept or accept.strip() == '':
            return True
        if 'multipart/related' not in accept:
            return False
        if 'application/dicom' not in accept and 'type=' in accept:
            return False
        # transfer-syntax: only '*' (any/stored) is satisfiable without transcode.
        if 'transfer-syntax=' in accept:
            ts = accept.split('transfer-syntax=', 1)[1]
            ts = ts.split(';')[0].strip().strip('"')
            if ts not in ('*', '', DEFAULT_TRANSFER_SYNTAX):
                # We can only honor a specific TS if every part already stores it.
                return ts  # caller compares per-instance
        return True

    def get(self, request, *args, **kwargs):
        pacs = self.get_pacs()
        accept = self._accept_ok()
        if accept is False:
            return Response(
                {'errorMessage': 'Only multipart/related; type="application/dicom" '
                                 'is supported.'},
                status=status.HTTP_406_NOT_ACCEPTABLE)

        instances = list(self.instances_queryset(pacs))
        if not instances:
            return Response(status=status.HTTP_404_NOT_FOUND)

        # If the client demanded a specific transfer syntax, every instance must
        # already store it (we do not transcode).
        if isinstance(accept, str):
            mismatched = [i for i in instances
                          if i.TransferSyntaxUID and i.TransferSyntaxUID != accept]
            if mismatched:
                return Response(
                    {'errorMessage': f'transfer-syntax={accept} not available '
                                     'without transcoding.'},
                    status=status.HTTP_406_NOT_ACCEPTABLE)

        boundary = uuid.uuid4().hex
        storage = connect_storage(settings)
        urls = self.urls_builder()

        resp = StreamingHttpResponse(
            self._iter_multipart(instances, boundary, storage, urls),
            status=status.HTTP_200_OK)
        resp['Content-Type'] = (
            f'multipart/related; type="{DICOM_MEDIA_TYPE}"; boundary={boundary}')
        return resp

    def _iter_multipart(self, instances, boundary, storage, urls):
        crlf = b'\r\n'
        delim = b'--' + boundary.encode('ascii')
        for inst in instances:
            fname = inst.pacs_file.fname.name
            try:
                data = storage.download_obj(fname)
            except Exception as exc:  # storage miss -> skip this part, log
                logger.error('WADO-RS: storage read failed for %s: %s',
                             fname, exc)
                continue
            series = inst.series
            location = urls.instance(series.StudyInstanceUID,
                                     series.SeriesInstanceUID,
                                     inst.SOPInstanceUID)
            header = (
                delim + crlf +
                b'Content-Type: ' + DICOM_MEDIA_TYPE.encode('ascii') + crlf +
                b'Content-Location: ' + location.encode('ascii') + crlf +
                b'Content-Length: ' + str(len(data)).encode('ascii') + crlf +
                crlf
            )
            yield header
            yield data
            yield crlf
        yield delim + b'--' + crlf


class RetrieveStudyView(_RetrieveBase):
    """GET studies/{study}."""
    def instances_queryset(self, pacs):
        get_object_or_404(PACSStudy, pacs=pacs,
                          StudyInstanceUID=self.kwargs['study_uid'])
        return (PACSInstance.objects
                .filter(series__pacs=pacs,
                        series__StudyInstanceUID=self.kwargs['study_uid'])
                .select_related('series', 'pacs_file')
                .order_by('series__SeriesNumber', 'InstanceNumber'))


class RetrieveSeriesView(_RetrieveBase):
    """GET studies/{study}/series/{series}."""
    def instances_queryset(self, pacs):
        series = get_object_or_404(
            PACSSeries, pacs=pacs,
            StudyInstanceUID=self.kwargs['study_uid'],
            SeriesInstanceUID=self.kwargs['series_uid'])
        return (PACSInstance.objects.filter(series=series)
                .select_related('series', 'pacs_file')
                .order_by('InstanceNumber'))


class RetrieveInstanceView(_RetrieveBase):
    """GET studies/{study}/series/{series}/instances/{sop}."""
    def instances_queryset(self, pacs):
        return (PACSInstance.objects
                .filter(series__pacs=pacs,
                        series__StudyInstanceUID=self.kwargs['study_uid'],
                        series__SeriesInstanceUID=self.kwargs['series_uid'],
                        SOPInstanceUID=self.kwargs['sop_uid'])
                .select_related('series', 'pacs_file'))


# --------------------------------------------------------------------------- #
# Metadata retrieval (application/dicom+json)
# --------------------------------------------------------------------------- #
class _MetadataBase(WadoBaseView):
    renderer_classes = (DicomJsonRenderer, DicomJsonAsJsonRenderer)

    def datasets(self, pacs, urls):
        raise NotImplementedError

    def get(self, request, *args, **kwargs):
        pacs = self.get_pacs()
        urls = self.urls_builder()
        datasets = self.datasets(pacs, urls)
        if not datasets:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(datasets, status=status.HTTP_200_OK)

    def _instance_metadata(self, inst, urls):
        """
        Instance-level metadata dataset, with PixelData as a BulkDataURI.

        Uses the indexed PACSInstance/PACSSeries/PACSStudy columns; PixelData
        (7FE00010) is referenced (not inlined) via the bulkdata/frames URL --
        OHIF then fetches pixels lazily through the frames endpoint.
        """
        series = inst.series
        ds = dcm_serializers.serialize_instance(inst, urls)
        # Add a BulkDataURI for PixelData -> the (stubbed) frames endpoint.
        frame_url = urls.instance(series.StudyInstanceUID,
                                  series.SeriesInstanceUID,
                                  inst.SOPInstanceUID) + '/frames/1'
        ds['7FE00010'] = dicomjson.bulkdata_element('OW', frame_url)
        if inst.TransferSyntaxUID:
            # AvailableTransferSyntaxUID (0008,3002) helps clients negotiate.
            ds['00083002'] = dicomjson.element('UI', inst.TransferSyntaxUID)
        return ds


class StudyMetadataView(_MetadataBase):
    """GET studies/{study}/metadata."""
    def datasets(self, pacs, urls):
        get_object_or_404(PACSStudy, pacs=pacs,
                          StudyInstanceUID=self.kwargs['study_uid'])
        insts = (PACSInstance.objects
                 .filter(series__pacs=pacs,
                         series__StudyInstanceUID=self.kwargs['study_uid'])
                 .select_related('series'))
        return [self._instance_metadata(i, urls) for i in insts]


class SeriesMetadataView(_MetadataBase):
    """GET studies/{study}/series/{series}/metadata."""
    def datasets(self, pacs, urls):
        series = get_object_or_404(
            PACSSeries, pacs=pacs,
            StudyInstanceUID=self.kwargs['study_uid'],
            SeriesInstanceUID=self.kwargs['series_uid'])
        insts = (PACSInstance.objects.filter(series=series)
                 .select_related('series'))
        return [self._instance_metadata(i, urls) for i in insts]


class InstanceMetadataView(_MetadataBase):
    """GET studies/{study}/series/{series}/instances/{sop}/metadata."""
    def datasets(self, pacs, urls):
        inst = get_object_or_404(
            PACSInstance,
            series__pacs=pacs,
            series__StudyInstanceUID=self.kwargs['study_uid'],
            series__SeriesInstanceUID=self.kwargs['series_uid'],
            SOPInstanceUID=self.kwargs['sop_uid'])
        return [self._instance_metadata(inst, urls)]


# --------------------------------------------------------------------------- #
# Frames / Bulkdata -- STUBS (return 501 Not Implemented)
# --------------------------------------------------------------------------- #
class _PixelBaseView(WadoBaseView):
    """Shared helpers for the frames + bulkdata pixel endpoints."""
    renderer_classes = (MultipartRelatedRenderer, DicomJsonRenderer)

    def _instance_or_404(self):
        return get_object_or_404(
            PACSInstance,
            series__pacs__identifier=self.kwargs['pacs_identifier'],
            series__StudyInstanceUID=self.kwargs['study_uid'],
            series__SeriesInstanceUID=self.kwargs['series_uid'],
            SOPInstanceUID=self.kwargs['sop_uid'])

    def _read_dataset(self, inst):
        storage = connect_storage(settings)
        raw = storage.download_obj(inst.pacs_file.fname.name)
        return pydicom.dcmread(io.BytesIO(raw), force=True)

    @staticmethod
    def _is_encapsulated(ds):
        ts = getattr(getattr(ds, 'file_meta', None), 'TransferSyntaxUID', None)
        return bool(getattr(ts, 'is_encapsulated', False)), ts

    @staticmethod
    def _multipart_octets(chunks, transfer_syntax):
        """Build a multipart/related; type="application/octet-stream" body."""
        boundary = uuid.uuid4().hex
        crlf = b'\r\n'
        delim = b'--' + boundary.encode('ascii')
        body = b''
        for chunk in chunks:
            body += (delim + crlf +
                     b'Content-Type: application/octet-stream; transfer-syntax='
                     + transfer_syntax.encode('ascii') + crlf +
                     b'Content-Length: ' + str(len(chunk)).encode('ascii') + crlf +
                     crlf + bytes(chunk) + crlf)
        body += delim + b'--' + crlf
        resp = HttpResponse(body, status=status.HTTP_200_OK)
        resp['Content-Type'] = ('multipart/related; '
                                'type="application/octet-stream"; '
                                f'boundary={boundary}')
        return resp


class FramesView(_PixelBaseView):
    """
    GET .../instances/{sop}/frames/{frameList}
        -> multipart/related; type="application/octet-stream"  (PS3.18 10.4.1.x)

    Returns the requested 1-based frames as raw pixel octets for NATIVE
    (uncompressed) transfer syntaxes: frames are sliced out of PixelData
    (7FE00010), frame_size = Rows*Columns*SamplesPerPixel*ceil(BitsAllocated/8).
    Encapsulated/compressed syntaxes still return 501 -- splitting encapsulated
    fragments / transcoding needs pylibjpeg|gdcm and is out of scope.
    """

    def get(self, request, *args, **kwargs):
        inst = self._instance_or_404()
        try:
            wanted = [int(n) for n in self.kwargs['frames'].split(',') if n != '']
        except ValueError:
            return Response({'errorMessage': 'invalid frame list'},
                            status=status.HTTP_400_BAD_REQUEST)
        if not wanted:
            return Response({'errorMessage': 'empty frame list'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            ds = self._read_dataset(inst)
        except Exception as exc:
            logger.error('WADO-RS frames: storage/parse failed for %s: %s',
                         inst.SOPInstanceUID, exc)
            return Response(status=status.HTTP_404_NOT_FOUND)

        encapsulated, ts = self._is_encapsulated(ds)
        if encapsulated:
            return Response(
                {'errorMessage': 'frame retrieval for encapsulated/compressed '
                                 'transfer syntaxes is not implemented (no '
                                 'transcoding); native syntaxes are supported.'},
                status=status.HTTP_501_NOT_IMPLEMENTED)
        if 'PixelData' not in ds:
            return Response({'errorMessage': 'instance has no PixelData'},
                            status=status.HTTP_404_NOT_FOUND)

        px = ds.PixelData
        spp = int(getattr(ds, 'SamplesPerPixel', 1) or 1)
        bytes_per_sample = (int(ds.BitsAllocated) + 7) // 8
        nframes = int(getattr(ds, 'NumberOfFrames', 1) or 1)
        frame_size = int(ds.Rows) * int(ds.Columns) * spp * bytes_per_sample

        chunks = []
        for n in wanted:
            if n < 1 or n > nframes:
                return Response(
                    {'errorMessage': f'frame {n} out of range (1..{nframes})'},
                    status=status.HTTP_404_NOT_FOUND)
            chunks.append(px[(n - 1) * frame_size: n * frame_size])
        return self._multipart_octets(chunks, str(ts) if ts else DEFAULT_TRANSFER_SYNTAX)


class BulkdataView(_PixelBaseView):
    """
    GET .../instances/{sop}/bulkdata
        -> multipart/related; type="application/octet-stream"

    Returns the instance's bulk PixelData (the element the metadata's
    ``BulkDataURI`` points at) for native transfer syntaxes; encapsulated -> 501.
    """

    def get(self, request, *args, **kwargs):
        inst = self._instance_or_404()
        try:
            ds = self._read_dataset(inst)
        except Exception as exc:
            logger.error('WADO-RS bulkdata: storage/parse failed for %s: %s',
                         inst.SOPInstanceUID, exc)
            return Response(status=status.HTTP_404_NOT_FOUND)
        encapsulated, ts = self._is_encapsulated(ds)
        if encapsulated:
            return Response(
                {'errorMessage': 'bulkdata for encapsulated/compressed transfer '
                                 'syntaxes is not implemented (no transcoding).'},
                status=status.HTTP_501_NOT_IMPLEMENTED)
        if 'PixelData' not in ds:
            return Response({'errorMessage': 'instance has no PixelData'},
                            status=status.HTTP_404_NOT_FOUND)
        return self._multipart_octets([ds.PixelData],
                                      str(ts) if ts else DEFAULT_TRANSFER_SYNTAX)
