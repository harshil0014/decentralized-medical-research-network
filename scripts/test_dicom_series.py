from pathlib import Path
from zipfile import ZipFile
import tempfile

import pydicom
from pydicom.dataset import Dataset, FileDataset
from pydicom.sequence import Sequence
from pydicom.uid import ExplicitVRLittleEndian, MRImageStorage, generate_uid

from backend.dicom_series import deidentify_dicom_series_zip
from backend.dicom_utils import deidentify_dataset


SENTINEL = "PHI-SENTINEL"


def contains_sentinel(dataset: pydicom.Dataset) -> bool:
    for element in dataset:
        if element.VR == "SQ":
            for item in element.value:
                if contains_sentinel(item):
                    return True
            continue
        if SENTINEL in str(element.value):
            return True
    return False


def has_private_tag(dataset: pydicom.Dataset) -> bool:
    for element in dataset:
        if element.tag.is_private:
            return True
        if element.VR == "SQ":
            for item in element.value:
                if has_private_tag(item):
                    return True
    return False


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    raw_dir = root / "raw"
    raw_dir.mkdir()
    zip_path = root / "series.zip"

    study_uid = generate_uid()
    series_uid = generate_uid()

    for i in range(1, 4):
        path = raw_dir / f"patient-secret-{i}.dcm"

        meta = Dataset()
        meta.MediaStorageSOPClassUID = MRImageStorage
        meta.MediaStorageSOPInstanceUID = generate_uid()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        meta.ImplementationClassUID = generate_uid()
        meta.SourceApplicationEntityTitle = SENTINEL

        ds = FileDataset(
            str(path),
            {},
            file_meta=meta,
            preamble=b"X" * 128,
        )

        ds.SOPClassUID = MRImageStorage
        ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
        ds.StudyInstanceUID = study_uid
        ds.SeriesInstanceUID = series_uid

        # Direct identifiers plus fields not covered by the old hand list.
        ds.PatientName = SENTINEL
        ds.PatientID = SENTINEL
        ds.PatientBirthDate = "19900101"
        ds.InstitutionName = SENTINEL
        ds.StudyDescription = SENTINEL
        ds.SeriesDescription = SENTINEL
        ds.ProtocolName = SENTINEL
        ds.StationName = SENTINEL
        ds.DeviceSerialNumber = SENTINEL
        ds.PatientReligiousPreference = SENTINEL
        ds.RequestingService = SENTINEL

        nested = Dataset()
        nested.AccessionNumber = SENTINEL
        nested.RequestedProcedureID = SENTINEL
        ds.RequestAttributesSequence = Sequence([nested])

        ds.add_new((0x0011, 0x0010), "LO", "PRIVATE-CREATOR")
        ds.add_new((0x0011, 0x1010), "LO", SENTINEL)

        ds.Modality = "MR"
        ds.BurnedInAnnotation = "NO"
        ds.RecognizableVisualFeatures = "NO"
        ds.InstanceNumber = i
        ds.Rows = 2
        ds.Columns = 2
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.BitsAllocated = 8
        ds.BitsStored = 8
        ds.HighBit = 7
        ds.PixelRepresentation = 0
        ds.PixelData = bytes([i, i + 1, i + 2, i + 3])
        ds.save_as(path, enforce_file_format=True)

    with ZipFile(zip_path, "w") as archive:
        for item in sorted(raw_dir.glob("*.dcm")):
            archive.write(item, arcname=item.name)

    # CT/MR pixel handling is fail-closed: both DICOM flags and an explicit
    # Hospital visual review attestation are required.
    missing_attestation = Dataset()
    missing_attestation.Modality = "CT"
    missing_attestation.BurnedInAnnotation = "NO"
    missing_attestation.RecognizableVisualFeatures = "NO"
    try:
        deidentify_dataset(missing_attestation)
    except ValueError as exc:
        assert "visual_phi_reviewed=true" in str(exc)
    else:
        raise AssertionError("CT/MR without visual review attestation must be rejected")

    missing_flag = Dataset()
    missing_flag.Modality = "MR"
    missing_flag.BurnedInAnnotation = "NO"
    try:
        deidentify_dataset(missing_flag, visual_phi_reviewed=True)
    except ValueError as exc:
        assert "RecognizableVisualFeatures=NO" in str(exc)
    else:
        raise AssertionError("CT/MR with unknown visual-feature status must be rejected")

    result = deidentify_dicom_series_zip(
        zip_path,
        visual_phi_reviewed=True,
    )

    clean = []
    with ZipFile(zip_path, "r") as archive:
        names = sorted(archive.namelist())
        assert names == [
            "slice_00001.dcm",
            "slice_00002.dcm",
            "slice_00003.dcm",
        ]
        for name in names:
            with archive.open(name) as handle:
                clean.append(pydicom.dcmread(handle))

    study_uids = {str(ds.StudyInstanceUID) for ds in clean}
    series_uids = {str(ds.SeriesInstanceUID) for ds in clean}
    sop_uids = {str(ds.SOPInstanceUID) for ds in clean}

    assert result["modality"] == "MR"
    assert result["slice_count"] == 3
    assert result["rows"] == 2
    assert result["columns"] == 2

    assert len(study_uids) == 1
    assert len(series_uids) == 1
    assert len(sop_uids) == 3
    assert study_uid not in study_uids
    assert series_uid not in series_uids

    for ds in clean:
        assert ds.PatientIdentityRemoved == "YES"
        assert "PS3.15 2024b" in str(ds.DeidentificationMethod)
        assert not contains_sentinel(ds)
        assert not has_private_tag(ds)
        assert ds.preamble == b"\x00" * 128
        assert SENTINEL not in str(ds.file_meta)
        assert "StudyDescription" not in ds
        assert "SeriesDescription" not in ds
        assert "ProtocolName" not in ds

print("DICOM PS3.15 2024b HEADER DE-IDENTIFICATION: PASS")
