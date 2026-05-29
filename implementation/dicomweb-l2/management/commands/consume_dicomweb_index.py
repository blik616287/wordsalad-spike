"""
VARIANT C (hybrid) consumer prototype.

Subscribes to a NATS subject carrying oxidicom-parsed DICOM tags and indexes
``PACSInstance``/``PACSStudy`` from each message -- WITHOUT re-reading the .dcm
file. This is the efficient path the L2 architecture doc recommends (D1): rather
than CUBE re-parsing every file (``index_pacs_instance``), an *extended* oxidicom
publishes the tags it already parsed in Rust during C-STORE, and this small
in-network consumer upserts the index directly.

oxidicom's existing LONK subject ``oxidicom.<pacs>.<series>`` carries progress
only (counts), so variant C needs a NEW tag-bearing event. This consumer
subscribes to ``oxidicom-meta.>`` by default; the event payload (JSON) is::

    {
      "pacs_name": "...", "fname": "SERVICES/PACS/.../<sop>.dcm",
      "StudyInstanceUID": "...", "SeriesInstanceUID": "...", "SOPInstanceUID": "...",
      "SOPClassUID": "...", "InstanceNumber": 1, "Rows": 256, "Columns": 256,
      "BitsAllocated": 16, "NumberOfFrames": 1, "TransferSyntaxUID": "...",
      "PatientID": "...", "PatientName": "...", "PatientSex": "F",
      "StudyDate": "20230102", "StudyTime": "143000", "AccessionNumber": "...",
      "StudyDescription": "...", "Modality": "MR"
    }

Run:
    python manage.py consume_dicomweb_index
    python manage.py consume_dicomweb_index --count 3   # exit after 3 messages
"""
import asyncio
import json
import os

import nats
from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand

from dicomweb.tasks import index_from_metadata


class Command(BaseCommand):
    help = ('Index PACSInstance/PACSStudy from oxidicom-pushed metadata over '
            'NATS (variant C); no file re-read.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--servers',
            default=os.environ.get('NATS_ADDRESS', 'nats://nats:4222'),
            help='NATS server URL(s).')
        parser.add_argument('--subject', default='oxidicom-meta.>',
                            help='NATS subject to subscribe to.')
        parser.add_argument('--count', type=int, default=0,
                            help='Exit after N messages (0 = run forever).')

    def handle(self, *args, **opts):
        asyncio.run(self._run(opts['servers'], opts['subject'], opts['count']))

    async def _run(self, servers, subject, count):
        nc = await nats.connect(servers)
        self.stdout.write(f'consume_dicomweb_index: connected {servers}, '
                          f'subject={subject!r}, count={count or "∞"}')
        seen = 0
        done = asyncio.Event()

        async def cb(msg):
            nonlocal seen
            try:
                meta = json.loads(msg.data)
            except Exception as exc:
                self.stderr.write(f'  bad message on {msg.subject}: {exc}')
                return
            ok = await sync_to_async(index_from_metadata,
                                     thread_sensitive=True)(meta)
            seen += 1
            self.stdout.write(f'  [{seen}] indexed={ok} '
                              f'sop={meta.get("SOPInstanceUID", "?")}')
            if count and seen >= count:
                done.set()

        await nc.subscribe(subject, cb=cb)
        try:
            if count:
                await done.wait()
            else:
                while True:
                    await asyncio.sleep(3600)
        finally:
            await nc.drain()
