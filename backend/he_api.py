import hashlib
import json

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)

from pydantic import BaseModel

from backend.api_auth import (
    AuthIdentity,
    researcher_org,
    require_authenticated,
    require_hospital,
    require_researcher,
)

from backend.runtime_security import (
    require_mutation_lock,
    require_job_lock,
)
from backend.researcher_signing import verify_researcher_signature

from backend.storage_crypto import (
    decrypt_bytes,
    is_encrypted_dataset,
)

from backend.he_service import (
    cleanup_he_job,
    compute_encrypted_average,
    create_encrypted_glucose_cohort,
    decrypt_average,
    discard_encrypted_result,
    extract_numeric_metric_from_csv,
    fetch_ipfs_dataset_bytes,
    publish_ciphertext_bundle_to_ipfs,
    publish_encrypted_result_to_ipfs,
    remove_research_exchange,
    restore_ciphertext_bundle_from_ipfs,
    restore_encrypted_result_from_ipfs,
    unpin_ipfs,
    verify_dataset_bytes,
)


router = APIRouter(
    prefix="/he/glucose",
    tags=["Homomorphic Encryption"],
)


class GlucoseCohortInput(BaseModel):
    dataset_id: str
    request_id: str
    metric: str = "fasting_glucose"


class ResearcherSignatureInput(BaseModel):
    signature: str


def _ledger_invoke(
    function: str,
    args: list[str],
    org: str,
) -> None:
    # Lazy import avoids circular import while
    # backend.app is registering this router.
    from backend.app import invoke

    invoke(
        function,
        args,
        org,
    )


def _ledger_query(
    function: str,
    args: list[str],
    org: str,
):
    from backend.app import query

    raw = query(
        function,
        args,
        org,
    )

    return json.loads(raw)


def _ledger_scalar_query(
    function: str,
    args: list[str],
    org: str,
) -> str:
    from backend.app import query
    return query(function, args, org)


