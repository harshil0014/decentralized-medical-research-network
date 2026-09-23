from __future__ import annotations

import io
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from pydicom.dataset import Dataset, FileDataset
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

    # Deliberate fake PHI: the upload path must remove it before IPFS storage.
    ds.PatientName = "PRIVATE^PATIENT"
    ds.PatientID = "MRN-SECRET-001"
    ds.PatientBirthDate = "19800101"
    ds.InstitutionName = "Private Hospital"

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
    pixels = np.arange(16, dtype=np.int16).reshape(4, 4)
    ds.PixelData = pixels.tobytes()
    ds.save_as(stream, enforce_file_format=True)
    return stream.getvalue()


suffix = str(int(time.time() * 1000))
dataset_id = f"DICOM-HE-E2E-{suffix}"
request_id = f"DICOM-HE-REQ-{suffix}"
dicom_bytes = make_ct()

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

# Stored pixels 0..15 become CT values -1024..-1009 HU.
expected = np.arange(16, dtype=np.float64) - 1024.0
references = {
    "MEAN": float(expected.mean()),
    "VARIANCE": float(expected.var()),
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
    assert body["researcher_has_raw_pixels"] is False
    assert body["researcher_has_secret_key"] is False
    assert body["ethereum_status"] == "ENCRYPTED"

    computed = client.post(
        f"/he/dicom/{job_id}/compute",
        headers=researcher,
    )
    assert computed.status_code == 200, computed.text
    assert computed.json()["result_is_ciphertext"] is True
    assert computed.json()["researcher_has_secret_key"] is False
    assert computed.json()["researcher_has_raw_pixels"] is False
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
