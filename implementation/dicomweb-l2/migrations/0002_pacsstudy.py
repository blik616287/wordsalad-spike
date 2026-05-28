# Hand-written to illustrate the migration that `just makemigrations` would
# generate for the PACSStudy model added in this spike (L2). In a real CUBE
# checkout, regenerate with `just makemigrations dicomweb` rather than editing
# this by hand (Phase A validated that flow / zero drift).
#
# Companion migration (in the pacsfiles app, NOT here) adds the nullable FK
# `PACSSeries.study -> dicomweb.PACSStudy`; see the migration note at the bottom
# of dicomweb/models.py. That pacsfiles migration depends on this one.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pacsfiles', '0009_pacsseries_bodypartexamined_pacsseries_manufacturer_and_more'),
        ('dicomweb', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='PACSStudy',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('creation_date', models.DateTimeField(auto_now_add=True)),
                ('PatientID', models.CharField(db_index=True, max_length=100)),
                ('PatientName', models.CharField(blank=True, max_length=150)),
                ('PatientBirthDate', models.DateField(blank=True, null=True)),
                ('PatientSex', models.CharField(blank=True, choices=[('M', 'Male'), ('F', 'Female'), ('O', 'Other')], max_length=1)),
                ('StudyInstanceUID', models.CharField(db_index=True, max_length=100)),
                ('StudyDate', models.DateField(blank=True, db_index=True, null=True)),
                ('StudyTime', models.TimeField(blank=True, null=True)),
                ('AccessionNumber', models.CharField(blank=True, db_index=True, max_length=100)),
                ('StudyDescription', models.CharField(blank=True, max_length=400)),
                ('ReferringPhysicianName', models.CharField(blank=True, max_length=150)),
                ('ModalitiesInStudy', models.CharField(blank=True, max_length=255)),
                ('NumberOfStudyRelatedSeries', models.IntegerField(default=0)),
                ('NumberOfStudyRelatedInstances', models.IntegerField(default=0)),
                ('pacs', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='studies', to='pacsfiles.pacs')),
            ],
            options={
                'verbose_name_plural': 'PACS studies',
                'ordering': ('pacs', '-StudyDate', 'StudyInstanceUID'),
                'unique_together': {('pacs', 'StudyInstanceUID')},
            },
        ),
        migrations.AddIndex(
            model_name='pacsstudy',
            index=models.Index(fields=['pacs', 'StudyInstanceUID'], name='pacsstudy_pacs_study_idx'),
        ),
        migrations.AddIndex(
            model_name='pacsstudy',
            index=models.Index(fields=['pacs', 'PatientID'], name='pacsstudy_pacs_patient_idx'),
        ),
    ]
