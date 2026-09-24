from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import io
import os
import tempfile
import time

runtime = Path(tempfile.mkdtemp(prefix="medical-api-e2e-"))
auth = runtime / "auth"
auth.mkdir(parents=True)
hospital_token = "1" * 64
researcher_token = "2" * 64
(auth / "hospital_api.token").write_text(hospital_token)
(auth / "researcher_api.token").write_text(researcher_token)

os.environ["MEDICAL_REGISTRY_AUTH_DIR"] = str(auth)
os.environ["MEDICAL_REGISTRY_RUNTIME_DIR"] = str(runtime / "runtime")
os.environ["MEDICAL_KEY_ROOT"] = str(runtime / "keys")
os.environ["MEDICAL_ETHEREUM_PRIVATE_LOCATORS"] = str(
    runtime / "private-locators.json"
)

from fastapi.testclient import TestClient  # noqa: E402
from backend.app import app  # noqa: E402

client = TestClient(app)
hospital = {"Authorization": f"Bearer {hospital_token}"}
researcher = {"Authorization": f"Bearer {researcher_token}"}

suffix = str(int(time.time() * 1000))
dataset_id = f"API-E2E-{suffix}"
request_id = f"API-REQ-{suffix}"
request_id_2 = f"API-REQ2-{suffix}"

csv_bytes = (
    b"patient_code,glucose_mg_dl\n"
    b"A,100\n"
    b"B,110\n"
    b"C,120\n"
    b"D,130\n"
)

health = client.get("/health")
assert health.status_code == 200, health.text
assert health.json()["blockchain"] == "ethereum"
assert health.json()["ethereum"]["connected"] is True

upload = client.post(
    "/datasets/upload",
    headers=hospital,
    data={
        "dataset_id": dataset_id,
        "data_type": "LAB_CSV",
        "metadata_summary": "Synthetic glucose cohort",
        "consent_state": "ACTIVE",
    },
    files={"file": ("glucose.csv", io.BytesIO(csv_bytes), "text/csv")},
)
assert upload.status_code == 200, upload.text
assert upload.json()["storageState"] == "PRIVATE_READY"
assert upload.json()["metadataSummary"].startswith("sha256:")
assert "Synthetic glucose cohort" not in upload.json()["metadataSummary"]

preview = client.get(
    f"/datasets/{dataset_id}/preview",
    headers=hospital,
)
assert preview.status_code == 200, preview.text
assert "glucose_mg_dl" in preview.json()["columns"]

create = client.post(
    "/requests",
    headers=researcher,
    json={
        "request_id": request_id,
        "dataset_id": dataset_id,
        "purpose": "Synthetic glucose analysis",
    },
)
assert create.status_code == 200, create.text
assert create.json()["status"] == "PENDING"
assert create.json()["purpose"].startswith("sha256:")
assert "Synthetic glucose analysis" not in create.json()["purpose"]

approve = client.post(
    f"/requests/{request_id}/approve",
    headers=hospital,
)
assert approve.status_code == 200, approve.text
assert approve.json()["status"] == "APPROVED"

download = client.get(
    f"/requests/{request_id}/download",
    headers=researcher,
)
assert download.status_code == 200, download.text
assert download.content == csv_bytes
assert download.headers["x-ipfs-sha256-verified"] == "true"
assert download.headers["x-storage-encryption"] == "AES-256-GCM"

rotate = client.post(
    f"/datasets/{dataset_id}/rotate-key",
    headers=hospital,
)
assert rotate.status_code == 200, rotate.text
assert rotate.json()["activeKeyVersion"] == 2

he_create = client.post(
    "/he/glucose/encrypt",
    headers=hospital,
    json={
        "dataset_id": dataset_id,
        "request_id": request_id,
        "metric": "glucose_mg_dl",
    },
)
assert he_create.status_code == 200, he_create.text
average_job = he_create.json()["job_id"]
assert he_create.json()["ethereum_status"] == "ENCRYPTED"

he_compute = client.post(
    f"/he/glucose/{average_job}/compute-average",
    headers=researcher,
)
assert he_compute.status_code == 200, he_compute.text
assert he_compute.json()["ethereum_status"] == "COMPUTED"

he_decrypt = client.post(
    f"/he/glucose/{average_job}/decrypt-average",
    headers=hospital,
)
assert he_decrypt.status_code == 200, he_decrypt.text
average_value = float(he_decrypt.json()["average"])
assert abs(average_value - 115.0) < 0.01

ledger = client.get(
    f"/he/glucose/{average_job}/ledger",
    headers=researcher,
)
history = client.get(
    f"/he/glucose/{average_job}/history",
    headers=researcher,
)
assert ledger.status_code == 200, ledger.text
assert ledger.json()["status"] == "DECRYPTED"
assert history.status_code == 200, history.text
assert len(history.json()) >= 3

sum_create = client.post(
    "/he/glucose/encrypt",
    headers=hospital,
    json={
        "dataset_id": dataset_id,
        "request_id": request_id,
        "metric": "glucose_mg_dl",
    },
)
assert sum_create.status_code == 200, sum_create.text
sum_job = sum_create.json()["job_id"]

sum_compute = client.post(
    f"/he/sum/{sum_job}/compute",
    headers=researcher,
)
assert sum_compute.status_code == 200, sum_compute.text
assert sum_compute.json()["ethereum_status"] == "COMPUTED"

sum_decrypt = client.post(
    f"/he/sum/{sum_job}/decrypt",
    headers=hospital,
)
assert sum_decrypt.status_code == 200, sum_decrypt.text
sum_value = float(sum_decrypt.json()["sum"])
assert abs(sum_value - 460.0) < 0.01

revocation_job_create = client.post(
    "/he/glucose/encrypt",
    headers=hospital,
    json={
        "dataset_id": dataset_id,
        "request_id": request_id,
        "metric": "glucose_mg_dl",
    },
)
assert revocation_job_create.status_code == 200, revocation_job_create.text
revocation_job = revocation_job_create.json()["job_id"]

revocation_job_compute = client.post(
    f"/he/glucose/{revocation_job}/compute-average",
    headers=researcher,
)
assert revocation_job_compute.status_code == 200, revocation_job_compute.text

revoke_request = client.post(
    f"/requests/{request_id}/revoke",
    headers=hospital,
)
assert revoke_request.status_code == 200, revoke_request.text

denied_decrypt = client.post(
    f"/he/glucose/{revocation_job}/decrypt-average",
    headers=hospital,
)
assert denied_decrypt.status_code == 403, denied_decrypt.text

denied = client.get(
    f"/requests/{request_id}/download",
    headers=researcher,
)
assert denied.status_code == 403

create2 = client.post(
    "/requests",
    headers=researcher,
    json={
        "request_id": request_id_2,
        "dataset_id": dataset_id,
        "purpose": "Second synthetic analysis",
    },
)
assert create2.status_code == 200, create2.text
approve2 = client.post(
    f"/requests/{request_id_2}/approve",
    headers=hospital,
)
assert approve2.status_code == 200, approve2.text

revoke_dataset = client.post(
    f"/datasets/{dataset_id}/consent",
    headers=hospital,
    json={"consent_state": "REVOKED"},
)
assert revoke_dataset.status_code == 200, revoke_dataset.text

denied2 = client.get(
    f"/requests/{request_id_2}/download",
    headers=researcher,
)
assert denied2.status_code == 403

print("FASTAPI + GANACHE + IPFS + AES + SEAL E2E: PASS")
