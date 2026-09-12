import math

from fastapi import APIRouter, HTTPException
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

        return create_encrypted_glucose_cohort(
            payload.values
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Homomorphic encryption failed",
        ) from exc


@router.post("/{job_id}/compute-average")
def compute_average(
    job_id: str,
):
    try:
        return compute_encrypted_average(
            job_id
        )

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
            detail="Encrypted computation failed",
        ) from exc


@router.post("/{job_id}/decrypt-average")
def decrypt_glucose_average(
    job_id: str,
):
    try:
        return decrypt_average(
            job_id
        )

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
            detail="HE result decryption failed",
        ) from exc
