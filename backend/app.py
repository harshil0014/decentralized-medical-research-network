from pathlib import Path
import hashlib
import json
import os
import subprocess
import tempfile
import uuid

from fastapi import Depends, FastAPI, HTTPException, Response, UploadFile, File, Form
from pydantic import BaseModel

from backend.api_auth import (
    authenticated_role,
    require_authenticated,
    require_hospital,
    require_researcher,
)

from backend.frontend_ui import router as frontend_router
from backend.runtime_security import (
    SecurityHeadersMiddleware,
    require_mutation_lock,
)

from backend.storage_crypto import (
    dataset_key_exists,
    decrypt_bytes,
    delete_dataset_key,
    encrypt_file,
    is_encrypted_dataset,
    load_dataset_key_metadata,
    rollback_dataset_key_rotation,
    rotate_dataset_key,
    sha256_bytes,
)


app = FastAPI(
    title="Medical Research Network Prototype",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(
    SecurityHeadersMiddleware
)

app.include_router(frontend_router)

REPO = Path(__file__).resolve().parents[1]

class AccessRequestInput(BaseModel):
    request_id: str
    dataset_id: str
    purpose: str


class ConsentUpdateInput(BaseModel):
    consent_state: str


def _public_request_record(raw: str):
    record = json.loads(raw)
    # Keep legacy org labels internal to the compatibility adapter.
    # The public/demo API should describe Ethereum actors directly.
    if record.get("requesterAddress"):
        record["requesterRole"] = "Researcher"
        record.pop("requesterOrg", None)
    if record.get("decidedByAddress"):
        record["decidedByRole"] = "Hospital"
        record.pop("decidedBy", None)
    elif record.get("decidedBy") == "Org1MSP":
        record["decidedByRole"] = "Hospital"
        record.pop("decidedBy", None)
    return record


from backend.ethereum_ledger import (
    health as ethereum_health,
    invoke as ethereum_invoke,
    invoke_private_org1 as ethereum_invoke_private_org1,
    query as ethereum_query,
)


def query(function: str, args: list[str], org: str = "org2") -> str:
    return ethereum_query(function, args, org)


def invoke(function: str, args: list[str], org: str) -> None:
    ethereum_invoke(function, args, org)


def invoke_private_org1(
    function: str,
    args: list[str],
    transient: dict,
) -> None:
    ethereum_invoke_private_org1(function, args, transient)


@app.get("/health")
def health():
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Health.Status}}", "medical-ipfs"],
        text=True,
        capture_output=True,
    )
    ipfs = result.stdout.strip() if result.returncode == 0 else "unavailable"
    chain = ethereum_health()
    return {
        "status": "ok",
        "blockchain": "ethereum",
        "ethereum": chain,
        "ipfs": ipfs,
    }


@app.get("/auth/me")
def auth_me(
    role: str = Depends(
        authenticated_role
    ),
):
    return {
        "authenticated": True,
        "role": role,
        "authMode": "service-token",
    }


