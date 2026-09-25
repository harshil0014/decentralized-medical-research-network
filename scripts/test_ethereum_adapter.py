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
import uuid
from concurrent.futures import ThreadPoolExecutor

from eth_account import Account
from eth_account.messages import encode_defunct

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
dataset_id = "ds-" + uuid.uuid4().hex
request_id = "req-" + uuid.uuid4().hex
job_id = f"PY-JOB-{suffix}"
researcher = Account.create()
researcher_org = f"researcher-address:{researcher.address}"

state = health()
assert state["connected"] is True
assert state["network"] in {"Ganache", "Besu-QBFT-4"}

# Independent relayed transactions may arrive from separate API workers at
# the same time. Every registration must receive a unique confirmed nonce.
concurrent_ids = ["ds-" + uuid.uuid4().hex for _ in range(4)]
with ThreadPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(
        lambda candidate: invoke(
            "RegisterDataset",
            [candidate, "CSV", "sha256:" + "e" * 64, "ACTIVE"],
            "org1",
        ),
        concurrent_ids,
    ))
assert results == [None] * len(concurrent_ids)
assert all(query("DatasetExists", [candidate], "org1") == "true"
           for candidate in concurrent_ids)

invoke(
    "RegisterDataset",
    [dataset_id, "CSV", "sha256:" + "a" * 64, "ACTIVE"],
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

purpose_commitment = "sha256:" + "b" * 64
request_digest = query(
    "ResearcherRequestDigest",
    [request_id, dataset_id, purpose_commitment],
    researcher_org,
)
request_signature = Account.sign_message(
    encode_defunct(hexstr=request_digest),
    researcher.key,
).signature.hex()
invoke(
    "RequestAccessSigned",
    [request_id, dataset_id, purpose_commitment, researcher.address, request_signature],
    "org1",
)
invoke("DecideAccess", [request_id, "APPROVED"], "org1")
assert query("CanAccess", [request_id], researcher_org) == "true"

invoke(
    "RegisterHEJob",
    [
        job_id,
        dataset_id,
        request_id,
        "",
        "",
        "CSV:sha256:" + "d" * 64,
        "4",
        "bafy-python-cipher",
        "b" * 64,
    ],
    "org1",
)
compute_digest = query("HEComputeDigest", [job_id], researcher_org)
compute_signature = Account.sign_message(
    encode_defunct(hexstr=compute_digest),
    researcher.key,
).signature.hex()
invoke(
    "RecordHEComputationSigned",
    [job_id, "bafy-python-result", "c" * 64, compute_signature],
    "org1",
)
invoke("RecordHEDecryption", [job_id], "org1")

job = json.loads(query("ReadHEJob", [job_id], "org1"))
assert job["status"] == "DECRYPTED"
assert len(json.loads(query("GetHEJobHistory", [job_id], researcher_org))) >= 3

invoke("DecideAccess", [request_id, "REVOKED"], "org1")
assert query("CanAccess", [request_id], researcher_org) == "false"

print("PYTHON ETHEREUM ADAPTER E2E: PASS")
