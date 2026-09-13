import json
import math

from fastapi import (
    APIRouter,
    HTTPException,
)
from pydantic import BaseModel

from backend.he_service import (
    compute_encrypted_average,
    create_encrypted_glucose_cohort,
    decrypt_average,
)


router = APIRouter(
    prefix="/he/glucose",
    tags=["Homomorphic Encryption"],
)


class GlucoseCohortInput(BaseModel):
    values: list[float]


def _fabric_invoke(
    function: str,
    args: list[str],
    org: str,
) -> None:
    # Imported lazily to avoid circular import while
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

        result = (
            create_encrypted_glucose_cohort(
                payload.values
            )
        )

        _fabric_invoke(
            "RegisterHEJob",
            [
                result["job_id"],
                "fasting_glucose",
                str(result["count"]),
                result[
                    "ciphertext_manifest_sha256"
                ],
            ],
            "org1",
        )

        ledger = _fabric_query(
            "ReadHEJob",
            [result["job_id"]],
            "org1",
        )

        result["fabric_status"] = (
            ledger["status"]
        )

        result["fabric_owner_org"] = (
            ledger["ownerOrg"]
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
            detail=f"Homomorphic encryption failed: {exc}",
        ) from exc


@router.post(
    "/{job_id}/compute-average"
)
def compute_average(
    job_id: str,
):
    try:
        result = (
            compute_encrypted_average(
                job_id
            )
        )

        _fabric_invoke(
            "RecordHEComputation",
            [
                job_id,
                result["result_sha256"],
            ],
            "org2",
        )

        ledger = _fabric_query(
            "ReadHEJob",
            [job_id],
            "org2",
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
            detail=f"Encrypted computation failed: {exc}",
        ) from exc


@router.post(
    "/{job_id}/decrypt-average"
)
def decrypt_glucose_average(
    job_id: str,
):
    try:
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
            detail=f"HE result decryption failed: {exc}",
        ) from exc


@router.get(
    "/{job_id}/ledger"
)
def read_he_ledger(
    job_id: str,
):
    try:
        return _fabric_query(
            "ReadHEJob",
            [job_id],
            "org2",
        )

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Fabric query failed: {exc}",
        ) from exc


@router.get(
    "/{job_id}/history"
)
def read_he_history(
    job_id: str,
):
    try:
        return _fabric_query(
            "GetHEJobHistory",
            [job_id],
            "org2",
        )

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Fabric history query failed: {exc}",
        ) from exc
