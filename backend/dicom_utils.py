from pathlib import Path
import hashlib

import pydicom
from pydicom.uid import generate_uid


SENSITIVE_KEYWORDS = [
    "PatientName",
    "PatientID",
    "IssuerOfPatientID",
    "PatientBirthDate",
    "PatientBirthTime",
    "PatientSex",
    "PatientAge",
    "PatientSize",
    "PatientWeight",
    "PatientAddress",
    "PatientTelephoneNumbers",
    "OtherPatientIDsSequence",
    "OtherPatientNames",
    "PatientMotherBirthName",
    "PatientComments",
    "MedicalRecordLocator",
    "AdditionalPatientHistory",
    "InstitutionName",
    "InstitutionAddress",
    "InstitutionalDepartmentName",
    "ReferringPhysicianName",
    "PerformingPhysicianName",
    "OperatorsName",
    "PhysiciansOfRecord",
    "NameOfPhysiciansReadingStudy",
    "RequestingPhysician",
    "AccessionNumber",
    "StudyID",
    "StudyDate",
    "StudyTime",
    "SeriesDate",
    "SeriesTime",
    "AcquisitionDate",
    "AcquisitionTime",
    "ContentDate",
    "ContentTime",
    "ImageComments",
]


def _reject_visual_identity_risk(ds: pydicom.Dataset) -> None:
    if str(getattr(ds, "BurnedInAnnotation", "")).upper() == "YES":
        raise ValueError(
            "DICOM declares burned-in annotation; clean pixel data before upload"
        )
    if str(getattr(ds, "RecognizableVisualFeatures", "")).upper() == "YES":
        raise ValueError(
            "DICOM declares recognizable visual features; defacing/cleaning is required"
        )


def _remove_overlay_data(ds: pydicom.Dataset) -> None:
    # Overlay planes may contain annotations that are not part of PixelData.
    for tag in list(ds.keys()):
        if 0x6000 <= tag.group <= 0x60FF:
            del ds[tag]


def deidentify_dataset(ds: pydicom.Dataset, uid_map: dict[str, str] | None = None) -> None:
    _reject_visual_identity_risk(ds)

    for keyword in SENSITIVE_KEYWORDS:
        if keyword in ds:
            del ds[keyword]

    ds.remove_private_tags()
    _remove_overlay_data(ds)

    if uid_map is None:
        uid_map = {}

    def remap(uid) -> str:
        value = str(uid)
        if value not in uid_map:
            uid_map[value] = generate_uid()
        return uid_map[value]

    identity_uid_keywords = {
        "StudyInstanceUID",
        "SeriesInstanceUID",
        "FrameOfReferenceUID",
        "SynchronizationFrameOfReferenceUID",
        "SOPInstanceUID",
        "ReferencedSOPInstanceUID",
    }

    def remap_dataset_uids(dataset: pydicom.Dataset) -> None:
        for element in dataset:
            if element.VR == "SQ":
                for item in element.value:
                    remap_dataset_uids(item)
                continue

            if element.keyword in identity_uid_keywords and element.value:
                element.value = remap(element.value)

    remap_dataset_uids(ds)

    if "SOPInstanceUID" in ds and "MediaStorageSOPInstanceUID" in ds.file_meta:
        ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID

    ds.PatientIdentityRemoved = "YES"
    ds.DeidentificationMethod = (
        "PHI removed; private tags/overlays removed; identity UIDs remapped"
    )


def deidentify_dicom_in_place(path: str | Path) -> dict:
    path = Path(path)
    ds = pydicom.dcmread(path)

    if "PixelData" not in ds:
        raise ValueError("DICOM object has no PixelData")

    deidentify_dataset(ds)
    ds.save_as(path, enforce_file_format=True)

    return {
        "modality": getattr(ds, "Modality", None),
        "study_description": getattr(ds, "StudyDescription", None),
        "series_description": getattr(ds, "SeriesDescription", None),
        "rows": getattr(ds, "Rows", None),
        "columns": getattr(ds, "Columns", None),
        "number_of_frames": getattr(ds, "NumberOfFrames", None),
    }


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()

    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()
