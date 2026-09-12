from pathlib import Path
import hashlib

import pydicom
from pydicom.uid import generate_uid


SENSITIVE_KEYWORDS = [
    "PatientName",
    "PatientID",
    "PatientBirthDate",
    "PatientBirthTime",
    "PatientSex",
    "PatientAddress",
    "PatientTelephoneNumbers",
    "OtherPatientIDsSequence",
    "InstitutionName",
    "InstitutionAddress",
    "ReferringPhysicianName",
    "PerformingPhysicianName",
    "OperatorsName",
    "AccessionNumber",
    "StudyID",
    "StudyDate",
    "SeriesDate",
    "AcquisitionDate",
    "ContentDate",
]


def deidentify_dicom_in_place(path: str | Path) -> dict:
    path = Path(path)
    ds = pydicom.dcmread(path)

    for keyword in SENSITIVE_KEYWORDS:
        if keyword in ds:
            del ds[keyword]

    ds.remove_private_tags()

    for keyword in (
        "StudyInstanceUID",
        "SeriesInstanceUID",
        "SOPInstanceUID",
        "FrameOfReferenceUID",
    ):
        if keyword in ds:
            setattr(ds, keyword, generate_uid())

    if (
        "SOPInstanceUID" in ds
        and "MediaStorageSOPInstanceUID" in ds.file_meta
    ):
        ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID

    ds.PatientIdentityRemoved = "YES"
    ds.DeidentificationMethod = "Prototype PHI removal + private tags + UID remap"

    ds.save_as(path, enforce_file_format=True)

    return {
        "modality": getattr(ds, "Modality", None),
        "study_description": getattr(ds, "StudyDescription", None),
        "series_description": getattr(ds, "SeriesDescription", None),
        "rows": getattr(ds, "Rows", None),
        "columns": getattr(ds, "Columns", None),
    }


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()

    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()
