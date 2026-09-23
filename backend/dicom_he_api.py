import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.api_auth import require_authenticated, require_hospital, require_researcher
from backend.runtime_security import require_mutation_lock
from backend.storage_crypto import decrypt_bytes, is_encrypted_dataset
from backend.he_service import fetch_ipfs_dataset_bytes, unpin_ipfs, verify_dataset_bytes
from backend.dicom_he_service import (
    SUPPORTED_ANALYSES,
    SUPPORTED_HE_MODES,
    cleanup_dicom_he_job,
    compute_encrypted_dicom_analysis,
    create_encrypted_dicom_job,
    decrypt_dicom_analysis,
    discard_encrypted_dicom_result,
    extract_dicom_analysis_values,
    publish_dicom_ciphertext_bundle,
    publish_encrypted_dicom_result,
    remove_dicom_research_exchange,
    restore_dicom_ciphertext_bundle,
    restore_encrypted_dicom_result,
)

router = APIRouter(prefix="/he/dicom", tags=["DICOM Homomorphic Encryption"])


class DicomROIBox(BaseModel):
    slice_start: int
    slice_end: int
    row_start: int
    row_end: int
    col_start: int
    col_end: int


class DicomHEInput(BaseModel):
    dataset_id: str
    request_id: str
    analysis: str = "MEAN"
    he_mode: str = "RAW_VOXELS"
    scope: str = "WHOLE_VOLUME"
    slice_index: int | None = None
    roi_box: DicomROIBox | None = None


def _invoke(function: str, args: list[str], org: str) -> None:
    from backend.app import invoke
    invoke(function, args, org)


def _query(function: str, args: list[str], org: str):
    from backend.app import query
    return json.loads(query(function, args, org))


def _require_approved_access(dataset_id: str, request_id: str) -> dict:
    request = _query("ReadAccessRequest", [request_id], "org2")
    if request.get("datasetId") != dataset_id:
        raise HTTPException(status_code=403, detail="Access request does not belong to this dataset")
    if request.get("status") != "APPROVED":
        raise HTTPException(status_code=403, detail="Research access is not approved")
    from backend.app import query
    if query("CanAccess", [request_id], "org2") != "true":
        raise HTTPException(status_code=403, detail="Research access is no longer authorized")
    return request


def _require_dicom_job(ledger: dict) -> None:
    metric = str(ledger.get("metric") or "")
    if not metric.startswith("DICOM:"):
        raise HTTPException(status_code=400, detail="HE job is not a DICOM analysis job")


@router.post("/encrypt", dependencies=[Depends(require_hospital), Depends(require_mutation_lock)])
def encrypt_dicom(payload: DicomHEInput):
    ciphertext_cid = None
    job_id = None
    try:
        analysis = payload.analysis.strip().upper()
        if analysis not in SUPPORTED_ANALYSES:
            raise HTTPException(
                status_code=400,
                detail="analysis must be one of: " + ", ".join(sorted(SUPPORTED_ANALYSES)),
            )

        he_mode = payload.he_mode.strip().upper()
        if he_mode not in SUPPORTED_HE_MODES:
            raise HTTPException(
                status_code=400,
                detail="he_mode must be one of: " + ", ".join(sorted(SUPPORTED_HE_MODES)),
            )

        _require_approved_access(payload.dataset_id, payload.request_id)
        dataset = _query("ReadDatasetPrivate", [payload.dataset_id], "org1")

        if dataset.get("consentState") != "ACTIVE":
            raise HTTPException(status_code=403, detail="Dataset consent is not active")

        data_type = str(dataset.get("dataType") or "").upper()
        if not data_type.startswith("DICOM"):
            raise HTTPException(status_code=400, detail="DICOM HE requires a DICOM dataset")

        stored = fetch_ipfs_dataset_bytes(dataset["cid"])
        verified_sha256 = verify_dataset_bytes(stored, dataset["sha256"])

        if is_encrypted_dataset(stored):
            dicom_bytes = decrypt_bytes(payload.dataset_id, stored)
            storage_encryption = "AES-256-GCM"
        else:
            dicom_bytes = stored
            storage_encryption = "LEGACY-PLAINTEXT"

        roi_box = payload.roi_box.model_dump() if payload.roi_box else None
        values, metadata = extract_dicom_analysis_values(
            dicom_bytes,
            scope=payload.scope,
            slice_index=payload.slice_index,
            roi_box=roi_box,
        )
        result = create_encrypted_dicom_job(
            values,
            analysis,
            metadata,
            he_mode=he_mode,
        )
        job_id = result["job_id"]
        ciphertext_cid = publish_dicom_ciphertext_bundle(job_id)

        metric = f'DICOM:{analysis}:{metadata["scope"]}:{he_mode}'
        try:
            _invoke(
                "RegisterHEJob",
                [
                    job_id,
                    payload.dataset_id,
                    payload.request_id,
                    metric,
                    str(result["voxel_count"]),
                    ciphertext_cid,
                    result["ciphertext_manifest_sha256"],
                ],
                "org1",
            )
        except Exception:
            unpin_ipfs(ciphertext_cid)
            cleanup_dicom_he_job(job_id)
            raise

        ledger = _query("ReadHEJob", [job_id], "org1")
        if ledger.get("ciphertextCid") != ciphertext_cid:
            raise RuntimeError("Ethereum ciphertext CID verification failed")

        remove_dicom_research_exchange(job_id)

        result.update(
            {
                "dataset_id": payload.dataset_id,
                "request_id": payload.request_id,
                "dataset_sha256_verified": True,
                "dataset_sha256": verified_sha256,
                "dataset_storage_encryption": storage_encryption,
                "ciphertext_cid": ciphertext_cid,
                "ciphertext_storage": "IPFS",
                "ethereum_status": ledger["status"],
                "ethereum_owner_role": "Hospital",
                "ethereum_owner_address": ledger.get("ownerAddress", ""),
                "ethereum_researcher_role": "Researcher",
                "ethereum_researcher_address": ledger.get("researcherAddress", ""),
            }
        )
        return result

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DICOM HE encryption failed: {exc}") from exc


