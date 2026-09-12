from pathlib import Path
import hashlib
import json
import os
import subprocess
import tempfile
import uuid

from fastapi import FastAPI, HTTPException, Response, UploadFile, File, Form
from pydantic import BaseModel


app = FastAPI(
    title="Medical Research Network Prototype",
    version="0.1.0",
)

REPO = Path(__file__).resolve().parents[1]
FABRIC_SAMPLES = REPO.parent
TEST_NETWORK = FABRIC_SAMPLES / "test-network"

PEER_BIN = FABRIC_SAMPLES / "bin" / "peer"
FABRIC_CONFIG = FABRIC_SAMPLES / "config"

ORDERER_CA = (
    TEST_NETWORK
    / "organizations/ordererOrganizations/example.com/orderers/"
      "orderer.example.com/msp/tlscacerts/tlsca.example.com-cert.pem"
)

ORG1_TLS = (
    TEST_NETWORK
    / "organizations/peerOrganizations/org1.example.com/"
      "peers/peer0.org1.example.com/tls/ca.crt"
)

ORG2_TLS = (
    TEST_NETWORK
    / "organizations/peerOrganizations/org2.example.com/"
      "peers/peer0.org2.example.com/tls/ca.crt"
)

ORG1_MSP = (
    TEST_NETWORK
    / "organizations/peerOrganizations/org1.example.com/"
      "users/Admin@org1.example.com/msp"
)

ORG2_MSP = (
    TEST_NETWORK
    / "organizations/peerOrganizations/org2.example.com/"
      "users/Admin@org2.example.com/msp"
)


class AccessRequestInput(BaseModel):
    request_id: str
    dataset_id: str
    purpose: str


class ConsentUpdateInput(BaseModel):
    consent_state: str


def fabric_env(org: str) -> dict:
    env = os.environ.copy()
    env["PATH"] = f"{FABRIC_SAMPLES / 'bin'}:{env.get('PATH', '')}"
    env["FABRIC_CFG_PATH"] = str(FABRIC_CONFIG)
    env["CORE_PEER_TLS_ENABLED"] = "true"

    if org == "org1":
        env["CORE_PEER_LOCALMSPID"] = "Org1MSP"
        env["CORE_PEER_TLS_ROOTCERT_FILE"] = str(ORG1_TLS)
        env["CORE_PEER_MSPCONFIGPATH"] = str(ORG1_MSP)
        env["CORE_PEER_ADDRESS"] = "localhost:7051"
    elif org == "org2":
        env["CORE_PEER_LOCALMSPID"] = "Org2MSP"
        env["CORE_PEER_TLS_ROOTCERT_FILE"] = str(ORG2_TLS)
        env["CORE_PEER_MSPCONFIGPATH"] = str(ORG2_MSP)
        env["CORE_PEER_ADDRESS"] = "localhost:9051"
    else:
        raise ValueError("Unknown organisation")

    return env


def run(cmd: list[str], org: str) -> str:
    result = subprocess.run(
        cmd,
        cwd=TEST_NETWORK,
        env=fabric_env(org),
        text=True,
        capture_output=True,
    )

    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise HTTPException(status_code=500, detail=message)

    return result.stdout.strip()


def query(function: str, args: list[str], org: str = "org2") -> str:
    payload = json.dumps({"function": function, "Args": args})

    return run(
        [
            str(PEER_BIN),
            "chaincode",
            "query",
            "-C",
            "mychannel",
            "-n",
            "medicalregistry",
            "-c",
            payload,
        ],
        org,
    )


def invoke(function: str, args: list[str], org: str) -> None:
    payload = json.dumps({"function": function, "Args": args})

    run(
        [
            str(PEER_BIN),
            "chaincode",
            "invoke",
            "-o",
            "localhost:7050",
            "--ordererTLSHostnameOverride",
            "orderer.example.com",
            "--tls",
            "--cafile",
            str(ORDERER_CA),
            "-C",
            "mychannel",
            "-n",
            "medicalregistry",
            "--peerAddresses",
            "localhost:7051",
            "--tlsRootCertFiles",
            str(ORG1_TLS),
            "--peerAddresses",
            "localhost:9051",
            "--tlsRootCertFiles",
            str(ORG2_TLS),
            "-c",
            payload,
            "--waitForEvent",
            "--waitForEventTimeout",
            "30s",
        ],
        org,
    )


