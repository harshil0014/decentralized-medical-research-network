from __future__ import annotations

import csv
import hashlib
import io
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
            "Microsoft SEAL binaries are not built"
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
        message = (
            result.stderr.strip()
            or result.stdout.strip()
            or "Unknown HE process error"
        )

        raise RuntimeError(
            f"{binary.name} failed: {message}"
        )

    return result.stdout


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)

            if not chunk:
                break

            h.update(chunk)

    return h.hexdigest()


def _ciphertext_manifest_hash(
    exchange: Path,
) -> str:
    files = sorted(
        p
        for p in exchange.glob("glucose_*.ct")
        if p.name != "glucose_average.ct"
    )

    if not files:
        raise RuntimeError(
            "No encrypted cohort ciphertexts found"
        )

    manifest = hashlib.sha256()

    for path in files:
        digest = _sha256_file(path)

        manifest.update(path.name.encode("utf-8"))
        manifest.update(b":")
        manifest.update(digest.encode("ascii"))
        manifest.update(b"\n")

    return manifest.hexdigest()


def create_encrypted_glucose_cohort(
    values: list[float],
) -> dict:
    _check_binaries()

    if not 2 <= len(values) <= 1000:
        raise ValueError(
            "Cohort must contain between 2 and 1000 values"
        )

    clean_values: list[float] = []

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
        "".join(
            f"{value:.12g}\n"
            for value in clean_values
        )
    )

    _run(HOSPITAL_ENCRYPT, job)

    secret_key = (
        job
        / "hospital_private"
        / "secret.key"
    )

    exchange = job / "research_exchange"

    if not secret_key.exists():
        shutil.rmtree(
            job,
            ignore_errors=True,
        )

        raise RuntimeError(
            "Hospital secret key was not created"
        )

    if not exchange.exists():
        shutil.rmtree(
            job,
            ignore_errors=True,
        )

        raise RuntimeError(
            "Research exchange was not created"
        )

    if any(
        "secret" in p.name.lower()
        for p in exchange.iterdir()
    ):
        shutil.rmtree(
            job,
            ignore_errors=True,
        )

        raise RuntimeError(
            "Secret material leaked into researcher exchange"
        )

    manifest_sha256 = (
        _ciphertext_manifest_hash(exchange)
    )

    return {
        "job_id": job_id,
        "count": len(clean_values),
        "state": "ENCRYPTED",
        "ciphertext_manifest_sha256": manifest_sha256,
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

    if any(
        "secret" in p.name.lower()
        for p in exchange.iterdir()
    ):
        raise RuntimeError(
            "Secret material detected in researcher exchange"
        )

    _run(
        RESEARCHER_COMPUTE,
        job,
    )

    result = (
        exchange
        / "glucose_average.ct"
    )

    if not result.exists():
        raise RuntimeError(
            "Encrypted result was not produced"
        )

    result_sha256 = _sha256_file(result)

    return {
        "job_id": job_id,
        "state": "ENCRYPTED_RESULT_READY",
        "result_sha256": result_sha256,
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

    output = _run(
        HOSPITAL_DECRYPT,
        job,
    )

    match = re.search(
        r"Decrypted result:\s*"
        r"([-+]?[0-9]+(?:\.[0-9]+)?)",
        output,
    )

    if not match:
        raise RuntimeError(
            "Could not parse decrypted HE result"
        )

    average = float(
        match.group(1)
    )

    return {
        "job_id": job_id,
        "state": "DECRYPTED",
        "metric": "fasting_glucose",
        "unit": "mg/dL",
        "average": average,
    }


def cleanup_he_job(job_id: str) -> None:
    job = _job_dir(job_id)

    shutil.rmtree(
        job,
        ignore_errors=True,
    )


def discard_encrypted_result(
    job_id: str,
) -> None:
    job = _job_dir(job_id)

    result = (
        job
        / "research_exchange"
        / "glucose_average.ct"
    )

    if result.exists():
        result.unlink()


def get_ciphertext_manifest_sha256(
    job_id: str,
) -> str:
    job = _job_dir(job_id)

    if not job.exists():
        raise FileNotFoundError(
            "HE job not found"
        )

    exchange = (
        job
        / "research_exchange"
    )

    if not exchange.exists():
        raise RuntimeError(
            "Research exchange is missing"
        )

    return _ciphertext_manifest_hash(
        exchange
    )


def get_encrypted_result_sha256(
    job_id: str,
) -> str:
    job = _job_dir(job_id)

    if not job.exists():
        raise FileNotFoundError(
            "HE job not found"
        )

    result = (
        job
        / "research_exchange"
        / "glucose_average.ct"
    )

    if not result.exists():
        raise FileNotFoundError(
            "Encrypted HE result not found"
        )

    return _sha256_file(result)


def fetch_ipfs_dataset_bytes(
    cid: str,
) -> bytes:
    if not cid or not cid.strip():
        raise ValueError("Dataset CID is required")

    result = subprocess.run(
        [
            "docker",
            "exec",
            "medical-ipfs",
            "ipfs",
            "cat",
            cid,
        ],
        capture_output=True,
        timeout=60,
    )

    if result.returncode != 0:
        message = (
            result.stderr.decode(
                "utf-8",
                errors="replace",
            ).strip()
            or "IPFS retrieval failed"
        )

        raise RuntimeError(message)

    return result.stdout


def verify_dataset_bytes(
    data: bytes,
    expected_sha256: str,
) -> str:
    actual = hashlib.sha256(data).hexdigest()

    if actual.lower() != expected_sha256.lower():
        raise RuntimeError(
            "Dataset integrity verification failed"
        )

    return actual


def extract_numeric_metric_from_csv(
    data: bytes,
    metric: str,
) -> list[float]:
    if not metric or not metric.strip():
        raise ValueError("Metric is required")

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(
            "Dataset must be a UTF-8 CSV file"
        ) from exc

    reader = csv.DictReader(
        io.StringIO(text)
    )

    if not reader.fieldnames:
        raise ValueError(
            "CSV dataset has no header"
        )

    normalized = {
        name.strip(): name
        for name in reader.fieldnames
        if name is not None
    }

    if metric not in normalized:
        raise ValueError(
            f"Metric column '{metric}' not found in dataset"
        )

    original_column = normalized[metric]

    values: list[float] = []

    for row_number, row in enumerate(
        reader,
        start=2,
    ):
        raw = row.get(original_column)

        if raw is None:
            continue

        raw = raw.strip()

        if raw == "":
            continue

        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError(
                f"Invalid numeric value in row {row_number}"
            ) from exc

        if not math.isfinite(value):
            raise ValueError(
                f"Non-finite value in row {row_number}"
            )

        values.append(value)

    if len(values) < 2:
        raise ValueError(
            "Metric must contain at least 2 numeric values"
        )

    if len(values) > 1000:
        raise ValueError(
            "Metric contains more than 1000 values"
        )

    return values
