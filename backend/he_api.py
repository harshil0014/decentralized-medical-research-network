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
    get_ciphertext_manifest_sha256,
    get_encrypted_result_sha256,
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
    try:
        # First enforce the existing Fabric research policy.
        _require_approved_access(
            payload.dataset_id,
            payload.request_id,
        )

        # Dataset metadata comes from Fabric, not the client.
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

        # Retrieve the actual registered dataset from IPFS.
        dataset_bytes = fetch_ipfs_dataset_bytes(
            dataset["cid"]
        )

        # Verify the bytes against the immutable Fabric hash
        # BEFORE extracting any medical values.
        verified_sha256 = verify_dataset_bytes(
            dataset_bytes,
            dataset["sha256"],
        )

        values = extract_numeric_metric_from_csv(
            dataset_bytes,
            payload.metric,
        )

        # Only now do the real hospital-side SEAL encryption.
        result = create_encrypted_glucose_cohort(
            values
        )

        try:
            _fabric_invoke(
                "RegisterHEJob",
                [
                    result["job_id"],
                    payload.dataset_id,
                    payload.request_id,
                    payload.metric,
                    str(result["count"]),
                    result[
                        "ciphertext_manifest_sha256"
                    ],
                ],
                "org1",
            )

        except Exception:
            cleanup_he_job(
                result["job_id"]
            )
            raise

        ledger = _fabric_query(
            "ReadHEJob",
            [result["job_id"]],
            "org1",
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
                f"Dataset-bound HE encryption failed: {exc}"
            ),
        ) from exc


@router.post(
    "/{job_id}/compute-average"
)
def compute_average(
    job_id: str,
):
    try:
        ledger = _fabric_query(
            "ReadHEJob",
            [job_id],
            "org2",
        )

        _require_approved_access(
            ledger["datasetId"],
            ledger["requestId"],
        )

        # Integrity check: the ciphertext set used
        # by the researcher must still match the hash
        # registered by the hospital on Fabric.
        actual_manifest = (
            get_ciphertext_manifest_sha256(
                job_id
            )
        )

        if (
            actual_manifest
            != ledger["ciphertextManifestSha256"]
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Encrypted cohort integrity "
                    "verification failed"
                ),
            )

        result = (
            compute_encrypted_average(
                job_id
            )
        )

        try:
            _fabric_invoke(
                "RecordHEComputation",
                [
                    job_id,
                    result["result_sha256"],
                ],
                "org2",
            )

        except Exception:
            # If access was revoked between our
            # pre-check and the ledger transaction,
            # discard the derived ciphertext.
            discard_encrypted_result(
                job_id
            )
            raise

        ledger = _fabric_query(
            "ReadHEJob",
            [job_id],
            "org2",
        )

        result["dataset_id"] = (
            ledger["datasetId"]
        )

        result["request_id"] = (
            ledger["requestId"]
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

    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Encrypted computation failed: {exc}"
            ),
        ) from exc


@router.post(
    "/{job_id}/decrypt-average"
)
def decrypt_glucose_average(
    job_id: str,
):
    try:
        ledger = _fabric_query(
            "ReadHEJob",
            [job_id],
            "org1",
        )

        if ledger["status"] != "COMPUTED":
            raise HTTPException(
                status_code=409,
                detail=(
                    "HE job is not ready for decryption"
                ),
            )

        # Hospital verifies the encrypted result
        # against the immutable Fabric hash before
        # allowing decryption.
        actual_result_hash = (
            get_encrypted_result_sha256(
                job_id
            )
        )

        if (
            actual_result_hash
            != ledger["resultSha256"]
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Encrypted result integrity "
                    "verification failed"
                ),
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

        result["dataset_id"] = (
            ledger["datasetId"]
        )

        result["request_id"] = (
            ledger["requestId"]
        )

        result["fabric_status"] = (
            ledger["status"]
        )

        return result

    except HTTPException:
        raise

    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"HE result decryption failed: {exc}"
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