@app.post("/datasets/upload", dependencies=[Depends(require_hospital), Depends(require_mutation_lock)])
def upload_dataset(
    dataset_id: str = Form(...),
    data_type: str | None = Form(None),
    metadata_summary: str | None = Form(None),
    consent_state: str = Form("ACTIVE"),
    file: UploadFile = File(...),
):
    temp_path = None
    encrypted_path = None
    container_path = None
    cid = None
    key_existed_before = None

    # Registration is now a three-stage Ethereum transaction:
    #
    # 1. public PRIVATE_PENDING record
    # 2. Org1-only transient CID/SHA -> implicit private collection
    # 3. public PRIVATE_READY finalization
    #
    # Once stage 1 has definitely committed, local encrypted
    # storage must be preserved unless we can definitely cancel
    # the pending registration.
    public_registered = False
    ledger_registered = False
    preserve_assets = False

    try:
        already_exists = (
            query(
                "DatasetExists",
                [dataset_id],
                "org1",
            )
            == "true"
        )

        if already_exists:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Dataset {dataset_id} already exists"
                ),
            )

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

        # Encrypt the final de-identified/validated medical object
        # before it ever enters IPFS.
        encrypted_path = f"{temp_path}.medaes"

        # Record whether this dataset already had a hospital key.
        # On rollback we only delete keys created by THIS upload.
        key_existed_before = dataset_key_exists(
            dataset_id
        )

        digest = encrypt_file(
            dataset_id,
            Path(temp_path),
            Path(encrypted_path),
        )

        container_path = (
            f"/tmp/medical-upload-{uuid.uuid4().hex}.medaes"
        )

        copied = subprocess.run(
            [
                "docker",
                "cp",
                encrypted_path,
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

        # ====================================================
        # STAGE 1
        # Public dataset metadata only.
        #
        # CID and SHA are deliberately NOT arguments here.
        # ====================================================

        try:
            invoke(
                "RegisterDataset",
                [
                    dataset_id,
                    data_type,
                    metadata_summary,
                    consent_state,
                ],
                "org1",
            )

            public_registered = True
            preserve_assets = True

        except HTTPException as register_error:

            # An invoke can fail locally after Ethereum has already
            # committed it. Determine state before deleting the
            # encrypted object or AES key.
            try:
                exists_after_error = (
                    query(
                        "DatasetExists",
                        [dataset_id],
                        "org1",
                    )
                    == "true"
                )

            except HTTPException as confirm_error:
                preserve_assets = True

                raise HTTPException(
                    status_code=503,
                    detail={
                        "message": (
                            "Dataset public registration result "
                            "could not be confirmed"
                        ),
                        "datasetId": dataset_id,
                        "action": (
                            "Preserving encrypted storage and key. "
                            "Reconcile Ethereum state before retrying."
                        ),
                    },
                ) from confirm_error

            if not exists_after_error:
                # Definitely not committed. Normal finally cleanup
                # may safely remove this upload's pin/new key.
                raise register_error

            pending = json.loads(
                query(
                    "DiscoverDataset",
                    [dataset_id],
                    "org1",
                )
            )

            if (
                pending.get("datasetId") != dataset_id
                or pending.get("ownerOrg") != "Org1MSP"
                or pending.get("storageState")
                != "PRIVATE_PENDING"
            ):
                preserve_assets = True

                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": (
                            "Dataset ID exists but does not match "
                            "the expected pending registration"
                        ),
                        "datasetId": dataset_id,
                    },
                ) from register_error

            # Ethereum committed stage 1 despite the local invoke
            # error. Continue from the confirmed pending state.
            public_registered = True
            preserve_assets = True


        # ====================================================
        # STAGE 2
        # CID/SHA go only through transient data to Org1's
        # implicit private collection.
        # ====================================================

        private_locator_present = False

        try:
            invoke_private_org1(
                "StoreDatasetLocatorPrivate",
                [dataset_id],
                {
                    "dataset_locator": {
                        "cid": cid,
                        "sha256": digest,
                    }
                },
            )

            private_locator_present = True

        except HTTPException as private_error:

            try:
                private_locator_present = (
                    query(
                        "DatasetPrivateLocatorExists",
                        [dataset_id],
                        "org1",
                    )
                    == "true"
                )

            except HTTPException as confirm_error:
                preserve_assets = True

                raise HTTPException(
                    status_code=503,
                    detail={
                        "message": (
                            "Private locator write result "
                            "could not be confirmed"
                        ),
                        "datasetId": dataset_id,
                        "storageState": "PRIVATE_PENDING",
                        "action": (
                            "Encrypted storage and AES key were "
                            "preserved for reconciliation."
                        ),
                    },
                ) from confirm_error

            if not private_locator_present:
                # The private write definitely did not commit.
                # Try to remove the public pending shell so the
                # local encrypted upload can be safely rolled back.
                try:
                    invoke(
                        "CancelPendingDatasetRegistration",
                        [dataset_id],
                        "org1",
                    )

                    public_registered = False
                    preserve_assets = False

                except HTTPException as cancel_error:

                    # Cancellation itself may have committed despite
                    # a local error. Confirm whether the public shell
                    # still exists.
                    try:
                        still_exists = (
                            query(
                                "DatasetExists",
                                [dataset_id],
                                "org1",
                            )
                            == "true"
                        )

                    except HTTPException as confirm_cancel_error:
                        preserve_assets = True

                        raise HTTPException(
                            status_code=503,
                            detail={
                                "message": (
                                    "Private locator failed and "
                                    "pending-registration cancellation "
                                    "could not be confirmed"
                                ),
                                "datasetId": dataset_id,
                                "action": (
                                    "Local encrypted storage and key "
                                    "were preserved."
                                ),
                            },
                        ) from confirm_cancel_error

                    if still_exists:
                        preserve_assets = True

                        raise HTTPException(
                            status_code=503,
                            detail={
                                "message": (
                                    "Private locator was not stored, "
                                    "but the public pending registration "
                                    "still exists"
                                ),
                                "datasetId": dataset_id,
                                "action": (
                                    "Local encrypted storage and key "
                                    "were preserved."
                                ),
                            },
                        ) from cancel_error

                    # Cancellation did commit despite the local error.
                    public_registered = False
                    preserve_assets = False

                # At this point we have proven:
                # - no private locator
                # - no public pending record
                #
                # Therefore ordinary cleanup is safe.
                raise private_error


        # ====================================================
        # STAGE 3
        # Public state becomes PRIVATE_READY only after Ethereum
        # can see the private-data hash.
        # ====================================================

        try:
            invoke(
                "FinalizeDatasetRegistration",
                [dataset_id],
                "org1",
            )

        except HTTPException as finalize_error:

            try:
                current = json.loads(
                    query(
                        "DiscoverDataset",
                        [dataset_id],
                        "org1",
                    )
                )

            except HTTPException as confirm_error:
                preserve_assets = True

                raise HTTPException(
                    status_code=503,
                    detail={
                        "message": (
                            "Dataset finalization result "
                            "could not be confirmed"
                        ),
                        "datasetId": dataset_id,
                        "action": (
                            "Private locator, encrypted storage, "
                            "and AES key were preserved."
                        ),
                    },
                ) from confirm_error

            state = current.get(
                "storageState"
            )

            if state != "PRIVATE_READY":
                preserve_assets = True

                raise HTTPException(
                    status_code=503,
                    detail={
                        "message": (
                            "Private locator exists but dataset "
                            "registration is not finalized"
                        ),
                        "datasetId": dataset_id,
                        "storageState": state,
                        "action": (
                            "Retry finalization/reconcile this "
                            "dataset before uploading it again."
                        ),
                    },
                ) from finalize_error

            # Finalization committed despite the local error.

        # From here the public record is confirmed PRIVATE_READY.
        ledger_registered = True
        preserve_assets = True

        raw = query(
            "ReadDatasetPrivate",
            [dataset_id],
            "org1",
        )

        record = json.loads(raw)

        record["uploadedFilename"] = file.filename
        record["storageEncryption"] = "AES-256-GCM"
        return record

    finally:
        # Transactional rollback:
        # if Ethereum registration never succeeded, the upload
        # must not leave durable encrypted storage or a newly
        # created hospital AES key behind.
        if (
            not ledger_registered
            and not preserve_assets
        ):
            if cid:
                subprocess.run(
                    [
                        "docker",
                        "exec",
                        "medical-ipfs",
                        "ipfs",
                        "pin",
                        "rm",
                        cid,
                    ],
                    capture_output=True,
                    text=True,
                )

            if key_existed_before is False:
                delete_dataset_key(
                    dataset_id
                )

        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass

        if encrypted_path:
            try:
                os.unlink(encrypted_path)
            except FileNotFoundError:
                pass

        if container_path:
            subprocess.run(
                ["docker", "exec", "medical-ipfs", "rm", "-f", container_path],
                capture_output=True,
            )


