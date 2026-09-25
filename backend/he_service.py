from __future__ import annotations
import io
import zipfile

import csv
import hashlib
import math
import re
import shutil
import subprocess
import uuid
from pathlib import Path, PurePosixPath

from backend.secure_temp import secure_plaintext_temp_root
from backend.ipfs_storage import (
    add_file as ipfs_add_file,
    cat as ipfs_cat,
    unpin as ipfs_unpin,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SEAL_DEMO = PROJECT_ROOT / "seal_demo"
BUILD_DIR = SEAL_DEMO / "build"

HOSPITAL_ENCRYPT = BUILD_DIR / "hospital_encrypt"
RESEARCHER_COMPUTE = BUILD_DIR / "researcher_compute"
HOSPITAL_DECRYPT = BUILD_DIR / "hospital_decrypt"

RUNTIME_ROOT = secure_plaintext_temp_root() / "medical-he-jobs"
RUNTIME_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
try:
    RUNTIME_ROOT.chmod(0o700)
except OSError:
    pass

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

    # Plaintext staging is no longer needed after encryption.
    # Keep only the hospital secret material and encrypted artifacts.
    shutil.rmtree(
        sample_dir,
        ignore_errors=True,
    )

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
        r"([-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?)",
        output,
    )

    if not match:
        raise RuntimeError(
            "Could not parse decrypted HE result"
        )

    average = float(
        match.group(1)
    )
    if not math.isfinite(average):
        raise RuntimeError("CKKS average is not finite")

    return {
        "job_id": job_id,
        "state": "DECRYPTED",
        "metric": "selected_numeric_metric",
        "unit": None,
        "unit_note": "Interpret the result using the selected source CSV column's units",
        "average": average,
        "ckks_approximate": True,
        "accuracy_note": "Approximate CKKS result; no fixed error bound",
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
    return ipfs_cat(cid, timeout=180)

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



def _ipfs_add_file(
    path: Path,
) -> str:
    return ipfs_add_file(path)

def _ipfs_cat_artifact(
    cid: str,
) -> bytes:
    return ipfs_cat(cid, timeout=180)

def unpin_ipfs(
    cid: str,
) -> None:
    ipfs_unpin(cid)

def remove_research_exchange(
    job_id: str,
) -> None:
    job = _job_dir(job_id)

    shutil.rmtree(
        job / "research_exchange",
        ignore_errors=True,
    )


def publish_ciphertext_bundle_to_ipfs(
    job_id: str,
) -> str:
    job = _job_dir(job_id)
    exchange = job / "research_exchange"

    if not exchange.exists():
        raise FileNotFoundError(
            "Research exchange is missing"
        )

    files = []

    for path in sorted(exchange.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(
                "Symlinks are not allowed in HE exchange"
            )

        if not path.is_file():
            continue

        relative = path.relative_to(
            exchange
        ).as_posix()

        if "secret" in relative.lower():
            raise RuntimeError(
                "Secret material detected in researcher exchange"
            )

        if path.name == "glucose_average.ct":
            continue

        files.append(path)

    if not files:
        raise RuntimeError(
            "No HE researcher artifacts available to publish"
        )

    bundle = (
        job
        / f".{job_id}.ciphertext_bundle.zip"
    )

    try:
        with zipfile.ZipFile(
            bundle,
            "w",
            compression=zipfile.ZIP_STORED,
        ) as archive:
            for path in files:
                archive.write(
                    path,
                    arcname=path.relative_to(
                        exchange
                    ).as_posix(),
                )

        return _ipfs_add_file(bundle)

    finally:
        try:
            bundle.unlink()
        except FileNotFoundError:
            pass


def restore_ciphertext_bundle_from_ipfs(
    job_id: str,
    cid: str,
    expected_manifest_sha256: str,
) -> str:
    job = _job_dir(job_id)

    job.mkdir(
        parents=True,
        exist_ok=True,
    )

    exchange = (
        job
        / "research_exchange"
    )

    shutil.rmtree(
        exchange,
        ignore_errors=True,
    )

    exchange.mkdir(
        parents=True,
        exist_ok=True,
    )

    data = _ipfs_cat_artifact(cid)

    # Prototype safety limit against oversized/bomb archives.
    if len(data) > 512 * 1024 * 1024:
        shutil.rmtree(
            exchange,
            ignore_errors=True,
        )

        raise RuntimeError(
            "HE ciphertext bundle is too large"
        )

    try:
        with zipfile.ZipFile(
            io.BytesIO(data),
            "r",
        ) as archive:

            infos = [
                info
                for info in archive.infolist()
                if not info.is_dir()
            ]

            if not infos or len(infos) > 5000:
                raise RuntimeError(
                    "Invalid HE ciphertext bundle"
                )

            total_size = sum(
                info.file_size
                for info in infos
            )

            if total_size > 512 * 1024 * 1024:
                raise RuntimeError(
                    "HE ciphertext bundle expands too large"
                )

            for info in infos:
                relative = PurePosixPath(
                    info.filename
                )

                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                ):
                    raise RuntimeError(
                        "Unsafe HE bundle path"
                    )

                if "secret" in info.filename.lower():
                    raise RuntimeError(
                        "Secret material detected in HE bundle"
                    )

                target = exchange.joinpath(
                    *relative.parts
                )

                target.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                with archive.open(
                    info,
                    "r",
                ) as src:
                    with target.open(
                        "wb",
                    ) as dst:
                        shutil.copyfileobj(
                            src,
                            dst,
                        )

        actual_manifest = (
            _ciphertext_manifest_hash(
                exchange
            )
        )

        if (
            actual_manifest.lower()
            != expected_manifest_sha256.lower()
        ):
            raise RuntimeError(
                "Ciphertext manifest does not match Ethereum commitment"
            )

        return actual_manifest

    except Exception:
        shutil.rmtree(
            exchange,
            ignore_errors=True,
        )
        raise


def publish_encrypted_result_to_ipfs(
    job_id: str,
) -> dict:
    job = _job_dir(job_id)

    result = (
        job
        / "research_exchange"
        / "glucose_average.ct"
    )

    if not result.exists():
        raise FileNotFoundError(
            "Encrypted HE result not found"
        )

    digest = _sha256_file(result)
    cid = _ipfs_add_file(result)

    return {
        "cid": cid,
        "sha256": digest,
    }


def restore_encrypted_result_from_ipfs(
    job_id: str,
    cid: str,
    expected_sha256: str,
) -> str:
    job = _job_dir(job_id)

    exchange = (
        job
        / "research_exchange"
    )

    exchange.mkdir(
        parents=True,
        exist_ok=True,
    )

    data = _ipfs_cat_artifact(cid)

    actual = hashlib.sha256(
        data
    ).hexdigest()

    if (
        actual.lower()
        != expected_sha256.lower()
    ):
        raise RuntimeError(
            "Encrypted result SHA256 does not match Ethereum commitment"
        )

    result = (
        exchange
        / "glucose_average.ct"
    )

    result.write_bytes(data)

    return actual
