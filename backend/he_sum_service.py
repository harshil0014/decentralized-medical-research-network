from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

from backend.he_service import (
    RUNTIME_ROOT,
    _ipfs_add_file,
    _ipfs_cat_artifact,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = PROJECT_ROOT / "seal_demo" / "build"
RESEARCHER_SUM = BUILD_DIR / "researcher_sum"
HOSPITAL_DECRYPT_SUM = BUILD_DIR / "hospital_decrypt_sum"
JOB_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def _job_dir(job_id: str) -> Path:
    if not JOB_PATTERN.fullmatch(job_id):
        raise ValueError("Invalid HE job ID")
    return RUNTIME_ROOT / job_id


def _run(binary: Path, cwd: Path) -> str:
    if not binary.exists():
        raise RuntimeError(f"Microsoft SEAL binary is not built: {binary.name}")

    result = subprocess.run(
        [str(binary)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Unknown HE process error"
        raise RuntimeError(f"{binary.name} failed: {message}")

    return result.stdout


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def compute_encrypted_sum(job_id: str) -> dict:
    job = _job_dir(job_id)
    exchange = job / "research_exchange"

    if not exchange.exists():
        raise RuntimeError("Research exchange is missing")

    if any("secret" in path.name.lower() for path in exchange.iterdir()):
        raise RuntimeError("Secret material detected in researcher exchange")

    _run(RESEARCHER_SUM, job)

    result = exchange / "glucose_sum.ct"
    if not result.exists():
        raise RuntimeError("Encrypted sum result was not produced")

    return {
        "job_id": job_id,
        "operation": "SUM",
        "state": "ENCRYPTED_RESULT_READY",
        "result_sha256": _sha256_file(result),
        "result_is_ciphertext": True,
        "researcher_has_secret_key": False,
    }


def publish_encrypted_sum_to_ipfs(job_id: str) -> dict:
    job = _job_dir(job_id)
    result = job / "research_exchange" / "glucose_sum.ct"

    if not result.exists():
        raise FileNotFoundError("Encrypted HE sum result not found")

    return {
        "cid": _ipfs_add_file(result),
        "sha256": _sha256_file(result),
    }


def restore_encrypted_sum_from_ipfs(
    job_id: str,
    cid: str,
    expected_sha256: str,
) -> str:
    job = _job_dir(job_id)
    exchange = job / "research_exchange"
    exchange.mkdir(parents=True, exist_ok=True)

    data = _ipfs_cat_artifact(cid)
    actual = hashlib.sha256(data).hexdigest()
    if actual.lower() != expected_sha256.lower():
        raise RuntimeError("Encrypted sum SHA256 does not match Ethereum ledger")

    (exchange / "glucose_sum.ct").write_bytes(data)
    return actual


def discard_encrypted_sum(job_id: str) -> None:
    result = _job_dir(job_id) / "research_exchange" / "glucose_sum.ct"
    if result.exists():
        result.unlink()


def decrypt_sum(job_id: str) -> dict:
    job = _job_dir(job_id)
    result_file = job / "research_exchange" / "glucose_sum.ct"

    if not result_file.exists():
        raise ValueError("Encrypted sum result is not ready")

    output = _run(HOSPITAL_DECRYPT_SUM, job)
    match = re.search(
        r"Decrypted result:\s*([-+]?[0-9]+(?:\.[0-9]+)?)",
        output,
    )

    if not match:
        raise RuntimeError("Could not parse decrypted HE sum")

    return {
        "job_id": job_id,
        "operation": "SUM",
        "state": "DECRYPTED",
        "sum": float(match.group(1)),
    }
