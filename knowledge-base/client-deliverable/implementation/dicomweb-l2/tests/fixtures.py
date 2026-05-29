"""
Synthetic pydicom dataset fixtures for the DICOMweb tests.

Builds in-memory DICOM objects (and their PS3.10 byte serializations) without
touching disk or a PACS, so the parser / renderer / STOW tests can run inside a
CUBE checkout without external sample data.

pydicom reference: https://pydicom.github.io/
"""
import io

import pydicom
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import (ExplicitVRLittleEndian, generate_uid,
                         CTImageStorage)


def make_dataset(*, study_uid=None, series_uid=None, sop_uid=None,
                 patient_name='DOE^JANE', patient_id='MRN0001',
                 modality='CT', study_date='20230102', study_time='143052',
                 series_number=1, instance_number=1, rows=256, columns=256,
                 sop_class_uid=CTImageStorage, accession='A12345',
                 study_description='CHEST CT', series_description='AXIAL',
                 with_pixels=False):
    """
    Return a pydicom ``FileDataset``-like ``Dataset`` populated with the
    attributes the DICOMweb index/serializers use. UIDs default to fresh ones.
    """
    study_uid = study_uid or generate_uid()
    series_uid = series_uid or generate_uid()
    sop_uid = sop_uid or generate_uid()

    ds = Dataset()
    ds.PatientName = patient_name
    ds.PatientID = patient_id
    ds.PatientBirthDate = '19800101'
    ds.PatientSex = 'F'

    ds.StudyInstanceUID = study_uid
    ds.StudyDate = study_date
    ds.StudyTime = study_time
    ds.AccessionNumber = accession
    ds.StudyDescription = study_description
    ds.ReferringPhysicianName = 'SMITH^JOHN'

    ds.SeriesInstanceUID = series_uid
    ds.Modality = modality
    ds.SeriesNumber = series_number
    ds.SeriesDescription = series_description
    ds.BodyPartExamined = 'CHEST'
    ds.Manufacturer = 'ACME'
    ds.ProtocolName = 'ROUTINE'

    ds.SOPClassUID = sop_class_uid
    ds.SOPInstanceUID = sop_uid
    ds.InstanceNumber = instance_number
    ds.Rows = rows
    ds.Columns = columns
    ds.BitsAllocated = 16
    ds.NumberOfFrames = 1

    if with_pixels:
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = 'MONOCHROME2'
        ds.PixelRepresentation = 0
        ds.BitsStored = 16
        ds.HighBit = 15
        ds.PixelData = (b'\x00\x00' * rows * columns)

    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = sop_class_uid
    file_meta.MediaStorageSOPInstanceUID = sop_uid
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta = file_meta
    return ds


def dataset_to_bytes(ds):
    """
    Serialize a dataset to PS3.10 bytes (128-byte preamble + ``DICM`` + meta +
    dataset).

    pydicom 3.x derives the dataset encoding from ``file_meta.TransferSyntaxUID``
    (the deprecated ``is_little_endian`` / ``is_implicit_VR`` / ``write_like_original``
    knobs are removed in 4.0). ``enforce_file_format=True`` is the 3.x idiom that
    guarantees a conformant PS3.10 stream (preamble + ``DICM`` magic + a valid
    File Meta Information group), which is exactly what STOW-RS parts must carry
    and what the WADO retrieve test greps for (``b'DICM'``).
    Ref: https://pydicom.github.io/pydicom/stable/reference/generated/pydicom.filewriter.dcmwrite.html
    """
    buf = io.BytesIO()
    fd = pydicom.dataset.FileDataset(
        None, ds, file_meta=ds.file_meta, preamble=b'\x00' * 128)
    pydicom.dcmwrite(buf, fd, enforce_file_format=True)
    return buf.getvalue()


def build_multipart_related(byte_parts, boundary='DICOMWEBBOUNDARY'):
    """
    Assemble a ``multipart/related; type="application/dicom"`` body and its
    Content-Type from a list of DICOM byte blobs (matches the STOW wire format).
    """
    crlf = b'\r\n'
    delim = b'--' + boundary.encode('ascii')
    chunks = []
    for blob in byte_parts:
        chunks.append(delim + crlf)
        chunks.append(b'Content-Type: application/dicom' + crlf)
        chunks.append(b'Content-Length: ' + str(len(blob)).encode() + crlf)
        chunks.append(crlf)
        chunks.append(blob)
        chunks.append(crlf)
    chunks.append(delim + b'--' + crlf)
    body = b''.join(chunks)
    content_type = (f'multipart/related; type="application/dicom"; '
                    f'boundary={boundary}')
    return body, content_type