@app.get("/health")
def health():
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Health.Status}}", "medical-ipfs"],
        text=True,
        capture_output=True,
    )

    ipfs = result.stdout.strip() if result.returncode == 0 else "unavailable"

    return {
        "status": "ok",
        "fabric_channel": "mychannel",
        "chaincode": "medicalregistry",
        "ipfs": ipfs,
    }


@app.post("/datasets/upload")
def upload_dataset(
    dataset_id: str = Form(...),
    data_type: str | None = Form(None),
    metadata_summary: str | None = Form(None),
    consent_state: str = Form("ACTIVE"),
    file: UploadFile = File(...),
):
    temp_path = None
    container_path = None

    try:
        suffix = Path(file.filename or "upload.bin").suffix

        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix,
        ) as tmp:
            temp_path = tmp.name
            sha256 = hashlib.sha256()

            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break

                sha256.update(chunk)
                tmp.write(chunk)

        digest = sha256.hexdigest()

        # DICOM series ZIPs are de-identified BEFORE hashing and BEFORE IPFS storage.
        if suffix.lower() == ".zip":
            try:
                from backend.dicom_series import deidentify_dicom_series_zip
                from backend.dicom_utils import sha256_file

                safe_metadata = deidentify_dicom_series_zip(temp_path)
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid or unsupported DICOM series ZIP",
                ) from exc

            digest = sha256_file(temp_path)

            parts = ["De-identified DICOM series"]
            for key in (
                "modality",
                "study_description",
                "series_description",
                "rows",
                "columns",
                "slice_count",
            ):
                value = safe_metadata.get(key)
                if value not in (None, ""):
                    parts.append(f"{key}={value}")

            metadata_summary = "; ".join(parts)

            modality = safe_metadata.get("modality")
            data_type = (
                f"DICOM_SERIES_{modality}"
                if modality
                else "DICOM_SERIES"
            )

        # DICOM files are de-identified BEFORE hashing and BEFORE IPFS storage.
        elif suffix.lower() in {".dcm", ".dicom"}:
            from backend.dicom_utils import deidentify_dicom_in_place, sha256_file

            try:
                safe_metadata = deidentify_dicom_in_place(temp_path)
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid or unsupported DICOM file",
                ) from exc

            digest = sha256_file(temp_path)

            parts = ["De-identified DICOM"]
            for key in (
                "modality",
                "study_description",
                "series_description",
                "rows",
                "columns",
            ):
                value = safe_metadata.get(key)
                if value not in (None, ""):
                    parts.append(f"{key}={value}")

            metadata_summary = "; ".join(parts)

            modality = safe_metadata.get("modality")
            data_type = f"DICOM_{modality}" if modality else "DICOM"

        if not data_type:
            raise HTTPException(
                status_code=400,
                detail="data_type is required for non-DICOM uploads",
            )

        if not metadata_summary:
            raise HTTPException(
                status_code=400,
                detail="metadata_summary is required for non-DICOM uploads",
            )

        container_path = f"/tmp/medical-upload-{uuid.uuid4().hex}{suffix}"

        copied = subprocess.run(
            [
                "docker",
                "cp",
                temp_path,
                f"medical-ipfs:{container_path}",
            ],
            text=True,
            capture_output=True,
        )

        if copied.returncode != 0:
            raise HTTPException(
                status_code=502,
                detail=copied.stderr.strip() or "Failed to copy file into IPFS node",
            )

        added = subprocess.run(
            [
                "docker",
                "exec",
                "medical-ipfs",
                "ipfs",
                "add",
                "-Q",
                container_path,
            ],
            text=True,
            capture_output=True,
        )

        if added.returncode != 0:
            raise HTTPException(
                status_code=502,
                detail=added.stderr.strip() or "IPFS add failed",
            )

        cid = added.stdout.strip()

        invoke(
            "RegisterDataset",
            [
                dataset_id,
                cid,
                digest,
                data_type,
                metadata_summary,
                consent_state,
            ],
            "org1",
        )

        raw = query("ReadDataset", [dataset_id], "org1")
        record = json.loads(raw)

        record["uploadedFilename"] = file.filename
        return record

    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass

        if container_path:
            subprocess.run(
                ["docker", "exec", "medical-ipfs", "rm", "-f", container_path],
                capture_output=True,
            )


