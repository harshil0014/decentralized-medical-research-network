import json

from fastapi import APIRouter, Depends, HTTPException

from backend.api_auth import require_hospital, require_researcher
from backend.runtime_security import require_mutation_lock
from backend.he_service import (
    remove_research_exchange,
    restore_ciphertext_bundle_from_ipfs,
    unpin_ipfs,
)
from backend.he_sum_service import (
    compute_encrypted_sum,
    decrypt_sum,
    discard_encrypted_sum,
    publish_encrypted_sum_to_ipfs,
    restore_encrypted_sum_from_ipfs,
)


router = APIRouter(
    prefix="/he/sum",
    tags=["Homomorphic Encryption"],
)


def _fabric_invoke(function: str, args: list[str], org: str) -> None:
    from backend.app import invoke
    invoke(function, args, org)


def _fabric_query(function: str, args: list[str], org: str):
    from backend.app import query
    return json.loads(query(function, args, org))


def _require_approved_access(dataset_id: str, request_id: str) -> None:
    request = _fabric_query("ReadAccessRequest", [request_id], "org2")

    if request.get("datasetId") != dataset_id:
        raise HTTPException(
            status_code=403,
            detail="Access request does not belong to this dataset",
        )

    if request.get("status") != "APPROVED":
        raise HTTPException(
            status_code=403,
            detail="Research access is not approved",
        )

    from backend.app import query
    if query("CanAccess", [request_id], "org2") != "true":
        raise HTTPException(
            status_code=403,
            detail="Research access is no longer authorized",
        )


@router.post(
    "/{job_id}/compute",
    dependencies=[
        Depends(require_researcher),
        Depends(require_mutation_lock),
    ],
)
def compute_sum(job_id: str):
    result_cid = None

    try:
        ledger = _fabric_query("ReadHEJob", [job_id], "org2")

        if ledger.get("status") != "ENCRYPTED":
            raise HTTPException(
                status_code=409,
                detail="HE job is not in ENCRYPTED state",
            )

        _require_approved_access(
            ledger["datasetId"],
            ledger["requestId"],
        )

        ciphertext_cid = ledger.get("ciphertextCid")
        if not ciphertext_cid:
            raise HTTPException(
                status_code=409,
                detail="HE job has no IPFS ciphertext CID",
            )

        restored_manifest = restore_ciphertext_bundle_from_ipfs(
            job_id,
            ciphertext_cid,
            ledger["ciphertextManifestSha256"],
        )

        result = compute_encrypted_sum(job_id)
        published = publish_encrypted_sum_to_ipfs(job_id)
        result_cid = published["cid"]

        if published["sha256"] != result["result_sha256"]:
            unpin_ipfs(result_cid)
            raise RuntimeError("Encrypted sum hash changed before IPFS publication")

        try:
            _fabric_invoke(
                "RecordHEComputation",
                [job_id, result_cid, result["result_sha256"]],
                "org2",
            )
        except Exception:
            unpin_ipfs(result_cid)
            discard_encrypted_sum(job_id)
            remove_research_exchange(job_id)
            raise

        ledger = _fabric_query("ReadHEJob", [job_id], "org2")

        if ledger.get("resultCid") != result_cid:
            raise RuntimeError("Fabric result CID verification failed")

        remove_research_exchange(job_id)

        result.update(
            {
                "dataset_id": ledger["datasetId"],
                "request_id": ledger["requestId"],
                "metric": ledger.get("metric"),
                "ciphertext_cid": ciphertext_cid,
                "ciphertext_manifest_sha256": restored_manifest,
                "result_cid": result_cid,
                "result_storage": "IPFS",
                "fabric_status": ledger["status"],
                "fabric_researcher_org": ledger["researcherOrg"],
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
        raise HTTPException(
            status_code=500,
            detail=f"IPFS-backed HE SUM computation failed: {exc}",
        ) from exc


@router.post(
    "/{job_id}/decrypt",
    dependencies=[
        Depends(require_hospital),
        Depends(require_mutation_lock),
    ],
)
def decrypt_he_sum(job_id: str):
    try:
        ledger = _fabric_query("ReadHEJob", [job_id], "org1")

        if ledger.get("status") != "COMPUTED":
            raise HTTPException(
                status_code=409,
                detail="HE job is not in COMPUTED state",
            )

        ciphertext_cid = ledger.get("ciphertextCid")
        result_cid = ledger.get("resultCid")

        if not ciphertext_cid or not result_cid:
            raise HTTPException(
                status_code=409,
                detail="HE job does not contain IPFS-backed artifacts",
            )

        restore_ciphertext_bundle_from_ipfs(
            job_id,
            ciphertext_cid,
            ledger["ciphertextManifestSha256"],
        )

        verified_result_sha256 = restore_encrypted_sum_from_ipfs(
            job_id,
            result_cid,
            ledger["resultSha256"],
        )

        result = decrypt_sum(job_id)

        _fabric_invoke(
            "RecordHEDecryption",
            [job_id],
            "org1",
        )

        ledger = _fabric_query("ReadHEJob", [job_id], "org1")
        remove_research_exchange(job_id)

        result.update(
            {
                "dataset_id": ledger["datasetId"],
                "request_id": ledger["requestId"],
                "metric": ledger.get("metric"),
                "ciphertext_cid": ciphertext_cid,
                "result_cid": result_cid,
                "result_sha256_verified": verified_result_sha256,
                "fabric_status": ledger["status"],
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
        raise HTTPException(
            status_code=500,
            detail=f"IPFS-backed HE SUM decryption failed: {exc}",
        ) from exc
