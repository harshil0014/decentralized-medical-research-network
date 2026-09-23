from __future__ import annotations

import io
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pydicom
from highdicom.seg import SegmentDescription, Segmentation
from highdicom.seg.enum import SegmentAlgorithmTypeValues, SegmentationTypeValues
from pydicom.dataset import Dataset, FileDataset
from pydicom.sr.coding import Code
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

runtime = Path(tempfile.mkdtemp(prefix="medical-dicom-he-e2e-"))
auth = runtime / "auth"
auth.mkdir(parents=True)
hospital_token = "3" * 64
researcher_token = "4" * 64
(auth / "hospital_api.token").write_text(hospital_token)
(auth / "researcher_api.token").write_text(researcher_token)

os.environ["MEDICAL_REGISTRY_AUTH_DIR"] = str(auth)
os.environ["MEDICAL_REGISTRY_RUNTIME_DIR"] = str(runtime / "runtime")
os.environ["MEDICAL_KEY_ROOT"] = str(runtime / "keys")
os.environ["MEDICAL_ETHEREUM_PRIVATE_LOCATORS"] = str(runtime / "private-locators.json")

from fastapi.testclient import TestClient  # noqa: E402
from backend.app import app  # noqa: E402

client = TestClient(app)
hospital = {"Authorization": f"Bearer {hospital_token}"}
researcher = {"Authorization": f"Bearer {researcher_token}"}


def make_ct() -> bytes:
    meta = Dataset()
    meta.MediaStorageSOPClassUID = CTImageStorage
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.ImplementationClassUID = generate_uid()

    stream = io.BytesIO()
    ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID = CTImageStorage
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.FrameOfReferenceUID = generate_uid()

    # Deliberate fake PHI: the upload path must remove it before IPFS storage.
    ds.PatientName = "PRIVATE^PATIENT"
    ds.PatientID = "MRN-SECRET-001"
    ds.PatientBirthDate = "19800101"
    ds.InstitutionName = "Private Hospital"
    ds.PatientSex = "O"
    ds.StudyDate = "20260101"
    ds.StudyTime = "120000"
    ds.AccessionNumber = "E2E-ACCESSION"
    ds.StudyID = "1"
    ds.SeriesNumber = 1

    ds.Modality = "CT"
    ds.StudyDescription = "Synthetic CT HE validation"
    ds.SeriesDescription = "Synthetic axial CT"
    ds.BurnedInAnnotation = "NO"
    ds.Rows = 4
    ds.Columns = 4
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 1
    ds.RescaleSlope = "1"
    ds.RescaleIntercept = "-1024"
    ds.PixelSpacing = [1.0, 1.0]
    ds.SliceThickness = "1"
    ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    ds.ImagePositionPatient = [0.0, 0.0, 0.0]
    ds.ImageType = ["ORIGINAL", "PRIMARY", "AXIAL"]
    pixels = np.arange(16, dtype=np.int16).reshape(4, 4)
    ds.PixelData = pixels.tobytes()
    ds.save_as(stream, enforce_file_format=True)
    return stream.getvalue()


def make_seg(source_bytes: bytes) -> bytes:
    source = pydicom.dcmread(io.BytesIO(source_bytes))
    description = SegmentDescription(
        segment_number=1,
        segment_label="Test ROI",
        segmented_property_category=Code(
            "123037004", "SCT", "Anatomical Structure"
        ),
        segmented_property_type=Code(
            "91723000", "SCT", "Anatomical structure"
        ),
        algorithm_type=SegmentAlgorithmTypeValues.MANUAL,
    )
    mask = np.zeros((1, 4, 4), dtype=np.uint8)
    mask[0, :2, :2] = 1
    seg = Segmentation(
        source_images=[source],
        pixel_array=mask,
        segmentation_type=SegmentationTypeValues.BINARY,
        segment_descriptions=[description],
        series_instance_uid=generate_uid(),
        series_number=99,
        sop_instance_uid=generate_uid(),
        instance_number=1,
        manufacturer="OpenAI Test",
        manufacturer_model_name="Synthetic",
        software_versions="1",
        device_serial_number="1",
    )
    stream = io.BytesIO()
    seg.save_as(stream, enforce_file_format=True)
    return stream.getvalue()


suffix = str(int(time.time() * 1000))
dataset_id = f"DICOM-HE-E2E-{suffix}"
seg_dataset_id = f"DICOM-SEG-E2E-{suffix}"
request_id = f"DICOM-HE-REQ-{suffix}"
dicom_bytes = make_ct()
seg_bytes = make_seg(dicom_bytes)

upload = client.post(
    "/datasets/upload",
    headers=hospital,
    data={
        "dataset_id": dataset_id,
        "data_type": "",
        "metadata_summary": "",
        "consent_state": "ACTIVE",
    },
    files={"file": ("ct.dcm", io.BytesIO(dicom_bytes), "application/dicom")},
)
assert upload.status_code == 200, upload.text
assert upload.json()["storageState"] == "PRIVATE_READY"
assert upload.json()["dataType"] == "DICOM_CT"

