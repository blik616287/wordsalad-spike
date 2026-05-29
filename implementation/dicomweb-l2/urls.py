"""
URL routing for the DICOMweb surface.

Mounted from ``chris_backend/config/urls.py`` (NOT ``core/api.py``) because the
DICOMweb endpoints deliberately break with CUBE's conventions: they speak
``application/dicom+json`` / ``multipart/related`` (not collection+json) and use
a per-PACS hierarchical path, not the flat aggregated ``format_suffix_patterns``
router. See the wiring note at the bottom of this file.

All paths are relative to the per-PACS root, so the full surface is:

    /dicom-web/pacs/<id>/studies                                          [QIDO + STOW]
    /dicom-web/pacs/<id>/studies/<study>/series                           [QIDO]
    /dicom-web/pacs/<id>/studies/<study>/series/<series>/instances        [QIDO]
    /dicom-web/pacs/<id>/studies/<study>/instances                        [QIDO]
    /dicom-web/pacs/<id>/series                                           [QIDO]
    /dicom-web/pacs/<id>/instances                                        [QIDO]

    /dicom-web/pacs/<id>/studies/<study>                                  [WADO retrieve + STOW-to-study]
    /dicom-web/pacs/<id>/studies/<study>/metadata                         [WADO metadata]
    /dicom-web/pacs/<id>/studies/<study>/series/<series>                  [WADO retrieve]
    /dicom-web/pacs/<id>/studies/<study>/series/<series>/metadata         [WADO metadata]
    /dicom-web/pacs/<id>/studies/<study>/series/<series>/instances/<sop>  [WADO retrieve]
    /dicom-web/pacs/<id>/.../instances/<sop>/metadata                     [WADO metadata]
    /dicom-web/pacs/<id>/.../instances/<sop>/frames/<frameList>           [WADO frames: native octet-stream; 501 if compressed]
    /dicom-web/pacs/<id>/.../instances/<sop>/bulkdata                     [WADO bulkdata: native octet-stream; 501 if compressed]

Two paths carry BOTH a GET (QIDO/WADO) and a POST (STOW) operation:
``/studies`` and ``/studies/<study>``. Django binds one path to one view, so a
thin method-dispatching ``View`` routes GET vs POST to the right handler.

Path converters: DICOM UIDs are dotted numeric strings (e.g.
``1.2.840.113619...``); the ``_UID`` regex below matches digits, dots, hyphens,
and the occasional letter without crossing a ``/``. The more specific
``studies/<study>/...`` patterns are registered before the bare
``studies/<study>`` so resolution is unambiguous.
"""
from django.urls import path, re_path
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from . import qido_views, wado_views, stow_views

app_name = 'dicomweb'

# UID path segment: dotted digits (and the occasional letter in some UIDs),
# never crossing a path separator.
_UID = r'[0-9A-Za-z.\-]+'


