from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import tempfile
import uuid

from fastapi import Depends, FastAPI, HTTPException, Response, UploadFile, File, Form
from pydantic import BaseModel

from backend.api_auth import (
    AuthIdentity,
    authenticated_identity,
    researcher_org,
    require_authenticated,
    require_hospital,
    require_researcher,
)

from backend.frontend_ui import router as frontend_router
from backend.runtime_security import (
    SecurityHeadersMiddleware,
    require_mutation_lock,
)

from backend.recovery import (
    export_recovery_bundle,
    recovery_backup_path,
    restore_recovery_bundle,
    snapshot_recovery_bundle,
)

from backend.secure_temp import (
    create_secure_plaintext_temp,
    secure_plaintext_temp_root,
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

# FastAPI/Starlette may spool multipart uploads before endpoint code runs.
# Force Python's process-wide tempfile directory onto verified RAM-backed
# storage so large medical uploads never spill onto the ordinary disk.
_SECURE_PLAINTEXT_TMP = secure_plaintext_temp_root()
for _temp_env in ("TMPDIR", "TMP", "TEMP"):
    os.environ[_temp_env] = str(_SECURE_PLAINTEXT_TMP)
tempfile.tempdir = str(_SECURE_PLAINTEXT_TMP)

OPAQUE_DATASET_ID_PATTERN = re.compile(r"^ds-[0-9a-f]{32}$")
OPAQUE_REQUEST_ID_PATTERN = re.compile(r"^req-[0-9a-f]{32}$")
DATA_TYPE_PATTERN = re.compile(r"^[A-Z0-9_:-]{1,64}$")


def _plaintext_downloads_enabled() -> bool:
    return os.getenv(
        "MEDICAL_ALLOW_PLAINTEXT_DOWNLOADS",
        "",
    ).strip().lower() in {"1", "true", "yes", "on"}


def _opaque_dataset_id() -> str:
    return "ds-" + uuid.uuid4().hex


def _opaque_request_id() -> str:
    return "req-" + uuid.uuid4().hex


def _validate_opaque_dataset_id(value: str) -> str:
    clean = (value or "").strip()
    if not OPAQUE_DATASET_ID_PATTERN.fullmatch(clean):
        raise HTTPException(
            status_code=400,
            detail="dataset_id must be an opaque server-issued ds- identifier",
        )
    return clean


def _public_text_commitment(value: str, label: str) -> str:
    clean = (value or "").strip()
    if not clean:
        raise HTTPException(status_code=400, detail=f"{label} is required")
    if len(clean) > 4096:
        raise HTTPException(status_code=400, detail=f"{label} is too long")
    return "sha256:" + hashlib.sha256(clean.encode("utf-8")).hexdigest()


class AccessRequestInput(BaseModel):
    request_id: str | None = None
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
    account_address as ethereum_account_address,
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
    identity: AuthIdentity = Depends(authenticated_identity),
):
    result = {
        "authenticated": True,
        "role": identity.role,
        "authMode": "service-token",
        "plaintextDownloadsEnabled": _plaintext_downloads_enabled(),
    }

    if identity.role == "researcher":
        org = researcher_org(identity)
        result["researcherId"] = identity.researcher_id
        result["ethereumAddress"] = ethereum_account_address(org)

    return result


