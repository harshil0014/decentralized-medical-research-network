import json
import math

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
    get_ciphertext_manifest_sha256,
    get_encrypted_result_sha256,
)


router = APIRouter(
    prefix="/he/glucose",
    tags=["Homomorphic Encryption"],
)


class GlucoseCohortInput(BaseModel):
    dataset_id: str
    request_id: str
    values: list[float]


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
        if any(
            not math.isfinite(float(v))
            for v in payload.values
        ):
            raise ValueError(
                "All values must be finite"
            )

        # Avoid creating ciphertext at all unless
        # Fabric currently authorizes this request.
        _require_approved_access(
            payload.dataset_id,
            payload.request_id,
        )

        result = (
            create_encrypted_glucose_cohort(
                payload.values
            )
        )

        try:
            _fabric_invoke(
                "RegisterHEJob",
                [
                    result["job_id"],
                    payload.dataset_id,
                    payload.request_id,
                    "fasting_glucose",
                    str(result["count"]),
                    result[
                        "ciphertext_manifest_sha256"
                    ],
                ],
                "org1",
            )

        except Exception:
            # Do not leave an orphan secret key /
            # ciphertext job if ledger registration fails.
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

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Homomorphic encryption failed: {exc}"
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
