from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import hashlib
import io
import json
import os
import subprocess
import uuid
import tempfile
import time
from unittest.mock import patch

from eth_account import Account
from eth_account.messages import encode_defunct

runtime = Path(tempfile.mkdtemp(prefix="medical-api-e2e-"))
auth = runtime / "auth"
auth.mkdir(parents=True)
auth.chmod(0o700)
hospital_token = "1" * 64
researcher_token = "2" * 64
researcher2_token = "5" * 64
researcher_wallet = Account.create()
researcher2_wallet = Account.create()
(auth / "hospital_api.token").write_text(hospital_token)
(auth / "researcher_a.token").write_text(researcher_token)
(auth / "researcher_b.token").write_text(researcher2_token)
(auth / "researchers.json").write_text(
    json.dumps(
        {
            "schemaVersion": 2,
            "researchers": [
                {
                    "id": "researcher-a",
                    "tokenFile": "researcher_a.token",
                    "walletAddress": researcher_wallet.address,
                },
                {
                    "id": "researcher-b",
                    "tokenFile": "researcher_b.token",
                    "walletAddress": researcher2_wallet.address,
                },
            ],
        }
    )
)
for credential_path in auth.iterdir():
    if credential_path.is_file():
        credential_path.chmod(0o600)

plaintext_ram = Path("/dev/shm") / f"medical-api-e2e-{os.getpid()}"
plaintext_ram.mkdir(parents=True, exist_ok=True)

os.environ["MEDICAL_REGISTRY_AUTH_DIR"] = str(auth)
os.environ["MEDICAL_REGISTRY_RUNTIME_DIR"] = str(runtime / "runtime")
os.environ["MEDICAL_KEY_ROOT"] = str(runtime / "keys")
os.environ["MEDICAL_MASTER_KEY_HEX"] = os.urandom(32).hex()
os.environ["MEDICAL_PLAINTEXT_TMPDIR"] = str(plaintext_ram)
os.environ["MEDICAL_RECOVERY_BACKUP_PATH"] = str(
    runtime / "external-backup" / "medical-recovery.medrec"
)
os.environ["MEDICAL_DATA_BACKUP_DIR"] = str(
    runtime / "external-data-backup"
)
os.environ["MEDICAL_ETHEREUM_PRIVATE_LOCATORS"] = str(
    runtime / "private-locators.json"
)

from fastapi.testclient import TestClient  # noqa: E402
from backend.app import app  # noqa: E402
from backend.storage_crypto import load_dataset_key  # noqa: E402
from backend.he_service import RUNTIME_ROOT as HE_RUNTIME_ROOT  # noqa: E402
from backend.dicom_he_service import RUNTIME_ROOT as DICOM_HE_RUNTIME_ROOT  # noqa: E402
from backend.ipfs_storage import has as ipfs_has, ipfs_containers, unpin as ipfs_unpin  # noqa: E402
from backend.ipfs_storage import add_file as ipfs_add_file  # noqa: E402
from backend.ethereum_ledger import invoke, invoke_private_org1  # noqa: E402
from backend.storage_crypto import get_or_create_dataset_key  # noqa: E402
from backend.recovery import restore_recovery_bundle  # noqa: E402

legacy_dataset_id = "ds-" + uuid.uuid4().hex
legacy_name = hashlib.sha256(legacy_dataset_id.encode("utf-8")).hexdigest()
legacy_path = runtime / "keys" / f"{legacy_name}.key"
legacy_path.parent.mkdir(parents=True, exist_ok=True)
legacy_key = os.urandom(32)
legacy_path.write_bytes(legacy_key)
legacy_path.chmod(0o600)
assert load_dataset_key(legacy_dataset_id, 1) == legacy_key
assert legacy_path.read_bytes().startswith(b"MEDKEY01")
assert legacy_path.read_bytes() != legacy_key

for runtime_root in (HE_RUNTIME_ROOT, DICOM_HE_RUNTIME_ROOT):
    runtime_root.resolve().relative_to(plaintext_ram.resolve())
    assert str(runtime_root).startswith(str(plaintext_ram))

client = TestClient(app)
hospital = {"Authorization": f"Bearer {hospital_token}"}
researcher = {"Authorization": f"Bearer {researcher_token}"}
researcher2 = {"Authorization": f"Bearer {researcher2_token}"}


def sign_digest(wallet, digest: str) -> str:
    return Account.sign_message(
        encode_defunct(hexstr=digest),
        wallet.key,
    ).signature.hex()