def _require_approved_access(
    dataset_id: str,
    request_id: str,
    *,
    actor_org: str | None = None,
    actor_address: str | None = None,
) -> dict:

    query_org = actor_org or "org2"

    request = _ledger_query(
        "ReadAccessRequest",
        [request_id],
        query_org,
    )

    if request.get("datasetId") != dataset_id:
        raise HTTPException(
            status_code=403,
            detail=(
                "Access request does not belong "
                "to this dataset"
            ),
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

    allowed = _ledger_query(
        "CanAccess",
        [request_id],
        query_org,
    )

    if allowed is not True:
        raise HTTPException(
            status_code=403,
            detail=(
                "Research access is no longer authorized"
            ),
        )

    return request


@router.post(
    "/encrypt",
    dependencies=[
        Depends(require_hospital),
        Depends(require_mutation_lock),
    ],
)
def encrypt_glucose_cohort(
    payload: GlucoseCohortInput,
):
    ciphertext_cid = None
    job_id = None

    try:
        _require_approved_access(
            payload.dataset_id,
            payload.request_id,
        )

        dataset = _ledger_query(
            "ReadDatasetPrivate",
            [payload.dataset_id],
            "org1",
        )

        if dataset.get("consentState") != "ACTIVE":
            raise HTTPException(
                status_code=403,
                detail="Dataset consent is not active",
            )

        data_type = (
            dataset.get("dataType")
            or ""
        ).upper()

        if data_type != "CSV":
            raise HTTPException(
                status_code=400,
                detail=(
                    "HE numeric extraction currently supports "
                    "LAB_CSV, NUMERIC_CSV or CSV datasets only"
                ),
            )

        stored_dataset_bytes = fetch_ipfs_dataset_bytes(
            dataset["cid"]
        )

        # Verify the exact IPFS object against Ethereum BEFORE
        # attempting authenticated decryption.
        verified_sha256 = verify_dataset_bytes(
            stored_dataset_bytes,
            dataset["sha256"],
        )

        if not is_encrypted_dataset(stored_dataset_bytes):
            raise HTTPException(
                status_code=409,
                detail="Unencrypted legacy dataset objects are not supported",
            )

        dataset_bytes = decrypt_bytes(
            payload.dataset_id,
            stored_dataset_bytes,
        )
        dataset_storage_encryption = "AES-256-GCM"

        values = extract_numeric_metric_from_csv(
            dataset_bytes,
            payload.metric,
        )

        result = create_encrypted_glucose_cohort(
            values
        )

        job_id = result["job_id"]

        # Only the researcher-safe encrypted exchange is published.
        # hospital_private/secret.key is outside this bundle.
        ciphertext_cid = (
            publish_ciphertext_bundle_to_ipfs(
                job_id
            )
        )

        try:
            _ledger_invoke(
                "RegisterHEJob",
                [
                    job_id,
                    payload.dataset_id,
                    payload.request_id,
                    "",
                    "",
                    "CSV:sha256:" + hashlib.sha256(
                        payload.metric.strip().encode("utf-8")
                    ).hexdigest(),
                    str(result["count"]),
                    ciphertext_cid,
                    result[
                        "ciphertext_manifest_sha256"
                    ],
                ],
                "org1",
            )

        except Exception:
            unpin_ipfs(ciphertext_cid)
            cleanup_he_job(job_id)
            raise

        ledger = _ledger_query(
            "ReadHEJob",
            [job_id],
            "org1",
        )

        if ledger.get("ciphertextCid") != ciphertext_cid:
            raise RuntimeError(
                "Ethereum ciphertext CID verification failed"
            )

        # Prove that subsequent research computation must
        # reconstruct its workspace from IPFS.
        remove_research_exchange(
            job_id
        )

        result["dataset_id"] = (
            payload.dataset_id
        )

        result["request_id"] = (
            payload.request_id
        )

        result["metric"] = (
            payload.metric
        )

        result["dataset_sha256_verified"] = True
        result["dataset_sha256"] = verified_sha256

        result["dataset_storage_encryption"] = (
            dataset_storage_encryption
        )

        result["ciphertext_cid"] = (
            ciphertext_cid
        )

        result["ciphertext_storage"] = (
            "IPFS"
        )

        result["ethereum_status"] = (
            ledger["status"]
        )

        result["ethereum_owner_role"] = "Hospital"
        result["ethereum_owner_address"] = (
            ledger.get("ownerAddress", "")
        )

        result["ethereum_researcher_role"] = "Researcher"
        result["ethereum_researcher_address"] = (
            ledger.get("researcherAddress", "")
        )

        return result

    except HTTPException:
        raise

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"IPFS-backed HE encryption failed: {exc}"
            ),
        ) from exc


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
        raise HTTPException(
            status_code=403,
            detail="HE job belongs to a different researcher wallet",
        )
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
    "/{job_id}/compute-average",
    dependencies=[
        Depends(require_job_lock),
    ],
)
def compute_average(
    job_id: str,
    body: ResearcherSignatureInput,
    identity: AuthIdentity = Depends(require_researcher),
):
    result_cid = None

    try:
        actor_org = researcher_org(identity)
        from backend.ethereum_ledger import account_address
        actor_address = account_address(actor_org)

        ledger = _ledger_query(
            "ReadHEJob",
            [job_id],
            actor_org,
        )

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

        signing_digest = _ledger_scalar_query(
            "HEComputeDigest",
            [job_id],
            actor_org,
        )
        verify_researcher_signature(
            identity,
            signing_digest,
            body.signature,
        )

        ciphertext_cid = ledger.get(
            "ciphertextCid"
        )

        if not ciphertext_cid:
            raise HTTPException(
                status_code=409,
                detail=(
                    "HE job has no IPFS ciphertext CID; "
                    "it predates IPFS-backed HE storage"
                ),
            )

        restored_manifest = (
            restore_ciphertext_bundle_from_ipfs(
                job_id,
                ciphertext_cid,
                ledger[
                    "ciphertextManifestSha256"
                ],
            )
        )

        result = compute_encrypted_average(
            job_id
        )

        published = (
            publish_encrypted_result_to_ipfs(
                job_id
            )
        )

        result_cid = published["cid"]

        if (
            published["sha256"]
            != result["result_sha256"]
        ):
            unpin_ipfs(result_cid)

            raise RuntimeError(
                "Encrypted result hash changed before IPFS publication"
            )

        try:
            _ledger_invoke(
                "RecordHEComputationSigned",
                [
                    job_id,
                    result_cid,
                    result["result_sha256"],
                    body.signature,
                ],
                "org1",
            )

        except Exception:
            unpin_ipfs(result_cid)

            discard_encrypted_result(
                job_id
            )

            remove_research_exchange(
                job_id
            )

            raise

        ledger = _ledger_query(
            "ReadHEJob",
            [job_id],
            actor_org,
        )

        if ledger.get("resultCid") != result_cid:
            raise RuntimeError(
                "Ethereum result CID verification failed"
            )

        # Researcher workspace is disposable.
        # The durable encrypted artifacts now live in IPFS.
        remove_research_exchange(
            job_id
        )

        result["dataset_id"] = (
            ledger["datasetId"]
        )

        result["request_id"] = (
            ledger["requestId"]
        )

        result["metric"] = (
            ledger.get("metric")
            or result.get("metric")
        )

        result["ciphertext_cid"] = (
            ciphertext_cid
        )

        result["ciphertext_manifest_sha256"] = (
            restored_manifest
        )

        result["result_cid"] = (
            result_cid
        )

        result["result_storage"] = (
            "IPFS"
        )

        result["ethereum_status"] = (
            ledger["status"]
        )

        result["ethereum_researcher_role"] = "Researcher"
        result["ethereum_researcher_address"] = (
            ledger.get("researcherAddress", "")
        )

        return result

    except HTTPException:
        raise

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"IPFS-backed HE computation failed: {exc}"
            ),
        ) from exc


