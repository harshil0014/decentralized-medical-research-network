import json

from fastapi import (
    APIRouter,
    HTTPException,
)

from pydantic import BaseModel

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


def _fabric_invoke(
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


def _fabric_query(
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


def _require_approved_access(
    dataset_id: str,
    request_id: str,
) -> dict:

    request = _fabric_query(
        "ReadAccessRequest",
        [request_id],
        "org2",
    )

    if request.get("datasetId") != dataset_id:
        raise HTTPException(
            status_code=403,
            detail=(
                "Access request does not belong "
                "to this dataset"
            ),
        )

    if request.get("status") != "APPROVED":
        raise HTTPException(
            status_code=403,
            detail="Research access is not approved",
        )

    allowed = _fabric_query(
        "CanAccess",
        [request_id],
        "org2",
    )

    if allowed is not True:
        raise HTTPException(
            status_code=403,
            detail=(
                "Research access is no longer authorized"
            ),
        )

    return request


@router.post("/encrypt")
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

        dataset = _fabric_query(
            "ReadDataset",
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

        if data_type not in {
            "LAB_CSV",
            "NUMERIC_CSV",
            "CSV",
        }:
            raise HTTPException(
                status_code=400,
                detail=(
                    "HE numeric extraction currently supports "
                    "LAB_CSV, NUMERIC_CSV or CSV datasets only"
                ),
            )

        dataset_bytes = fetch_ipfs_dataset_bytes(
            dataset["cid"]
        )

        verified_sha256 = verify_dataset_bytes(
            dataset_bytes,
            dataset["sha256"],
        )

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
            _fabric_invoke(
                "RegisterHEJob",
                [
                    job_id,
                    payload.dataset_id,
                    payload.request_id,
                    payload.metric,
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

        ledger = _fabric_query(
            "ReadHEJob",
            [job_id],
            "org1",
        )

        if ledger.get("ciphertextCid") != ciphertext_cid:
            raise RuntimeError(
                "Fabric ciphertext CID verification failed"
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

        result["ciphertext_cid"] = (
            ciphertext_cid
        )

        result["ciphertext_storage"] = (
            "IPFS"
        )

        result["fabric_status"] = (
            ledger["status"]
        )

        result["fabric_owner_org"] = (
            ledger["ownerOrg"]
        )

        result["fabric_researcher_org"] = (
            ledger["researcherOrg"]
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


@router.post(
    "/{job_id}/compute-average"
)
def compute_average(
    job_id: str,
):
    result_cid = None

    try:
        ledger = _fabric_query(
            "ReadHEJob",
            [job_id],
            "org2",
        )

        if ledger.get("status") != "ENCRYPTED":
            raise HTTPException(
                status_code=409,
                detail="HE job is not in ENCRYPTED state",
            )

        _require_approved_access(
            ledger["datasetId"],
            ledger["requestId"],
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
            _fabric_invoke(
                "RecordHEComputation",
                [
                    job_id,
                    result_cid,
                    result["result_sha256"],
                ],
                "org2",
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

        ledger = _fabric_query(
            "ReadHEJob",
            [job_id],
            "org2",
        )

        if ledger.get("resultCid") != result_cid:
            raise RuntimeError(
                "Fabric result CID verification failed"
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

        result["fabric_status"] = (
            ledger["status"]
        )

        result["fabric_researcher_org"] = (
            ledger["researcherOrg"]
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
    "/{job_id}/decrypt-average"
)
def decrypt_he_average(
    job_id: str,
):
    try:
        ledger = _fabric_query(
            "ReadHEJob",
            [job_id],
            "org1",
        )

        if ledger.get("status") != "COMPUTED":
            raise HTTPException(
                status_code=409,
                detail="HE job is not in COMPUTED state",
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

        _fabric_invoke(
            "RecordHEDecryption",
            [job_id],
            "org1",
        )

        ledger = _fabric_query(
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

        result["ciphertext_cid"] = (
            ciphertext_cid
        )

        result["result_cid"] = (
            result_cid
        )

        result["result_sha256_verified"] = (
            verified_result_sha256
        )

        result["fabric_status"] = (
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
    "/{job_id}/ledger"
)
def read_he_ledger(
    job_id: str,
):
    return _fabric_query(
        "ReadHEJob",
        [job_id],
        "org2",
    )


@router.get(
    "/{job_id}/history"
)
def read_he_history(
    job_id: str,
):
    return _fabric_query(
        "GetHEJobHistory",
        [job_id],
        "org2",
    )
