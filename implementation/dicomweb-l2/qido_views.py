"""
QIDO-RS query endpoints (PS3.18 §10.6).

Per-PACS DICOMweb roots, mounted under ``/dicom-web/pacs/<pacs_identifier>/``
(see ``urls.py``). All endpoints are GET-only and list-shaped (QIDO has no
detail resource). Responses are the DICOM JSON Model as ``application/dicom+json``
(or ``application/json``, treated as equivalent).

Endpoints implemented:
    GET  studies
    GET  studies/{study}/series
    GET  studies/{study}/series/{series}/instances
    GET  studies/{study}/instances
    GET  series                         (cross-study within the PACS)
    GET  instances                      (cross-study within the PACS)

HTTP semantics (PS3.18 §10.6.3):
    200  results in body (JSON array; ``[]`` if a valid query matched nothing)
    400  malformed query (bad tag hex, malformed range)  -> QidoQueryError
    401/403  auth / permission (CUBE DRF chain + IsChrisOrIsPACSUserReadOnly)
    404  the named PACS / parent study|series does not exist
    406  Accept not satisfiable (DRF content negotiation)
    413  result would exceed MAX_LIMIT (only when the client asks for >= MAX_LIMIT)

Empty-result policy: PS3.18 §10.6.3's Search status table does not define 204,
so a valid query that matches nothing returns 200 + ``[]`` (what OHIF / dcm4che
expect). We never emit 204; the parent-resource-absent case is a 404 via the
per-view ``get_object_or_404`` on the study/series.
"""
import logging

from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.authentication import (TokenAuthentication,
                                            BasicAuthentication,
                                            SessionAuthentication)

from pacsfiles.models import PACS, PACSSeries
from pacsfiles.permissions import IsChrisOrIsPACSUserReadOnly

from django.db.models import Count

from . import query_parser, serializers as dcm_serializers
from .models import PACSStudy, PACSInstance
from .renderers import DicomJsonRenderer, DicomJsonAsJsonRenderer

logger = logging.getLogger(__name__)


class QidoBaseView(generics.GenericAPIView):
    """
    Shared base for all QIDO endpoints.

    Reuses CUBE's auth chain (Token / Basic / Session, with LDAP wired behind
    Token/Basic via ``users.models.CustomLDAPBackend``) and the existing
    read-only PACS permission. No collection+json, no DRF ModelSerializer.
    """
    http_method_names = ['get']
    authentication_classes = (TokenAuthentication, BasicAuthentication,
                              SessionAuthentication)
    permission_classes = (permissions.IsAuthenticated,
                          IsChrisOrIsPACSUserReadOnly)
    renderer_classes = (DicomJsonRenderer, DicomJsonAsJsonRenderer)

    # QIDO level for the query parser; set by subclasses.
    qido_level = None

    def get_pacs(self):
        pacs = get_object_or_404(PACS,
                                 identifier=self.kwargs['pacs_identifier'])
        # IsChrisOrIsPACSUserReadOnly is a view-level (has_permission) check on
        # this read path; no per-object owner check is needed for PACS reads.
        return pacs

    def parse_query(self):
        try:
            return query_parser.parse(self.qido_level,
                                      self.request.query_params)
        except query_parser.QidoQueryError as exc:
            # Surfaced as 400 by handle_exception via DRF's ValidationError-ish
            # path; we raise a dedicated 400 here for a clean DICOM-ish body.
            raise _BadQuery(str(exc))

    def respond(self, datasets):
        """
        QIDO Search success body. Always 200 + a JSON array (``[]`` on an empty
        match). PS3.18 §10.6.3's status table does not define 204 for Search, so
        we do not emit 204 -- a valid query matching nothing is 200 + ``[]``,
        which is what OHIF and dcm4che expect.
        """
        return Response(datasets if datasets else [],
                        status=status.HTTP_200_OK)

    def urls_builder(self):
        return dcm_serializers.RetrieveURLBuilder(
            self.request, self.kwargs['pacs_identifier'])

    # -- DRF hook: turn _BadQuery into a 400 with a small JSON body --------- #
    def handle_exception(self, exc):
        if isinstance(exc, _BadQuery):
            return Response({'errorMessage': str(exc)},
                            status=status.HTTP_400_BAD_REQUEST)
        return super().handle_exception(exc)


class _BadQuery(Exception):
    """Internal marker -> 400 Bad Request."""