@app.get("/datasets", dependencies=[Depends(require_authenticated)])
def list_datasets():
    raw = query("GetAllDatasets", [], "org2")
    records = json.loads(raw)

    # Researcher-facing discovery metadata only.
    # Do NOT expose IPFS CID or SHA-256 here.
    return [
        {
            "datasetId": r["datasetId"],
            "ownerOrg": r["ownerOrg"],
            "ownerAddress": r.get("ownerAddress", ""),
            "dataType": r["dataType"],
            "metadataSummary": r["metadataSummary"],
            "consentState": r["consentState"],
            "version": r["version"],
            "createdAt": r["createdAt"],
            "updatedAt": r["updatedAt"],
        }
        for r in records
        if (
            r.get("consentState") == "ACTIVE"
            and r.get(
                "storageState",
                "LEGACY_PUBLIC",
            )
            in {
                "LEGACY_PUBLIC",
                "PRIVATE_READY",
            }
        )
    ]


@app.get("/datasets/{dataset_id}/history", dependencies=[Depends(require_authenticated)])
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


@app.get("/datasets/{dataset_id}", dependencies=[Depends(require_authenticated)])
def get_dataset(dataset_id: str):
    raw = query(
        "DiscoverDataset",
        [dataset_id],
        "org2",
    )
    return json.loads(raw)