@router.post(
    "/{job_id}/decrypt-average",
    dependencies=[
        Depends(require_hospital),
        Depends(require_mutation_lock),
    ],
)
def decrypt_he_average(
    job_id: str,
):
    try:
        ledger = _ledger_query(
            "ReadHEJob",
            [job_id],
            "org1",
        )

        if ledger.get("status") != "COMPUTED":
            raise HTTPException(
                status_code=409,
                detail="HE job is not in COMPUTED state",
            )

        _require_approved_access(
            ledger["datasetId"],
            ledger["requestId"],
        )

        ciphertext_cid = ledger.get(
            "ciphertextCid"
        )

        result_cid = ledger.get(
            "resultCid"
        )

        if not ciphertext_cid or not result_cid:
            raise HTTPException(
                status_code=409,
                detail=(
                    "HE job does not contain IPFS-backed artifacts"
                ),
            )

        # Rebuild the non-secret SEAL context from IPFS.
        restore_ciphertext_bundle_from_ipfs(
            job_id,
            ciphertext_cid,
            ledger[
                "ciphertextManifestSha256"
            ],
        )

        # Fetch the encrypted aggregate independently from IPFS.
        verified_result_sha256 = (
            restore_encrypted_result_from_ipfs(
                job_id,
                result_cid,
                ledger["resultSha256"],
            )
        )

        result = decrypt_average(
            job_id
        )

        # CKKS is approximate arithmetic. Present a stable demo value
        # while preserving that the result came from approximate HE.
        if isinstance(result.get("average"), (int, float)):
            result["average"] = round(float(result["average"]), 6)
            result["ckks_approximate"] = True

        _ledger_invoke(
            "RecordHEDecryption",
            [job_id],
            "org1",
        )

        ledger = _ledger_query(
            "ReadHEJob",
            [job_id],
            "org1",
        )

        # Leave the hospital secret key local,
        # but discard all fetched researcher artifacts.
        remove_research_exchange(
            job_id
        )

        result["dataset_id"] = (
            ledger["datasetId"]
        )

        result["request_id"] = (
            ledger["requestId"]
        )

        result["metric"] = (
            ledger.get("metric")
            or result.get("metric")
        )

        result["ciphertext_cid"] = (
            ciphertext_cid
        )

        result["result_cid"] = (
            result_cid
        )

        result["result_sha256_verified"] = (
            verified_result_sha256
        )

        result["ethereum_status"] = (
            ledger["status"]
        )

        return result

    except HTTPException:
        raise

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"IPFS-backed HE decryption failed: {exc}"
            ),
        ) from exc


@router.get(
    "/{job_id}/ledger",
    dependencies=[
        Depends(require_authenticated)
    ],
)
def read_he_ledger(
    job_id: str,
):
    record = _ledger_query(
        "ReadHEJob",
        [job_id],
        "org2",
    )
    if record.get("ownerAddress"):
        record["ownerRole"] = "Hospital"
        record.pop("ownerOrg", None)
    if record.get("researcherAddress"):
        record["researcherRole"] = "Researcher"
        record.pop("researcherOrg", None)
    return record


@router.get(
    "/{job_id}/history",
    dependencies=[
        Depends(require_authenticated)
    ],
)
def read_he_history(
    job_id: str,
    offset: int = 0,
    limit: int = 50,
):
    if offset < 0 or not 1 <= limit <= 100:
        raise HTTPException(status_code=400, detail="Invalid pagination")
    history = _ledger_query(
        "GetHEJobHistory",
        [job_id, str(offset), str(limit)],
        "org2",
    )

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