@app.post("/datasets/upload", dependencies=[Depends(require_hospital), Depends(require_mutation_lock)])
def upload_dataset(
    dataset_id: str = Form(...),
    data_type: str | None = Form(None),
    metadata_summary: str | None = Form(None),
    consent_state: str = Form("ACTIVE"),
    visual_phi_reviewed: bool = Form(False),
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

    client_dataset_label = (dataset_id or "").strip()
    dataset_id = _opaque_dataset_id()

    # Fail before any durable mutation if disaster recovery is not configured.
    recovery_backup_path()

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

        # Medical plaintext never stages on the ordinary filesystem.
        # This path is verified to be RAM-backed (tmpfs/ramfs).
        temp_path = create_secure_plaintext_temp(
            suffix=suffix,
        )
        sha256 = hashlib.sha256()

        with open(temp_path, "wb") as tmp:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break

                sha256.update(chunk)
                tmp.write(chunk)

            tmp.flush()
            os.fsync(tmp.fileno())

        digest = sha256.hexdigest()

        # The caller-supplied label is intentionally never written to Ethereum.
        # Ethereum receives only the opaque server-generated dataset ID.
        if len(client_dataset_label) > 256:
            raise HTTPException(
                status_code=400,
                detail="dataset label is too long",
            )

        # DICOM series ZIPs are de-identified BEFORE hashing and BEFORE IPFS storage.
        if suffix.lower() == ".zip":
            try:
                from backend.dicom_series import deidentify_dicom_series_zip
                from backend.dicom_utils import sha256_file

                safe_metadata = deidentify_dicom_series_zip(
                    temp_path,
                    visual_phi_reviewed=visual_phi_reviewed,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid or unsupported DICOM series ZIP",
                ) from exc

            digest = sha256_file(temp_path)

            parts = ["De-identified DICOM series"]
            for key in (
                "modality",
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
                safe_metadata = deidentify_dicom_in_place(
                    temp_path,
                    visual_phi_reviewed=visual_phi_reviewed,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid or unsupported DICOM file",
                ) from exc

            digest = sha256_file(temp_path)

            parts = ["De-identified DICOM"]
            for key in (
                "modality",
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

        data_type = data_type.strip().upper()
        if not DATA_TYPE_PATTERN.fullmatch(data_type):
            raise HTTPException(
                status_code=400,
                detail="data_type must use only A-Z, 0-9, _, : or -",
            )

        if not metadata_summary:
            raise HTTPException(
                status_code=400,
                detail="metadata_summary is required for non-DICOM uploads",
            )

        ledger_metadata_summary = _public_text_commitment(
            metadata_summary,
            "metadata_summary",
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
                    ledger_metadata_summary,
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

        try:
            record["recoveryBackup"] = snapshot_recovery_bundle()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "message": "Dataset committed, but recovery snapshot failed",
                    "datasetId": dataset_id,
                    "action": "Repair the recovery destination and create a snapshot before further mutations",
                },
            ) from exc

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
    # Fail before generating a new key generation if recovery is not configured.
    recovery_backup_path()

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

    try:
        recovery = snapshot_recovery_bundle()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Key rotation committed, but recovery snapshot failed",
                "datasetId": dataset_id,
                "activeKeyVersion": new_version,
                "action": "Repair the recovery destination and create a snapshot before further rotations",
            },
        ) from exc

    return {
        "datasetId": dataset_id,
        "previousKeyVersion": previous_version,
        "activeKeyVersion": after[
            "activeKeyVersion"
        ],
        "ethereumAudit": audit,
        "recoveryBackup": recovery,
    }


@app.post("/admin/recovery/snapshot", dependencies=[Depends(require_hospital), Depends(require_mutation_lock)])
def create_recovery_snapshot():
    try:
        return snapshot_recovery_bundle()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Recovery snapshot failed: {exc}",
        ) from exc


@app.get("/admin/recovery/export", dependencies=[Depends(require_hospital)])
def export_recovery_snapshot():
    try:
        bundle = export_recovery_bundle()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Recovery export failed: {exc}",
        ) from exc

    return Response(
        content=bundle,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": 'attachment; filename="medical-recovery.medrec"',
            "X-Recovery-Sha256": hashlib.sha256(bundle).hexdigest(),
        },
    )


@app.post("/admin/recovery/restore", dependencies=[Depends(require_hospital), Depends(require_mutation_lock)])
def restore_recovery_snapshot(
    backup: UploadFile = File(...),
    replace_existing: bool = Form(False),
):
    try:
        bundle = backup.file.read(64 * 1024 * 1024 + 1)
        if len(bundle) > 64 * 1024 * 1024:
            raise HTTPException(
                status_code=413,
                detail="Recovery bundle exceeds 64 MiB",
            )

        result = restore_recovery_bundle(
            bundle,
            replace_existing=replace_existing,
        )
        result["recoveryBackup"] = snapshot_recovery_bundle()
        return result

    except HTTPException:
        raise
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Recovery restore failed: {exc}",
        ) from exc


@app.post("/requests", dependencies=[Depends(require_mutation_lock)])
def create_request(
    body: AccessRequestInput,
    identity: AuthIdentity = Depends(require_researcher),
):
    request_id = _opaque_request_id()
    dataset_id = _validate_opaque_dataset_id(body.dataset_id)
    purpose_commitment = _public_text_commitment(body.purpose, "purpose")
    actor_org = researcher_org(identity)

    invoke(
        "RequestAccess",
        [request_id, dataset_id, purpose_commitment],
        actor_org,
    )

    raw = query("ReadAccessRequest", [request_id], actor_org)
    record = _public_request_record(raw)
    record["researcherId"] = identity.researcher_id
    return record


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


@app.get("/requests/{request_id}/download")
def download_dataset(
    request_id: str,
    identity: AuthIdentity = Depends(require_researcher),
):
    if not _plaintext_downloads_enabled():
        raise HTTPException(
            status_code=403,
            detail=(
                "Plaintext dataset downloads are disabled by default; "
                "use the homomorphic-encryption workflow or explicitly set "
                "MEDICAL_ALLOW_PLAINTEXT_DOWNLOADS=true for a controlled demo"
            ),
        )

    actor_org = researcher_org(identity)
    actor_address = ethereum_account_address(actor_org).lower()

    allowed = query(
        "CanAccess",
        [request_id],
        actor_org,
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
            actor_org,
        )
    )

    if str(request_data.get("requesterAddress") or "").lower() != actor_address:
        raise HTTPException(
            status_code=403,
            detail="Access request belongs to a different researcher identity",
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
from backend.dicom_he_api import router as dicom_he_router
app.include_router(dicom_he_router)