def create_signed_request(headers, wallet, dataset_id: str, purpose: str):
    prepared = client.post(
        "/requests/prepare",
        headers=headers,
        json={"dataset_id": dataset_id, "purpose": purpose},
    )
    assert prepared.status_code == 200, prepared.text
    body = prepared.json()
    signature = sign_digest(wallet, body["signingDigest"])
    return client.post(
        "/requests",
        headers=headers,
        json={
            "request_id": body["requestId"],
            "dataset_id": dataset_id,
            "purpose": purpose,
            "signature": signature,
        },
    )


def signed_compute(headers, wallet, digest_url: str, compute_url: str):
    prepared = client.get(digest_url, headers=headers)
    assert prepared.status_code == 200, prepared.text
    signature = sign_digest(wallet, prepared.json()["signingDigest"])
    return client.post(
        compute_url,
        headers=headers,
        json={"signature": signature},
    )


suffix = str(int(time.time() * 1000))
dataset_label = f"API-E2E-{suffix}"
request_label = f"API-REQ-{suffix}"
request_label_2 = f"API-REQ2-{suffix}"
dataset_id = dataset_label
request_id = request_label
request_id_2 = request_label_2

csv_bytes = (
    b"patient_code,glucose_mg_dl\n"
    b"A,100\n"
    b"B,110\n"
    b"C,120\n"
    b"D,130\n"
)

health = client.get("/health")
assert health.status_code == 200, health.text

me1 = client.get("/auth/me", headers=researcher)
me2 = client.get("/auth/me", headers=researcher2)
assert me1.status_code == 200 and me2.status_code == 200
assert me1.json()["researcherId"] == "researcher-a"
assert me2.json()["researcherId"] == "researcher-b"
assert me1.json()["ethereumAddress"].lower() != me2.json()["ethereumAddress"].lower()
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
dataset_id = upload.json()["datasetId"]
assert dataset_id.startswith("ds-") and len(dataset_id) == 35
assert dataset_label not in dataset_id
assert upload.json()["metadataSummary"].startswith("sha256:")
assert "Synthetic glucose cohort" not in upload.json()["metadataSummary"]
public_dataset = client.get(f"/datasets/{dataset_id}", headers=hospital)
assert public_dataset.status_code == 200, public_dataset.text
assert public_dataset.json()["dataType"] == "CSV"
assert all(label not in json.dumps(public_dataset.json()) for label in
           ("hiv_status", "BRCA1_mutation", "cancer_stage", "patient_code"))
assert upload.json()["recoveryBackup"]["size"] > 0
assert not list(plaintext_ram.glob("medical-plaintext-*"))

key_files = sorted((runtime / "keys").glob("*.key"))
assert len(key_files) == 2
assert all(path.read_bytes().startswith(b"MEDKEY01") for path in key_files)
assert all(len(path.read_bytes()) > 32 for path in key_files)

preview = client.get(
    f"/datasets/{dataset_id}/preview",
    headers=hospital,
)
assert preview.status_code == 200, preview.text
assert "glucose_mg_dl" in preview.json()["columns"]

prepared_wrong = client.post(
    "/requests/prepare", headers=researcher,
    json={"dataset_id": dataset_id, "purpose": "Synthetic glucose analysis"},
)
assert prepared_wrong.status_code == 200, prepared_wrong.text
wrong_payload = prepared_wrong.json()
wrong_signature = sign_digest(researcher2_wallet, wrong_payload["signingDigest"])
wrong_request = client.post(
    "/requests", headers=researcher,
    json={"request_id": wrong_payload["requestId"], "dataset_id": dataset_id,
          "purpose": "Synthetic glucose analysis", "signature": wrong_signature},
)
assert wrong_request.status_code == 403, wrong_request.text
replayed_signature = sign_digest(researcher_wallet, wrong_payload["signingDigest"])
replayed_request = client.post(
    "/requests", headers=researcher,
    json={"request_id": "req-" + uuid.uuid4().hex, "dataset_id": dataset_id,
          "purpose": "Synthetic glucose analysis", "signature": replayed_signature},
)
assert replayed_request.status_code == 403, replayed_request.text

create = create_signed_request(
    researcher,
    researcher_wallet,
    dataset_id,
    "Synthetic glucose analysis",
)
assert create.status_code == 200, create.text
request_id = create.json()["requestId"]
assert request_id.startswith("req-") and len(request_id) == 36
assert request_label not in request_id
assert create.json()["status"] == "PENDING"
assert create.json()["purpose"].startswith("sha256:")
assert "Synthetic glucose analysis" not in create.json()["purpose"]
assert create.json()["requesterAddress"].lower() == me1.json()["ethereumAddress"].lower()

