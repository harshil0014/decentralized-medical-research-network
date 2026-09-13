from __future__ import annotations

import csv
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "seal_demo" / "build"

HOSPITAL_ENCRYPT = BUILD / "hospital_encrypt"
RESEARCHER_COMPUTE = BUILD / "researcher_compute"
HOSPITAL_DECRYPT = BUILD / "hospital_decrypt"

COHORT_SIZES = [5, 25, 100]


def run(binary: Path, cwd: Path):
    start = time.perf_counter()

    result = subprocess.run(
        [str(binary)],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )

    elapsed = time.perf_counter() - start

    return elapsed, result.stdout


def parse_decrypt(output: str):
    result_match = re.search(
        r"Decrypted result:\s*([-+]?[0-9.]+)",
        output,
    )

    error_match = re.search(
        r"CKKS error:\s*([-+]?[0-9.eE-]+)",
        output,
    )

    if not result_match or not error_match:
        raise RuntimeError(
            "Could not parse hospital decrypt output"
        )

    return (
        float(result_match.group(1)),
        float(error_match.group(1)),
    )


rows = []

print("=== MICROSOFT SEAL CKKS BENCHMARK ===")
print()

for cohort_size in COHORT_SIZES:

    # Deterministic synthetic glucose cohort
    values = [
        85.0 + ((i * 7) % 45) + (i % 4) * 0.25
        for i in range(cohort_size)
    ]

    with tempfile.TemporaryDirectory(
        prefix="seal-benchmark-"
    ) as tmp:

        job = Path(tmp)

        sample = job / "sample_input"
        sample.mkdir()

        csv_path = (
            sample
            / "glucose_values.csv"
        )

        csv_path.write_text(
            "".join(
                f"{value}\n"
                for value in values
            )
        )

        encryption_time, _ = run(
            HOSPITAL_ENCRYPT,
            job,
        )

        exchange = (
            job
            / "research_exchange"
        )

        input_ciphertexts = sorted(
            p
            for p in exchange.glob(
                "glucose_*.ct"
            )
            if p.name
            != "glucose_average.ct"
        )

        input_ciphertext_bytes = sum(
            p.stat().st_size
            for p in input_ciphertexts
        )

        computation_time, _ = run(
            RESEARCHER_COMPUTE,
            job,
        )

        result_ciphertext = (
            exchange
            / "glucose_average.ct"
        )

        result_ciphertext_bytes = (
            result_ciphertext.stat().st_size
        )

        decryption_time, decrypt_output = run(
            HOSPITAL_DECRYPT,
            job,
        )

        decrypted_result, ckks_error = (
            parse_decrypt(
                decrypt_output
            )
        )

        expected = (
            sum(values)
            / len(values)
        )

        row = {
            "cohort_size": cohort_size,
            "encryption_seconds": encryption_time,
            "computation_seconds": computation_time,
            "decryption_seconds": decryption_time,
            "total_seconds":
                encryption_time
                + computation_time
                + decryption_time,
            "input_ciphertext_bytes":
                input_ciphertext_bytes,
            "result_ciphertext_bytes":
                result_ciphertext_bytes,
            "expected_average":
                expected,
            "decrypted_average":
                decrypted_result,
            "ckks_error":
                abs(
                    decrypted_result
                    - expected
                ),
        }

        rows.append(row)

        print(
            f"Cohort {cohort_size}: "
            f"encrypt={encryption_time:.4f}s | "
            f"compute={computation_time:.4f}s | "
            f"decrypt={decryption_time:.4f}s | "
            f"input ciphertext="
            f"{input_ciphertext_bytes / 1024 / 1024:.2f} MB | "
            f"result ciphertext="
            f"{result_ciphertext_bytes / 1024:.2f} KB | "
            f"error={row['ckks_error']:.10f}"
        )


output = ROOT / "benchmarks" / "seal_ckks_benchmark.csv"

with output.open(
    "w",
    newline="",
) as f:

    writer = csv.DictWriter(
        f,
        fieldnames=rows[0].keys(),
    )

    writer.writeheader()
    writer.writerows(rows)


print()
print("BENCHMARK CSV:", output)
print("SEAL CKKS BENCHMARK: PASS")