@app.post("/datasets/{dataset_id}/consent", dependencies=[Depends(require_hospital), Depends(require_mutation_lock)])
def update_dataset_consent(dataset_id: str, body: ConsentUpdateInput):
    invoke("UpdateConsent", [dataset_id, body.consent_state], "org1")

    raw = query("ReadDatasetPrivate", [dataset_id], "org1")
    return json.loads(raw)


@app.post("/datasets/{dataset_id}/rotate-key", dependencies=[Depends(require_hospital), Depends(require_mutation_lock)])
def rotate_dataset_encryption_key(
    dataset_id: str,
):
    dataset = json.loads(
        query(
            "ReadDatasetPrivate",
            [dataset_id],
            "org1",
        )
    )

    if dataset.get(
        "ownerOrg"
    ) != "Org1MSP":
        raise HTTPException(
            status_code=403,
            detail=(
                "Dataset is not owned by "
                "the hospital organisation"
            ),
        )

    before = load_dataset_key_metadata(
        dataset_id
    )

    previous_version = int(
        before[
            "activeKeyVersion"
        ]
    )

    local_rotation = rotate_dataset_key(
        dataset_id
    )

    new_version = int(
        local_rotation[
            "activeKeyVersion"
        ]
    )

    try:
        invoke(
            "RecordKeyRotation",
            [
                dataset_id,
                str(
                    previous_version
                ),
                str(
                    new_version
                ),
            ],
            "org1",
        )

    except HTTPException as invoke_error:

        try:
            committed = (
                query(
                    "KeyRotationExists",
                    [
                        dataset_id,
                        str(
                            new_version
                        ),
                    ],
                    "org1",
                )
                == "true"
            )

        except HTTPException as confirm_error:
            raise HTTPException(
                status_code=503,
                detail={
                    "message": (
                        "Local key rotation completed, "
                        "but Ethereum audit state could "
                        "not be confirmed"
                    ),
                    "datasetId": dataset_id,
                    "previousKeyVersion": previous_version,
                    "activeKeyVersion": new_version,
                    "action": (
                        "Do not rotate again until "
                        "Ethereum state is reconciled"
                    ),
                },
            ) from confirm_error

        if not committed:
            rollback_dataset_key_rotation(
                dataset_id,
                previous_version,
                new_version,
            )

            raise invoke_error

    try:
        audit = json.loads(
            query(
                "ReadKeyRotation",
                [
                    dataset_id,
                    str(
                        new_version
                    ),
                ],
                "org1",
            )
        )

    except HTTPException as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "message": (
                    "Key rotation committed, "
                    "but audit record could not "
                    "be read back"
                ),
                "datasetId": dataset_id,
                "activeKeyVersion": new_version,
            },
        ) from exc

    after = load_dataset_key_metadata(
        dataset_id
    )

    return {
        "datasetId": dataset_id,
        "previousKeyVersion": previous_version,
        "activeKeyVersion": after[
            "activeKeyVersion"
        ],
        "ethereumAudit": audit,
    }