researcher2_request = create_signed_request(
    researcher2,
    researcher2_wallet,
    dataset_id,
    "Independent second researcher request",
)
assert researcher2_request.status_code == 200, researcher2_request.text
assert researcher2_request.json()["researcherId"] == "researcher-b"
assert (
    researcher2_request.json()["requesterAddress"].lower()
    == me2.json()["ethereumAddress"].lower()
)
assert (
    researcher2_request.json()["requesterAddress"].lower()
    != create.json()["requesterAddress"].lower()
)

approve = client.post(
    f"/requests/{request_id}/approve",
    headers=hospital,
)
assert approve.status_code == 200, approve.text
assert approve.json()["status"] == "APPROVED"

download_blocked = client.get(
    f"/requests/{request_id}/download",
    headers=researcher,
)
assert download_blocked.status_code == 403, download_blocked.text
assert "disabled by default" in download_blocked.json()["detail"]

# Plaintext release is an explicit controlled-demo escape hatch, never default.
os.environ["MEDICAL_ALLOW_PLAINTEXT_DOWNLOADS"] = "true"
wrong_researcher_download = client.get(
    f"/requests/{request_id}/download",
    headers=researcher2,
)
assert wrong_researcher_download.status_code == 403, wrong_researcher_download.text

download = client.get(
    f"/requests/{request_id}/download",
    headers=researcher,
)
assert download.status_code == 200, download.text
assert download.content == csv_bytes
assert download.headers["x-ipfs-sha256-verified"] == "true"
assert download.headers["x-storage-encryption"] == "AES-256-GCM"
del os.environ["MEDICAL_ALLOW_PLAINTEXT_DOWNLOADS"]

rotate = client.post(
    f"/datasets/{dataset_id}/rotate-key",
    headers=hospital,
)
assert rotate.status_code == 200, rotate.text
assert rotate.json()["activeKeyVersion"] == 2
assert rotate.json()["recoveryBackup"]["size"] > 0
key_files = sorted((runtime / "keys").glob("*.key"))
assert len(key_files) == 3
assert all(path.read_bytes().startswith(b"MEDKEY01") for path in key_files)
assert all(len(path.read_bytes()) > 32 for path in key_files)

# Disaster recovery: the bundle is encrypted/authenticated, survives total
# loss of local wrapped-key files + private locators, and restores a working
# dataset only when the bundle is intact and bound to this contract.
recovery_path = Path(os.environ["MEDICAL_RECOVERY_BACKUP_PATH"])
recovery_bundle = recovery_path.read_bytes()
assert recovery_bundle.startswith(b"MEDREC01")
assert csv_bytes not in recovery_bundle

locator_path = Path(os.environ["MEDICAL_ETHEREUM_PRIVATE_LOCATORS"])
encrypted_cid = json.loads(locator_path.read_text())[dataset_id]["cid"]
assert ipfs_has(encrypted_cid)

tampered = bytearray(recovery_bundle)
tampered[-1] ^= 0x01
tampered_restore = client.post(
    "/admin/recovery/restore",
    headers=hospital,
    data={"replace_existing": "true"},
    files={
        "backup": (
            "tampered.medrec",
            io.BytesIO(bytes(tampered)),
            "application/octet-stream",
        )
    },
)
assert tampered_restore.status_code == 400, tampered_restore.text

correct_master = os.environ["MEDICAL_MASTER_KEY_HEX"]
os.environ["MEDICAL_MASTER_KEY_HEX"] = os.urandom(32).hex()
wrong_key_restore = client.post(
    "/admin/recovery/restore", headers=hospital,
    data={"replace_existing": "true"},
    files={"backup": ("wrong-key.medrec", io.BytesIO(recovery_bundle),
                       "application/octet-stream")},
)
os.environ["MEDICAL_MASTER_KEY_HEX"] = correct_master
assert wrong_key_restore.status_code == 400, wrong_key_restore.text

with patch("backend.recovery._ledger_fingerprint",
           return_value={"chainId": 987654, "contractAddress": "0x" + "0" * 40}):
    try:
        restore_recovery_bundle(recovery_bundle, replace_existing=True)
    except ValueError as exc:
        assert "different chain or contract" in str(exc)
    else:
        raise AssertionError("Cross-deployment recovery was accepted")