@app.get("/datasets")
def list_datasets():
    raw = query("GetAllDatasets", [], "org2")
    records = json.loads(raw)

    # Researcher-facing discovery metadata only.
    # Do NOT expose IPFS CID or SHA-256 here.
    return [
        {
            "datasetId": r["datasetId"],
            "ownerOrg": r["ownerOrg"],
            "dataType": r["dataType"],
            "metadataSummary": r["metadataSummary"],
            "consentState": r["consentState"],
            "version": r["version"],
            "createdAt": r["createdAt"],
            "updatedAt": r["updatedAt"],
        }
        for r in records
        if r.get("consentState") == "ACTIVE"
    ]


@app.get("/datasets/{dataset_id}/history")
def get_dataset_history(dataset_id: str):
    raw = query("GetDatasetHistory", [dataset_id], "org2")
    history = json.loads(raw)

    safe_history = []

    for row in history:
        safe = {
            "txId": row.get("txId"),
            "timestamp": row.get("timestamp"),
            "isDelete": row.get("isDelete", False),
        }

        value = row.get("value")
        if isinstance(value, dict):
            safe["datasetId"] = value.get("datasetId")
            safe["ownerOrg"] = value.get("ownerOrg")
            safe["consentState"] = value.get("consentState")
            safe["version"] = value.get("version")
            safe["updatedAt"] = value.get("updatedAt")

        safe_history.append(safe)

    return safe_history


@app.get("/datasets/{dataset_id}")
def get_dataset(dataset_id: str):
    raw = query("ReadDataset", [dataset_id])
    return json.loads(raw)


@app.post("/datasets/{dataset_id}/consent")
def update_dataset_consent(dataset_id: str, body: ConsentUpdateInput):
    invoke("UpdateConsent", [dataset_id, body.consent_state], "org1")

    raw = query("ReadDataset", [dataset_id], "org1")
    return json.loads(raw)


@app.post("/requests")
def create_request(body: AccessRequestInput):
    invoke(
        "RequestAccess",
        [body.request_id, body.dataset_id, body.purpose],
        "org2",
    )

    raw = query("ReadAccessRequest", [body.request_id], "org2")
    return json.loads(raw)


@app.get("/requests/{request_id}")
def get_request(request_id: str):
    raw = query("ReadAccessRequest", [request_id], "org2")
    return json.loads(raw)


@app.post("/requests/{request_id}/approve")
def approve_request(request_id: str):
    invoke("DecideAccess", [request_id, "APPROVED"], "org1")

    raw = query("ReadAccessRequest", [request_id], "org1")
    return json.loads(raw)


@app.post("/requests/{request_id}/revoke")
def revoke_request(request_id: str):
    invoke("DecideAccess", [request_id, "REVOKED"], "org1")

    raw = query("ReadAccessRequest", [request_id], "org1")
    return json.loads(raw)


@app.get("/requests/{request_id}/download")
def download_dataset(request_id: str):
    allowed = query("CanAccess", [request_id], "org2")

    if allowed != "true":
        raise HTTPException(
            status_code=403,
            detail="Access denied by Hyperledger Fabric",
        )

    request_data = json.loads(
        query("ReadAccessRequest", [request_id], "org2")
    )

    dataset_data = json.loads(
        query("ReadDataset", [request_data["datasetId"]], "org2")
    )

    cid = dataset_data["cid"]

    result = subprocess.run(
        ["docker", "exec", "medical-ipfs", "ipfs", "cat", cid],
        capture_output=True,
    )

    if result.returncode != 0:
        raise HTTPException(
            status_code=502,
            detail="IPFS retrieval failed",
        )

    return Response(
        content=result.stdout,
        media_type="application/octet-stream",
        headers={
            "X-Dataset-ID": dataset_data["datasetId"],
            "X-IPFS-CID": cid,
        },
    )


# Microsoft SEAL homomorphic-encryption API
from backend.he_api import router as he_router
app.include_router(he_router)
