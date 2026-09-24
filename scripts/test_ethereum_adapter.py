from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import json
import os
import tempfile
import time

root = tempfile.mkdtemp(prefix="medical-eth-adapter-")
os.environ["MEDICAL_REGISTRY_AUTH_DIR"] = root
os.environ["MEDICAL_ETHEREUM_PRIVATE_LOCATORS"] = os.path.join(
    root,
    "private-locators.json",
)

from backend.ethereum_ledger import (  # noqa: E402
    health,
    invoke,
    invoke_private_org1,
    query,
)

suffix = str(int(time.time() * 1000))
dataset_id = f"PY-E2E-{suffix}"
request_id = f"PY-REQ-{suffix}"
job_id = f"PY-JOB-{suffix}"

state = health()
assert state["connected"] is True
assert state["network"] == "Ganache"

invoke(
    "RegisterDataset",
    [dataset_id, "LAB_CSV", "Synthetic adapter test", "ACTIVE"],
    "org1",
)
invoke_private_org1(
    "StoreDatasetLocatorPrivate",
    [dataset_id],
    {
        "dataset_locator": {
            "cid": "bafy-python-adapter",
            "sha256": "a" * 64,
        }
    },
)
invoke("FinalizeDatasetRegistration", [dataset_id], "org1")

private_ds = json.loads(
    query("ReadDatasetPrivate", [dataset_id], "org1")
)
assert private_ds["cid"] == "bafy-python-adapter"
assert private_ds["storageState"] == "PRIVATE_READY"

invoke(
    "RequestAccess",
    [request_id, dataset_id, "Adapter glucose analysis"],
    "org2",
)
invoke("DecideAccess", [request_id, "APPROVED"], "org1")
assert query("CanAccess", [request_id], "org2") == "true"

invoke(
    "RegisterHEJob",
    [
        job_id,
        dataset_id,
        request_id,
        "",
        "",
        "glucose_mg_dl",
        "4",
        "bafy-python-cipher",
        "b" * 64,
    ],
    "org1",
)
invoke(
    "RecordHEComputation",
    [job_id, "bafy-python-result", "c" * 64],
    "org2",
)
invoke("RecordHEDecryption", [job_id], "org1")

job = json.loads(query("ReadHEJob", [job_id], "org1"))
assert job["status"] == "DECRYPTED"
assert len(json.loads(query("GetHEJobHistory", [job_id], "org2"))) >= 3

invoke("DecideAccess", [request_id, "REVOKED"], "org1")
assert query("CanAccess", [request_id], "org2") == "false"

print("PYTHON ETHEREUM ADAPTER E2E: PASS")
