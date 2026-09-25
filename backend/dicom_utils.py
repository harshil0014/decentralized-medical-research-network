from pathlib import Path
import hashlib
import copy
import hmac
import os

import pydicom
from pydicom.uid import generate_uid
from dicomanonymizer import anonymize_dataset as ps315_anonymize_dataset
from dicomanonymizer.simpledicomanonymizer import initialize_actions_2024b


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
    "StudyDescription",
    "SeriesDescription",
    "ProtocolName",
    "RequestedProcedureDescription",
    "ScheduledProcedureStepDescription",
    "PerformedProcedureStepDescription",
    "AdmittingDiagnosesDescription",
    "DerivationDescription",
    "ImageComments",
    "PatientState",
]


VISUAL_PHI_REVIEW_MODALITIES = {"CT", "MR"}


def _reject_visual_identity_risk(
    ds: pydicom.Dataset,
    *,
    visual_phi_reviewed: bool = False,
) -> None:
    burned_in = str(getattr(ds, "BurnedInAnnotation", "")).strip().upper()
    recognizable = str(
        getattr(ds, "RecognizableVisualFeatures", "")
    ).strip().upper()
    modality = str(getattr(ds, "Modality", "")).strip().upper()

    if burned_in == "YES":
        raise ValueError(
            "DICOM declares burned-in annotation; clean pixel data before upload"
        )
    if recognizable == "YES":
        raise ValueError(
            "DICOM declares recognizable visual features; defacing/cleaning is required"
        )

    if modality in VISUAL_PHI_REVIEW_MODALITIES:
        if burned_in != "NO":
            raise ValueError(
                "CT/MR upload requires BurnedInAnnotation=NO after pixel review"
            )
        if recognizable != "NO":
            raise ValueError(
                "CT/MR upload requires RecognizableVisualFeatures=NO after visual review"
            )
        if not visual_phi_reviewed:
            raise ValueError(
                "CT/MR upload requires Hospital visual_phi_reviewed=true attestation"
            )


def _remove_overlay_data(ds: pydicom.Dataset) -> None:
    # Overlay planes may contain annotations that are not part of PixelData.
    for tag in list(ds.keys()):
        if 0x6000 <= tag.group <= 0x60FF:
            del ds[tag]


def deidentify_dataset(
    ds: pydicom.Dataset,
    uid_map: dict[str, str] | None = None,
    *,
    visual_phi_reviewed: bool = False,
) -> None:
    original = copy.deepcopy(ds)
    _reject_visual_identity_risk(
        ds,
        visual_phi_reviewed=visual_phi_reviewed,
    )

    # Apply the maintained DICOM PS3.15 2024b Basic Application
    # Confidentiality Profile header rule table first. Our stricter
    # cleanup below is additive and deliberately removes all private tags.
    ps315_anonymize_dataset(
        ds,
        delete_private_tags=True,
        base_rules_gen=initialize_actions_2024b,
    )

    def scrub_dataset(dataset: pydicom.Dataset) -> None:
        for keyword in SENSITIVE_KEYWORDS:
            if keyword in dataset:
                del dataset[keyword]

        dataset.remove_private_tags()
        _remove_overlay_data(dataset)

        for element in list(dataset):
            if element.VR == "SQ":
                for item in element.value:
                    scrub_dataset(item)

    scrub_dataset(ds)

    if uid_map is None:
        uid_map = {}

    def remap(uid) -> str:
        value = str(uid)
        if value not in uid_map:
            secret = os.environ.get("MEDICAL_MASTER_KEY_HEX", "")
            if len(secret) == 64:
                digest = hmac.new(bytes.fromhex(secret), value.encode("ascii"), hashlib.sha256).digest()
                uid_map[value] = f"2.25.{int.from_bytes(digest[:16], 'big')}"
            else:
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

    def remap_dataset_uids(dataset: pydicom.Dataset, source: pydicom.Dataset) -> None:
        for element in dataset:
            if element.VR == "SQ":
                source_sequence = source.get(element.keyword, [])
                for index, item in enumerate(element.value):
                    if index < len(source_sequence):
                        remap_dataset_uids(item, source_sequence[index])
                continue

            if element.keyword in identity_uid_keywords:
                original_value = source.get(element.keyword)
                if original_value:
                    element.value = remap(original_value)
            elif element.keyword == "ReferencedSOPClassUID":
                original_value = source.get(element.keyword)
                if original_value:
                    element.value = original_value

    remap_dataset_uids(ds, original)

    if "SOPInstanceUID" in ds and "MediaStorageSOPInstanceUID" in ds.file_meta:
        ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID

    for keyword in (
        "SourceApplicationEntityTitle",
        "SendingApplicationEntityTitle",
        "ReceivingApplicationEntityTitle",
        "PrivateInformationCreatorUID",
        "PrivateInformation",
    ):
        if keyword in ds.file_meta:
            del ds.file_meta[keyword]

    ds.preamble = b"\x00" * 128

    ds.PatientIdentityRemoved = "YES"
    # LO permits at most 64 characters. Pixel review is a separate Hospital
    # attestation, not an automated part of header de-identification.
    ds.DeidentificationMethod = "PS3.15 2024b Basic Profile; UID remap; pixel visual review"


def deidentify_dicom_in_place(
    path: str | Path,
    *,
    visual_phi_reviewed: bool = False,
) -> dict:
    path = Path(path)
    ds = pydicom.dcmread(path)

    if "PixelData" not in ds:
        raise ValueError("DICOM object has no PixelData")

    deidentify_dataset(
        ds,
        visual_phi_reviewed=visual_phi_reviewed,
    )
    ds.save_as(path, enforce_file_format=True)

    return {
        "modality": getattr(ds, "Modality", None),
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
