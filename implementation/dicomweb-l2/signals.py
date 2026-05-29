"""Real-time auto-indexing of ingested DICOM into the dicomweb tables.

oxidicom registers ``PACSFile`` rows via its own ingest path (NATS -> a Celery
``register_pacs_series`` task), NOT through the REST ``PACSSeriesSerializer``
that Phase A hooked. Without a universal hook, oxidicom-ingested studies never
reach the QIDO/WADO index until a manual ``reindex_pacs_instances`` run.

A ``post_save`` receiver on ``PACSFile`` closes that gap: every newly created
``.dcm`` is queued for the idempotent ``index_pacs_instance`` task, so QIDO/WADO
see oxidicom data automatically. Wrapped in ``transaction.on_commit`` so:
  * the worker only runs once the row is visible, and
  * it is a no-op inside atomic ``TestCase``s (callbacks are not fired there),
    keeping the unit suite independent of a live broker.
"""
import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from pacsfiles.models import PACSFile

from . import tasks

logger = logging.getLogger(__name__)


@receiver(post_save, sender=PACSFile, dispatch_uid='dicomweb_autoindex_pacsfile')
def autoindex_pacs_file(sender, instance, created, **kwargs):
    """Queue the DICOMweb indexer for each newly ingested ``.dcm`` PACSFile."""
    if not created:
        return
    name = getattr(instance.fname, 'name', '') or ''
    if not name.endswith('.dcm'):
        return
    pk = instance.pk
    transaction.on_commit(lambda: tasks.index_pacs_instance.delay(pk))