@app.post("/requests", dependencies=[Depends(require_researcher), Depends(require_mutation_lock)])
def create_request(body: AccessRequestInput):
    invoke(
        "RequestAccess",
        [body.request_id, body.dataset_id, body.purpose],
        "org2",
    )

    raw = query("ReadAccessRequest", [body.request_id], "org2")
    return _public_request_record(raw)


@app.get("/requests/{request_id}", dependencies=[Depends(require_authenticated)])
def get_request(request_id: str):
    raw = query("ReadAccessRequest", [request_id], "org2")
    return _public_request_record(raw)


@app.post("/requests/{request_id}/approve", dependencies=[Depends(require_hospital), Depends(require_mutation_lock)])
def approve_request(request_id: str):
    invoke("DecideAccess", [request_id, "APPROVED"], "org1")

    raw = query("ReadAccessRequest", [request_id], "org1")
    return _public_request_record(raw)


@app.post("/requests/{request_id}/revoke", dependencies=[Depends(require_hospital), Depends(require_mutation_lock)])
def revoke_request(request_id: str):
    invoke("DecideAccess", [request_id, "REVOKED"], "org1")

    raw = query("ReadAccessRequest", [request_id], "org1")
    return json.loads(raw)


@app.get("/requests/{request_id}/download", dependencies=[Depends(require_researcher)])
def download_dataset(request_id: str):
    allowed = query(
        "CanAccess",
        [request_id],
        "org2",
    )

    if allowed != "true":
        raise HTTPException(
            status_code=403,
            detail="Access denied by Ethereum smart contract",
        )

    request_data = json.loads(
        query(
            "ReadAccessRequest",
            [request_id],
            "org2",
        )
    )

    dataset_data = json.loads(
        query(
            "ReadDatasetPrivate",
            [request_data["datasetId"]],
            "org1",
        )
    )

    cid = dataset_data["cid"]

    result = subprocess.run(
        [
            "docker",
            "exec",
            "medical-ipfs",
            "ipfs",
            "cat",
            cid,
        ],
        capture_output=True,
    )

    if result.returncode != 0:
        raise HTTPException(
            status_code=502,
            detail="IPFS retrieval failed",
        )

    stored_bytes = result.stdout

    # Ethereum ledger stores the SHA-256 of the exact object placed in IPFS.
    actual_sha256 = sha256_bytes(
        stored_bytes
    )

    expected_sha256 = (
        dataset_data["sha256"].lower()
    )

    if actual_sha256.lower() != expected_sha256:
        raise HTTPException(
            status_code=409,
            detail=(
                "IPFS dataset integrity verification failed"
            ),
        )

    if is_encrypted_dataset(stored_bytes):
        try:
            content = decrypt_bytes(
                dataset_data["datasetId"],
                stored_bytes,
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    "Hospital dataset encryption key is unavailable"
                ),
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=409,
                detail=(
                    "AES-GCM dataset authentication failed"
                ),
            ) from exc

        storage_encryption = "AES-256-GCM"

    else:
        # Backward compatibility only for datasets registered
        # before encrypted-at-rest storage was introduced.
        content = stored_bytes
        storage_encryption = "LEGACY-PLAINTEXT"

    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "X-Dataset-ID": dataset_data["datasetId"],
            "X-IPFS-CID": cid,
            "X-IPFS-SHA256-Verified": "true",
            "X-Storage-Encryption": storage_encryption,
        },
    )


# Microsoft SEAL homomorphic-encryption API
from backend.he_api import router as he_router
app.include_router(he_router)