# These dispatchers are plain Django Views; DRF's per-view csrf_exempt does not
# reach the inner as_view() through them, so exempt the dispatcher itself. The
# inner DRF views still authenticate (Token/Basic) and never rely on CSRF.
@method_decorator(csrf_exempt, name='dispatch')
class StudiesRootDispatcher(View):
    """``/studies``: GET -> QIDO StudyList, POST -> STOW store (any study)."""
    http_method_names = ['get', 'post']

    def get(self, request, *args, **kwargs):
        return qido_views.StudyListView.as_view()(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        return stow_views.StowView.as_view()(request, *args, **kwargs)


@method_decorator(csrf_exempt, name='dispatch')
class StudyDispatcher(View):
    """``/studies/<study>``: GET -> WADO retrieve study, POST -> STOW to study."""
    http_method_names = ['get', 'post']

    def get(self, request, *args, **kwargs):
        return wado_views.RetrieveStudyView.as_view()(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        return stow_views.StowView.as_view()(request, *args, **kwargs)


urlpatterns = [
    # ---- QIDO-RS list roots (+ STOW on /studies) ------------------------ #
    path('studies', StudiesRootDispatcher.as_view(), name='studies-root'),
    path('series', qido_views.AllSeriesListView.as_view(), name='all-series'),
    path('instances', qido_views.AllInstanceListView.as_view(),
         name='all-instances'),

    # ---- instance-scoped (most specific) -------------------------------- #
    re_path(rf'^studies/(?P<study_uid>{_UID})/series/(?P<series_uid>{_UID})/'
            rf'instances/(?P<sop_uid>{_UID})/metadata$',
            wado_views.InstanceMetadataView.as_view(), name='instance-metadata'),
    re_path(rf'^studies/(?P<study_uid>{_UID})/series/(?P<series_uid>{_UID})/'
            rf'instances/(?P<sop_uid>{_UID})/frames/(?P<frames>[0-9,]+)$',
            wado_views.FramesView.as_view(), name='instance-frames'),
    re_path(rf'^studies/(?P<study_uid>{_UID})/series/(?P<series_uid>{_UID})/'
            rf'instances/(?P<sop_uid>{_UID})/bulkdata$',
            wado_views.BulkdataView.as_view(), name='instance-bulkdata'),
    re_path(rf'^studies/(?P<study_uid>{_UID})/series/(?P<series_uid>{_UID})/'
            rf'instances/(?P<sop_uid>{_UID})$',
            wado_views.RetrieveInstanceView.as_view(), name='instance-retrieve'),

    # ---- series-scoped -------------------------------------------------- #
    re_path(rf'^studies/(?P<study_uid>{_UID})/series/(?P<series_uid>{_UID})/'
            rf'instances$',
            qido_views.SeriesInstanceListView.as_view(),
            name='series-instances'),
    re_path(rf'^studies/(?P<study_uid>{_UID})/series/(?P<series_uid>{_UID})/'
            rf'metadata$',
            wado_views.SeriesMetadataView.as_view(), name='series-metadata'),
    re_path(rf'^studies/(?P<study_uid>{_UID})/series/(?P<series_uid>{_UID})$',
            wado_views.RetrieveSeriesView.as_view(), name='series-retrieve'),

    # ---- study-scoped --------------------------------------------------- #
    re_path(rf'^studies/(?P<study_uid>{_UID})/series$',
            qido_views.StudySeriesListView.as_view(), name='study-series'),
    re_path(rf'^studies/(?P<study_uid>{_UID})/instances$',
            qido_views.StudyInstanceListView.as_view(), name='study-instances'),
    re_path(rf'^studies/(?P<study_uid>{_UID})/metadata$',
            wado_views.StudyMetadataView.as_view(), name='study-metadata'),
    re_path(rf'^studies/(?P<study_uid>{_UID})$',
            StudyDispatcher.as_view(), name='study'),
]


# --------------------------------------------------------------------------- #
# Wiring into a real CUBE checkout
# --------------------------------------------------------------------------- #
#
# 1. chris_backend/config/urls.py -- add the per-PACS mount:
#
#        from django.urls import path, include
#        urlpatterns = [
#            ...
#            path('dicom-web/pacs/<str:pacs_identifier>/',
#                 include('dicomweb.urls')),
#        ]
#
#    This sits alongside the existing '/schema/' and '/chris-admin/' mounts in
#    config/urls.py and is intentionally OUTSIDE core/api.py's
#    format_suffix_patterns aggregation.
#
# 2. chris_backend/config/settings/common.py
#        INSTALLED_APPS += ['dicomweb']            # already added in Phase A
#        # Optionally exclude the DICOMweb views from drf-spectacular, since
#        # their responses are not collection+json and don't fit the schema
#        # post-processing hooks (e.g. @extend_schema(exclude=True) per view).
#
# 3. chris_backend/core/celery.py -- task route (already added in Phase A):
#        'dicomweb.tasks.index_pacs_instance': {'queue': 'main2'},
#
# 4. CORS: local.py already sets CORS_ALLOW_ALL_ORIGINS = True for dev, which
#    covers OHIF in the browser. Production CORS is a deployment concern.
# --------------------------------------------------------------------------- #
