"""
Management command: backfill the DICOMweb index for pre-existing PACS data.

After deploying the ``dicomweb`` app to a CUBE instance that already has
``PACSSeries`` / ``PACSFile`` rows (i.e. the common upgrade case), run this once
to populate ``PACSInstance`` (and, with the PACSStudy FK in place, reconcile
``PACSStudy`` roll-ups) for every existing ``.dcm`` in the PACS tree:

    just bash
    python manage.py reindex_pacs_instances                 # all PACS
    python manage.py reindex_pacs_instances --pacs BCH      # one PACS
    python manage.py reindex_pacs_instances --dry-run

Idempotent (the underlying task uses ``update_or_create``); safe to re-run.
Dispatches the Phase A ``index_pacs_instance`` Celery task per file by default,
or runs it synchronously with ``--sync`` for small datasets / debugging.
"""
from django.core.management.base import BaseCommand

from pacsfiles.models import PACSFile


class Command(BaseCommand):
    help = 'Build PACSInstance rows for existing PACSFile data (idempotent).'

    def add_arguments(self, parser):
        parser.add_argument('--pacs', help='Limit to one PACS identifier.')
        parser.add_argument('--series',
                            help='Limit to one SeriesInstanceUID prefix.')
        parser.add_argument('--sync', action='store_true',
                            help='Run the indexing task synchronously in-process '
                                 '(default: dispatch to Celery).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Count matching files; dispatch nothing.')
        parser.add_argument('--batch', type=int, default=500,
                            help='Log progress every N files.')

    def handle(self, *args, **opts):
        from .. import tasks  # local import: avoid app-load import cycle

        qs = PACSFile.get_base_queryset().filter(fname__endswith='.dcm')
        if opts['pacs']:
            qs = qs.filter(fname__startswith=f'SERVICES/PACS/{opts["pacs"]}/')
        if opts['series']:
            qs = qs.filter(fname__contains=opts['series'])

        total = qs.count()
        self.stdout.write(f'Matched {total} .dcm file(s).')
        if opts['dry_run']:
            return

        n = 0
        for pacs_file in qs.iterator():
            if opts['sync']:
                tasks.index_pacs_instance(pacs_file.pk)
            else:
                tasks.index_pacs_instance.delay(pacs_file.pk)
            n += 1
            if n % opts['batch'] == 0:
                self.stdout.write(f'  ...{n}/{total}')
        verb = 'indexed' if opts['sync'] else 'dispatched'
        self.stdout.write(self.style.SUCCESS(f'{verb} {n} file(s).'))