seg_upload = client.post(
    "/datasets/upload",
    headers=hospital,
    data={
        "dataset_id": seg_dataset_id,
        "data_type": "",
        "metadata_summary": "",
        "consent_state": "ACTIVE",
    },
    files={"file": ("roi_seg.dcm", io.BytesIO(seg_bytes), "application/dicom")},
)
assert seg_upload.status_code == 200, seg_upload.text
assert seg_upload.json()["storageState"] == "PRIVATE_READY"
assert seg_upload.json()["dataType"] == "DICOM_SEG"

segment_catalog = client.get(
    f"/he/dicom/segments/{seg_dataset_id}",
    headers=hospital,
)
assert segment_catalog.status_code == 200, segment_catalog.text
assert segment_catalog.json()["segment_count"] == 1
assert segment_catalog.json()["segments"][0]["segment_number"] == 1
assert segment_catalog.json()["segments"][0]["segment_label"] == "Test ROI"

request = client.post(
    "/requests",
    headers=researcher,
    json={
        "request_id": request_id,
        "dataset_id": dataset_id,
        "purpose": "Encrypted quantitative CT analysis",
    },
)
assert request.status_code == 200, request.text
assert request.json()["status"] == "PENDING"

approve = client.post(
    f"/requests/{request_id}/approve",
    headers=hospital,
)
assert approve.status_code == 200, approve.text
assert approve.json()["status"] == "APPROVED"

capabilities = client.get("/he/dicom/capabilities", headers=researcher)
assert capabilities.status_code == 200, capabilities.text
assert capabilities.json()["engine"] == "Microsoft SEAL 4.4 CKKS"
assert "SKEWNESS" in capabilities.json()["analyses"]
assert "KURTOSIS" in capabilities.json()["analyses"]
assert capabilities.json()["privacy"]["researcher_receives_he_secret_key"] is False

# Stored pixels 0..15 become CT values -1024..-1009 HU.
expected = np.arange(16, dtype=np.float64) - 1024.0
expected_mean = float(expected.mean())
expected_variance = float(expected.var())
expected_centered = expected - expected_mean
expected_m3 = float(np.mean(expected_centered ** 3))
expected_m4 = float(np.mean(expected_centered ** 4))
references = {
    "MEAN": expected_mean,
    "VARIANCE": expected_variance,
    "STANDARD_DEVIATION": float(np.sqrt(expected_variance)),
    "ROOT_MEAN_SQUARED": float(np.sqrt(np.mean(expected ** 2))),
    "TOTAL_ENERGY": float(np.sum(expected ** 2)),
    "SKEWNESS": expected_m3 / (expected_variance ** 1.5),
    "KURTOSIS": expected_m4 / (expected_variance ** 2),
}

