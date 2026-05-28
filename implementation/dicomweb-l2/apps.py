from django.apps import AppConfig


class DicomwebConfig(AppConfig):
    """
    The ``dicomweb`` Django app: the DICOMweb (QIDO-RS / WADO-RS / STOW-RS)
    surface on top of CUBE's existing ``pacsfiles`` storage tree.

    Deliberately isolated from ``pacsfiles`` so the DICOMweb concerns (the
    instance/study index, the DICOM-tag query parser, the ``application/dicom+json``
    renderer, and the QIDO/WADO/STOW views) never perturb the stable
    ``/api/v1/pacs/...`` collection+json surface. Mirrors the minimal app
    pattern used elsewhere in ``chris_backend`` (compare ``pacsfiles/apps.py``).
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'dicomweb'