# --------------------------------------------------------------------------- #
# Study level
# --------------------------------------------------------------------------- #
class StudyListView(QidoBaseView):
    """GET /studies  -- Study-level QIDO over the explicit PACSStudy index."""
    qido_level = 'study'

    def get(self, request, *args, **kwargs):
        pacs = self.get_pacs()
        pq = self.parse_query()
        qs = (PACSStudy.objects
              .filter(pacs=pacs)
              .filter(pq.filter_q)
              .order_by('-StudyDate', 'StudyInstanceUID'))
        total = qs.count()
        if total > query_parser.MAX_LIMIT and pq.limit >= query_parser.MAX_LIMIT:
            return _too_many()
        rows = qs[pq.offset:pq.offset + pq.limit]
        urls = self.urls_builder()
        datasets = [
            dcm_serializers.serialize_study(
                s, urls, include_all=pq.include_all,
                includefields=pq.includefields)
            for s in rows
        ]
        return self.respond(datasets)


# --------------------------------------------------------------------------- #
# Series level
# --------------------------------------------------------------------------- #
class _SeriesListBase(QidoBaseView):
    qido_level = 'series'

    def base_queryset(self, pacs):
        raise NotImplementedError

    def get(self, request, *args, **kwargs):
        pacs = self.get_pacs()
        pq = self.parse_query()
        qs = (self.base_queryset(pacs)
              .filter(pq.filter_q)
              .annotate(num_instances=Count('instances'))
              .order_by('SeriesNumber', 'SeriesInstanceUID'))
        rows = qs[pq.offset:pq.offset + pq.limit]
        urls = self.urls_builder()
        datasets = [
            dcm_serializers.serialize_series(
                s, urls, num_instances=s.num_instances,
                include_all=pq.include_all, includefields=pq.includefields)
            for s in rows
        ]
        return self.respond(datasets)


class StudySeriesListView(_SeriesListBase):
    """GET /studies/{study}/series."""
    def base_queryset(self, pacs):
        # 404 the study if it doesn't exist at all (friendlier than empty list).
        get_object_or_404(PACSStudy, pacs=pacs,
                          StudyInstanceUID=self.kwargs['study_uid'])
        return PACSSeries.objects.filter(
            pacs=pacs, StudyInstanceUID=self.kwargs['study_uid'])


class AllSeriesListView(_SeriesListBase):
    """GET /series  -- cross-study series search within the PACS."""
    def base_queryset(self, pacs):
        return PACSSeries.objects.filter(pacs=pacs)


# --------------------------------------------------------------------------- #
# Instance level
# --------------------------------------------------------------------------- #
class _InstanceListBase(QidoBaseView):
    qido_level = 'instance'

    def base_queryset(self, pacs):
        raise NotImplementedError

    def get(self, request, *args, **kwargs):
        pacs = self.get_pacs()
        pq = self.parse_query()
        qs = (self.base_queryset(pacs)
              .select_related('series')
              .filter(pq.filter_q)
              .order_by('InstanceNumber', 'SOPInstanceUID'))
        rows = qs[pq.offset:pq.offset + pq.limit]
        urls = self.urls_builder()
        datasets = [
            dcm_serializers.serialize_instance(
                i, urls, include_all=pq.include_all,
                includefields=pq.includefields)
            for i in rows
        ]
        return self.respond(datasets)


class SeriesInstanceListView(_InstanceListBase):
    """GET /studies/{study}/series/{series}/instances."""
    def base_queryset(self, pacs):
        series = get_object_or_404(
            PACSSeries, pacs=pacs,
            StudyInstanceUID=self.kwargs['study_uid'],
            SeriesInstanceUID=self.kwargs['series_uid'])
        return PACSInstance.objects.filter(series=series)


class StudyInstanceListView(_InstanceListBase):
    """GET /studies/{study}/instances  -- all instances in a study."""
    def base_queryset(self, pacs):
        get_object_or_404(PACSStudy, pacs=pacs,
                          StudyInstanceUID=self.kwargs['study_uid'])
        return PACSInstance.objects.filter(
            series__pacs=pacs,
            series__StudyInstanceUID=self.kwargs['study_uid'])


class AllInstanceListView(_InstanceListBase):
    """GET /instances  -- cross-study instance search within the PACS."""
    def base_queryset(self, pacs):
        return PACSInstance.objects.filter(series__pacs=pacs)


def _too_many():
    resp = Response(
        {'errorMessage': 'The search was too broad; refine the query.'},
        status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    # PS3.18 §10.6.3: reference a Search Status report via the Warning header.
    resp['Warning'] = '299 {} "Too many matches; refine query"'.format(
        'cube-dicomweb')
    return resp