for analysis, reference in references.items():
    created = client.post(
        "/he/dicom/encrypt",
        headers=hospital,
        json={
            "dataset_id": dataset_id,
            "request_id": request_id,
            "analysis": analysis,
            "scope": "WHOLE_VOLUME",
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    job_id = body["job_id"]
    assert body["modality"] == "CT"
    assert body["unit"] == "HU"
    assert body["voxel_count"] == 16
    assert body["he_mode"] == "RAW_VOXELS"
    assert body["representation"] == "ENCRYPTED_RAW_VOXELS"
    assert body["researcher_has_plaintext_pixels"] is False
    assert body["researcher_has_raw_pixels"] is False
    assert body["researcher_has_encrypted_raw_voxels"] is True
    assert body["researcher_has_secret_key"] is False
    assert body["ethereum_status"] == "ENCRYPTED"

    computed = client.post(
        f"/he/dicom/{job_id}/compute",
        headers=researcher,
    )
    assert computed.status_code == 200, computed.text
    assert computed.json()["result_is_ciphertext"] is True
    assert computed.json()["researcher_has_plaintext_pixels"] is False
    assert computed.json()["researcher_has_raw_pixels"] is False
    assert computed.json()["researcher_has_encrypted_raw_voxels"] is True
    assert computed.json()["researcher_has_secret_key"] is False
    assert computed.json()["ethereum_status"] == "COMPUTED"

    decrypted = client.post(
        f"/he/dicom/{job_id}/decrypt",
        headers=hospital,
    )
    assert decrypted.status_code == 200, decrypted.text
    value = float(decrypted.json()["value"])
    tolerance = max(0.02, abs(reference) * 2e-5)
    assert abs(value - reference) <= tolerance, (analysis, value, reference)
    assert decrypted.json()["ethereum_status"] == "DECRYPTED"
    assert decrypted.json()["ckks_approximate"] is True

    ledger = client.get(
        f"/he/dicom/{job_id}/ledger",
        headers=researcher,
    )
    history = client.get(
        f"/he/dicom/{job_id}/history",
        headers=researcher,
    )
    assert ledger.status_code == 200, ledger.text
    assert ledger.json()["status"] == "DECRYPTED"
    assert ledger.json()["metric"].startswith(f"DICOM:{analysis}:")
    assert history.status_code == 200, history.text
    assert [x["value"]["status"] for x in history.json()] == [
        "ENCRYPTED",
        "COMPUTED",
        "DECRYPTED",
    ]
    print(f"DICOM {analysis} E2E: PASS ({value:.6f})")

roi_created = client.post(
    "/he/dicom/encrypt",
    headers=hospital,
    json={
        "dataset_id": dataset_id,
        "request_id": request_id,
        "analysis": "MEAN",
        "he_mode": "RAW_VOXELS",
        "scope": "ROI_BOX",
        "roi_box": {
            "slice_start": 0,
            "slice_end": 1,
            "row_start": 0,
            "row_end": 2,
            "col_start": 0,
            "col_end": 2,
        },
    },
)
assert roi_created.status_code == 200, roi_created.text
roi_body = roi_created.json()
assert roi_body["voxel_count"] == 4
assert roi_body["selected_shape"] == [1, 2, 2]
assert roi_body["scope"] == "ROI_BOX"
assert roi_body["researcher_has_encrypted_raw_voxels"] is True
roi_job = roi_body["job_id"]

roi_computed = client.post(
    f"/he/dicom/{roi_job}/compute",
    headers=researcher,
)
assert roi_computed.status_code == 200, roi_computed.text
roi_decrypted = client.post(
    f"/he/dicom/{roi_job}/decrypt",
    headers=hospital,
)
assert roi_decrypted.status_code == 200, roi_decrypted.text
roi_value = float(roi_decrypted.json()["value"])
assert abs(roi_value - (-1021.5)) <= 0.02, roi_value
print(f"DICOM RAW ROI E2E: PASS ({roi_value:.6f})")

seg_created = client.post(
    "/he/dicom/encrypt",
    headers=hospital,
    json={
        "dataset_id": dataset_id,
        "request_id": request_id,
        "analysis": "MEAN",
        "he_mode": "RAW_VOXELS",
        "scope": "DICOM_SEG",
        "segmentation_dataset_id": seg_dataset_id,
        "segment_number": 1,
    },
)
assert seg_created.status_code == 200, seg_created.text
seg_body = seg_created.json()
assert seg_body["scope"] == "DICOM_SEG"
assert seg_body["segment_number"] == 1
assert seg_body["segment_label"] == "Test ROI"
assert seg_body["segment_voxel_count"] == 4
assert seg_body["segmentation_dataset_id"] == seg_dataset_id
assert seg_body["segmentation_dataset_sha256_verified"] is True
assert seg_body["researcher_has_encrypted_raw_voxels"] is True
seg_job = seg_body["job_id"]

seg_computed = client.post(
    f"/he/dicom/{seg_job}/compute",
    headers=researcher,
)
assert seg_computed.status_code == 200, seg_computed.text
seg_decrypted = client.post(
    f"/he/dicom/{seg_job}/decrypt",
    headers=hospital,
)
assert seg_decrypted.status_code == 200, seg_decrypted.text
seg_value = float(seg_decrypted.json()["value"])
assert abs(seg_value - (-1021.5)) <= 0.02, seg_value
assert seg_decrypted.json()["segment_number"] == 1
assert seg_decrypted.json()["segment_label"] == "Test ROI"
seg_ledger = client.get(
    f"/he/dicom/{seg_job}/ledger",
    headers=researcher,
)
assert seg_ledger.status_code == 200, seg_ledger.text
assert ":DICOM_SEG:RAW_VOXELS:SEG1" in seg_ledger.json()["metric"]
print(f"DICOM SEG RAW-VOXEL E2E: PASS ({seg_value:.6f})")

block_created = client.post(
    "/he/dicom/encrypt",
    headers=hospital,
    json={
        "dataset_id": dataset_id,
        "request_id": request_id,
        "analysis": "MEAN",
        "he_mode": "BLOCK_STATS",
        "scope": "WHOLE_VOLUME",
    },
)
assert block_created.status_code == 200, block_created.text
block_body = block_created.json()
assert block_body["he_mode"] == "BLOCK_STATS"
assert block_body["representation"] == "ENCRYPTED_BLOCK_SUFFICIENT_STATISTICS"
assert block_body["researcher_has_encrypted_raw_voxels"] is False
block_job = block_body["job_id"]

block_computed = client.post(
    f"/he/dicom/{block_job}/compute",
    headers=researcher,
)
assert block_computed.status_code == 200, block_computed.text
block_decrypted = client.post(
    f"/he/dicom/{block_job}/decrypt",
    headers=hospital,
)
assert block_decrypted.status_code == 200, block_decrypted.text
block_value = float(block_decrypted.json()["value"])
assert abs(block_value - float(expected.mean())) <= 0.02, block_value
print(f"DICOM BLOCK-STATS FALLBACK E2E: PASS ({block_value:.6f})")

revoke = client.post(
    f"/requests/{request_id}/revoke",
    headers=hospital,
)
assert revoke.status_code == 200, revoke.text

denied = client.post(
    "/he/dicom/encrypt",
    headers=hospital,
    json={
        "dataset_id": dataset_id,
        "request_id": request_id,
        "analysis": "MEAN",
    },
)
assert denied.status_code == 403, denied.text

print("DICOM + AES + IPFS + CKKS + ETHEREUM E2E: PASS")
