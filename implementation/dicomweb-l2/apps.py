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

    def ready(self):
        # Register the post_save auto-indexer so oxidicom-ingested DICOM reaches
        # the QIDO/WADO index in real time (no manual reindex needed).
        from . import signals  # noqa: F401

        # Register the pg_trgm `__trigram_similar` lookup used by QIDO fuzzy PN
        # matching. Normally provided by having 'django.contrib.postgres' in
        # INSTALLED_APPS; we register it locally so the dicomweb app is
        # self-contained and does not force a CUBE-wide INSTALLED_APPS change.
        try:
            from django.contrib.postgres.lookups import TrigramSimilar
            from django.db.models import CharField, TextField
            CharField.register_lookup(TrigramSimilar)
            TextField.register_lookup(TrigramSimilar)
        except Exception:  # pragma: no cover - defensive
            import logging
            logging.getLogger(__name__).warning(
                'dicomweb: could not register trigram_similar lookup; '
                'fuzzymatching=true will be unavailable')
