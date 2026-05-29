# Enables Postgres pg_trgm so QIDO-RS fuzzy PN matching (?fuzzymatching=true,
# query_parser's `__trigram_similar`) works, and adds the trigram GIN index that
# backs it (D4 in the L2 decisions doc). CREATE EXTENSION needs a superuser DB
# role (the CUBE dev/test Postgres is one).
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('dicomweb', '0002_pacsstudy'),
    ]

    operations = [
        TrigramExtension(),
        migrations.AddIndex(
            model_name='pacsstudy',
            index=GinIndex(
                name='pacsstudy_patientname_trgm',
                fields=['PatientName'],
                opclasses=['gin_trgm_ops'],
            ),
        ),
    ]
