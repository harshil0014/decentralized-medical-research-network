import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.api_auth import (
    AuthIdentity,
    researcher_org,
    require_hospital,
    require_researcher,
)
from backend.runtime_security import require_mutation_lock, require_job_lock
from backend.researcher_signing import verify_researcher_signature
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


class ResearcherSignatureInput(BaseModel):
    signature: str


def _ledger_invoke(function: str, args: list[str], org: str) -> None:
    from backend.app import invoke
    invoke(function, args, org)


def _ledger_query(function: str, args: list[str], org: str):
    from backend.app import query
    return json.loads(query(function, args, org))


def _ledger_scalar_query(function: str, args: list[str], org: str) -> str:
    from backend.app import query
    return query(function, args, org)


def _require_approved_access(
    dataset_id: str,
    request_id: str,
    *,
    actor_org: str | None = None,
    actor_address: str | None = None,
) -> None:
    query_org = actor_org or "org2"
    request = _ledger_query("ReadAccessRequest", [request_id], query_org)

    if request.get("datasetId") != dataset_id:
        raise HTTPException(
            status_code=403,
            detail="Access request does not belong to this dataset",
        )

    if actor_address is not None and str(
        request.get("requesterAddress") or ""
    ).lower() != actor_address.lower():
        raise HTTPException(
            status_code=403,
            detail="Access request belongs to a different researcher identity",
        )

    if request.get("status") != "APPROVED":
        raise HTTPException(
            status_code=403,
            detail="Research access is not approved",
        )

    from backend.app import query
    if query("CanAccess", [request_id], query_org) != "true":
        raise HTTPException(
            status_code=403,
            detail="Research access is no longer authorized",
        )


@router.get("/{job_id}/signing-digest")
def compute_signing_digest(
    job_id: str,
    identity: AuthIdentity = Depends(require_researcher),
):
    actor_org = researcher_org(identity)
    from backend.ethereum_ledger import account_address
    actor_address = account_address(actor_org)
    ledger = _ledger_query("ReadHEJob", [job_id], actor_org)
    if str(ledger.get("researcherAddress") or "").lower() != actor_address.lower():
        raise HTTPException(status_code=403, detail="HE job belongs to a different researcher wallet")
    if ledger.get("status") != "ENCRYPTED":
        raise HTTPException(status_code=409, detail="HE job is not in ENCRYPTED state")
    digest = _ledger_scalar_query("HEComputeDigest", [job_id], actor_org)
    return {
        "jobId": job_id,
        "signingDigest": digest,
        "ethereumAddress": actor_address,
        "signatureScheme": "EIP-191 personal_sign",
    }


@router.post(
    "/{job_id}/compute",
    dependencies=[
        Depends(require_job_lock),
    ],
)
def compute_sum(
    job_id: str,
    body: ResearcherSignatureInput,
    identity: AuthIdentity = Depends(require_researcher),
):
    result_cid = None

    try:
        actor_org = researcher_org(identity)
        from backend.ethereum_ledger import account_address
        actor_address = account_address(actor_org)
        ledger = _ledger_query("ReadHEJob", [job_id], actor_org)

        if ledger.get("status") != "ENCRYPTED":
            raise HTTPException(
                status_code=409,
                detail="HE job is not in ENCRYPTED state",
            )

        _require_approved_access(
            ledger["datasetId"],
            ledger["requestId"],
            actor_org=actor_org,
            actor_address=actor_address,
        )

        signing_digest = _ledger_scalar_query("HEComputeDigest", [job_id], actor_org)
        verify_researcher_signature(identity, signing_digest, body.signature)

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
            _ledger_invoke(
                "RecordHEComputationSigned",
                [job_id, result_cid, result["result_sha256"], body.signature],
                "org1",
            )
        except Exception:
            unpin_ipfs(result_cid)
            discard_encrypted_sum(job_id)
            remove_research_exchange(job_id)
            raise

        ledger = _ledger_query("ReadHEJob", [job_id], actor_org)

        if ledger.get("resultCid") != result_cid:
            raise RuntimeError("Ethereum result CID verification failed")

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
        raise HTTPException(status_code=404, detail="Required HE artifact is unavailable") from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="HE SUM computation failed; inspect job state before retrying",
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
        ledger = _ledger_query("ReadHEJob", [job_id], "org1")

        if ledger.get("status") != "COMPUTED":
            raise HTTPException(
                status_code=409,
                detail="HE job is not in COMPUTED state",
            )

        _require_approved_access(
            ledger["datasetId"],
            ledger["requestId"],
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

        # CKKS uses approximate arithmetic; present a stable demo value.
        if isinstance(result.get("sum"), (int, float)):
            result["sum"] = round(float(result["sum"]), 6)
            result["ckks_approximate"] = True

        _ledger_invoke(
            "RecordHEDecryption",
            [job_id],
            "org1",
        )

        ledger = _ledger_query("ReadHEJob", [job_id], "org1")
        remove_research_exchange(job_id)

        result.update(
            {
                "dataset_id": ledger["datasetId"],
                "request_id": ledger["requestId"],
                "metric": ledger.get("metric"),
                "ciphertext_cid": ciphertext_cid,
                "result_cid": result_cid,
                "result_sha256_verified": verified_result_sha256,
                "ethereum_status": ledger["status"],
            }
        )

        return result

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Required HE artifact is unavailable") from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="HE SUM decryption failed; inspect job state before retrying",
        ) from exc