@router.post("/{job_id}/compute", dependencies=[Depends(require_researcher), Depends(require_mutation_lock)])
def compute_dicom(job_id: str):
    result_cid = None
    try:
        ledger = _query("ReadHEJob", [job_id], "org2")
        _require_dicom_job(ledger)
        if ledger.get("status") != "ENCRYPTED":
            raise HTTPException(status_code=409, detail="DICOM HE job is not in ENCRYPTED state")

        _require_approved_access(ledger["datasetId"], ledger["requestId"])

        restore_dicom_ciphertext_bundle(
            job_id,
            ledger["ciphertextCid"],
            ledger["ciphertextManifestSha256"],
        )
        result = compute_encrypted_dicom_analysis(job_id)
        published = publish_encrypted_dicom_result(job_id)
        result_cid = published["cid"]

        if published["sha256"] != result["result_sha256"]:
            unpin_ipfs(result_cid)
            raise RuntimeError("Encrypted DICOM result changed before IPFS publication")

        try:
            _invoke(
                "RecordHEComputation",
                [job_id, result_cid, result["result_sha256"]],
                "org2",
            )
        except Exception:
            unpin_ipfs(result_cid)
            discard_encrypted_dicom_result(job_id)
            remove_dicom_research_exchange(job_id)
            raise

        ledger = _query("ReadHEJob", [job_id], "org2")
        if ledger.get("resultCid") != result_cid:
            raise RuntimeError("Ethereum DICOM result CID verification failed")

        remove_dicom_research_exchange(job_id)
        result.update(
            {
                "dataset_id": ledger["datasetId"],
                "request_id": ledger["requestId"],
                "result_cid": result_cid,
                "result_storage": "IPFS",
                "ethereum_status": ledger["status"],
                "ethereum_researcher_role": "Researcher",
                "ethereum_researcher_address": ledger.get("researcherAddress", ""),
            }
        )
        return result

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DICOM HE computation failed: {exc}") from exc


@router.post("/{job_id}/decrypt", dependencies=[Depends(require_hospital), Depends(require_mutation_lock)])
def decrypt_dicom(job_id: str):
    try:
        ledger = _query("ReadHEJob", [job_id], "org1")
        _require_dicom_job(ledger)
        if ledger.get("status") != "COMPUTED":
            raise HTTPException(status_code=409, detail="DICOM HE job is not in COMPUTED state")

        restore_dicom_ciphertext_bundle(
            job_id,
            ledger["ciphertextCid"],
            ledger["ciphertextManifestSha256"],
        )
        verified_result = restore_encrypted_dicom_result(
            job_id,
            ledger["resultCid"],
            ledger["resultSha256"],
        )

        result = decrypt_dicom_analysis(job_id)
        if isinstance(result.get("value"), (int, float)):
            result["value"] = round(float(result["value"]), 6)

        _invoke("RecordHEDecryption", [job_id], "org1")
        ledger = _query("ReadHEJob", [job_id], "org1")
        remove_dicom_research_exchange(job_id)

        result.update(
            {
                "dataset_id": ledger["datasetId"],
                "request_id": ledger["requestId"],
                "ciphertext_cid": ledger["ciphertextCid"],
                "result_cid": ledger["resultCid"],
                "result_sha256_verified": verified_result,
                "ethereum_status": ledger["status"],
            }
        )
        return result

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DICOM HE decryption failed: {exc}") from exc


@router.get("/{job_id}/ledger", dependencies=[Depends(require_authenticated)])
def read_dicom_ledger(job_id: str):
    record = _query("ReadHEJob", [job_id], "org2")
    _require_dicom_job(record)
    if record.get("ownerAddress"):
        record["ownerRole"] = "Hospital"
        record.pop("ownerOrg", None)
    if record.get("researcherAddress"):
        record["researcherRole"] = "Researcher"
        record.pop("researcherOrg", None)
    return record


@router.get("/{job_id}/history", dependencies=[Depends(require_authenticated)])
def read_dicom_history(job_id: str):
    history = _query("GetHEJobHistory", [job_id], "org2")
    for item in history:
        value = item.get("value")
        if not isinstance(value, dict):
            continue
        if value.get("ownerAddress"):
            value["ownerRole"] = "Hospital"
            value.pop("ownerOrg", None)
        if value.get("researcherAddress"):
            value["researcherRole"] = "Researcher"
            value.pop("researcherOrg", None)
    return history
