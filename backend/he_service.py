from __future__ import annotations

import math
import re
import shutil
import subprocess
import uuid
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SEAL_DEMO = PROJECT_ROOT / "seal_demo"
BUILD_DIR = SEAL_DEMO / "build"

HOSPITAL_ENCRYPT = BUILD_DIR / "hospital_encrypt"
RESEARCHER_COMPUTE = BUILD_DIR / "researcher_compute"
HOSPITAL_DECRYPT = BUILD_DIR / "hospital_decrypt"

RUNTIME_ROOT = Path("/tmp/medical-he-jobs")
RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)

JOB_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def _check_binaries() -> None:
    required = [
        HOSPITAL_ENCRYPT,
        RESEARCHER_COMPUTE,
        HOSPITAL_DECRYPT,
    ]

    missing = [str(p) for p in required if not p.exists()]

    if missing:
        raise RuntimeError(
            "Microsoft SEAL demo binaries are not built"
        )


def _job_dir(job_id: str) -> Path:
    if not JOB_PATTERN.fullmatch(job_id):
        raise ValueError("Invalid HE job ID")

    return RUNTIME_ROOT / job_id


def _run(binary: Path, cwd: Path) -> str:
    result = subprocess.run(
        [str(binary)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"HE process failed: {binary.name}"
        )

    return result.stdout


def create_encrypted_glucose_cohort(
    values: list[float],
) -> dict:
    _check_binaries()

    if not 2 <= len(values) <= 1000:
        raise ValueError(
            "Cohort must contain between 2 and 1000 values"
        )

    clean_values = []

    for value in values:
        numeric = float(value)

        if not math.isfinite(numeric):
            raise ValueError(
                "All glucose values must be finite numbers"
            )

        clean_values.append(numeric)

    job_id = uuid.uuid4().hex
    job = _job_dir(job_id)

    sample_dir = job / "sample_input"
    sample_dir.mkdir(parents=True)

    csv_path = sample_dir / "glucose_values.csv"

    csv_path.write_text(
        "".join(f"{value:.12g}\n" for value in clean_values)
    )

    _run(HOSPITAL_ENCRYPT, job)

    secret_key = job / "hospital_private" / "secret.key"
    exchange = job / "research_exchange"

    if not secret_key.exists():
        shutil.rmtree(job, ignore_errors=True)
        raise RuntimeError(
            "Hospital secret key was not created"
        )

    if any("secret" in p.name.lower() for p in exchange.iterdir()):
        shutil.rmtree(job, ignore_errors=True)
        raise RuntimeError(
            "Secret material leaked into researcher exchange"
        )

    return {
        "job_id": job_id,
        "count": len(clean_values),
        "state": "ENCRYPTED",
        "researcher_has_secret_key": False,
    }


def compute_encrypted_average(
    job_id: str,
) -> dict:
    job = _job_dir(job_id)

    if not job.exists():
        raise FileNotFoundError(
            "HE job not found"
        )

    exchange = job / "research_exchange"

    if not exchange.exists():
        raise RuntimeError(
            "Research exchange is missing"
        )

    if any("secret" in p.name.lower() for p in exchange.iterdir()):
        raise RuntimeError(
            "Secret material detected in researcher exchange"
        )

    _run(RESEARCHER_COMPUTE, job)

    result = exchange / "glucose_average.ct"

    if not result.exists():
        raise RuntimeError(
            "Encrypted result was not produced"
        )

    return {
        "job_id": job_id,
        "state": "ENCRYPTED_RESULT_READY",
        "result_is_ciphertext": True,
        "researcher_has_secret_key": False,
    }


def decrypt_average(
    job_id: str,
) -> dict:
    job = _job_dir(job_id)

    if not job.exists():
        raise FileNotFoundError(
            "HE job not found"
        )

    result_file = (
        job
        / "research_exchange"
        / "glucose_average.ct"
    )

    if not result_file.exists():
        raise ValueError(
            "Encrypted result is not ready"
        )

    output = _run(HOSPITAL_DECRYPT, job)

    match = re.search(
        r"Decrypted result:\s*"
        r"([-+]?[0-9]+(?:\.[0-9]+)?)",
        output,
    )

    if not match:
        raise RuntimeError(
            "Could not parse decrypted HE result"
        )

    average = float(match.group(1))

    return {
        "job_id": job_id,
        "state": "DECRYPTED",
        "metric": "fasting_glucose",
        "unit": "mg/dL",
        "average": average,
    }