encrypted_backup_path = Path(os.environ["MEDICAL_DATA_BACKUP_DIR"]) / f"{dataset_id}.medobj"
untampered_object = encrypted_backup_path.read_bytes()
corrupt_object = bytearray(untampered_object)
corrupt_object[-1] ^= 1
encrypted_backup_path.write_bytes(corrupt_object)
corrupt_restore = client.post(
    "/admin/recovery/restore", headers=hospital,
    data={"replace_existing": "true"},
    files={"backup": ("corrupt-object.medrec", io.BytesIO(recovery_bundle),
                       "application/octet-stream")},
)
encrypted_backup_path.write_bytes(untampered_object)
assert corrupt_restore.status_code != 200, corrupt_restore.text
assert client.get(f"/datasets/{dataset_id}/preview", headers=hospital).status_code == 200

for path in (runtime / "keys").iterdir():
    if path.is_file():
        path.unlink()
locator_path.unlink()
ipfs_unpin(encrypted_cid)
for container in ipfs_containers():
    removed = subprocess.run(
        ["docker", "exec", container, "ipfs", "block", "rm", encrypted_cid],
        capture_output=True, text=True,
    )
    assert removed.returncode == 0, removed.stderr
assert not ipfs_has(encrypted_cid), "Encrypted IPFS object survived destructive loss"

broken_preview = client.get(
    f"/datasets/{dataset_id}/preview",
    headers=hospital,
)
assert broken_preview.status_code != 200

restored = client.post(
    "/admin/recovery/restore",
    headers=hospital,
    data={"replace_existing": "true"},
    files={
        "backup": (
            "medical-recovery.medrec",
            io.BytesIO(recovery_bundle),
            "application/octet-stream",
        )
    },
)
assert restored.status_code == 200, restored.text
assert restored.json()["verifiedDatasets"] >= 1
assert restored.json()["restoredKeyFiles"] >= 3
assert restored.json()["restoredIpfsObjects"] == 1
assert ipfs_has(encrypted_cid)

restored_preview = client.get(
    f"/datasets/{dataset_id}/preview",
    headers=hospital,
)
assert restored_preview.status_code == 200, restored_preview.text
assert "glucose_mg_dl" in restored_preview.json()["columns"]

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

wrong_compute = client.post(
    f"/he/glucose/{average_job}/compute-average",
    headers=researcher2,
    json={"signature": "0x" + "00" * 65},
)
assert wrong_compute.status_code == 403, wrong_compute.text

he_compute = signed_compute(
    researcher,
    researcher_wallet,
    f"/he/glucose/{average_job}/signing-digest",
    f"/he/glucose/{average_job}/compute-average",
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
assert ledger.json()["metric"].startswith("CSV:sha256:")
assert "glucose_mg_dl" not in json.dumps(ledger.json())
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

sum_compute = signed_compute(
    researcher,
    researcher_wallet,
    f"/he/sum/{sum_job}/signing-digest",
    f"/he/sum/{sum_job}/compute",
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

revocation_job_compute = signed_compute(
    researcher,
    researcher_wallet,
    f"/he/glucose/{revocation_job}/signing-digest",
    f"/he/glucose/{revocation_job}/compute-average",
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

create2 = create_signed_request(
    researcher,
    researcher_wallet,
    dataset_id,
    "Second synthetic analysis",
)
assert create2.status_code == 200, create2.text
request_id_2 = create2.json()["requestId"]
assert request_id_2.startswith("req-") and len(request_id_2) == 36
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

# A committed locator pointing to raw medical plaintext is rejected even
# when the private CID/SHA commitment itself is valid.
raw_dataset_id = "ds-" + uuid.uuid4().hex
raw_source = runtime / "raw-plaintext-test.csv"
raw_source.write_bytes(csv_bytes)
raw_cid = ipfs_add_file(raw_source)
raw_sha = hashlib.sha256(csv_bytes).hexdigest()
get_or_create_dataset_key(raw_dataset_id)
invoke("RegisterDataset", [raw_dataset_id, "CSV", "sha256:" + "f" * 64,
                           "ACTIVE"], "org1")
invoke_private_org1("StoreDatasetLocatorPrivate", [raw_dataset_id],
                    {"dataset_locator": {"cid": raw_cid, "sha256": raw_sha}})
invoke("FinalizeDatasetRegistration", [raw_dataset_id], "org1")
raw_preview = client.get(f"/datasets/{raw_dataset_id}/preview", headers=hospital)
assert raw_preview.status_code == 422, raw_preview.text
ipfs_unpin(raw_cid)
raw_source.unlink()

print("FASTAPI + ETHEREUM + IPFS + AES + SEAL E2E: PASS")
