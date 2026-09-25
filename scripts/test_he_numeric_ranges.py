"""Synthetic CSV CKKS mean/sum accuracy across representative scales."""

import math
import subprocess
import tempfile
from pathlib import Path

from backend.he_service import (
    HOSPITAL_ENCRYPT, cleanup_he_job, compute_encrypted_average, create_encrypted_glucose_cohort,
    decrypt_average,
)
from backend.he_sum_service import compute_encrypted_sum, decrypt_sum
from backend.secure_temp import secure_plaintext_temp_root


cases = {
    "positive": [1.5, 2.0, 3.0, 4.5],
    "negative": [-4.5, -3.0, -2.0, -1.5],
    "mixed": [-4.0, -1.0, 2.0, 7.0],
    "small": [0.001, 0.002, 0.003, 0.004],
    "large": [100_000.0, 100_010.0, 100_020.0, 100_030.0],
}
for label, values in cases.items():
    job_id = None
    try:
        job_id = create_encrypted_glucose_cohort(values)["job_id"]
        compute_encrypted_average(job_id)
        compute_encrypted_sum(job_id)
        average = decrypt_average(job_id)
        summed = decrypt_sum(job_id)
        assert average["unit"] is None
        for result, actual, expected in (
            (average, average["average"], sum(values) / len(values)),
            (summed, summed["sum"], sum(values)),
        ):
            assert result["ckks_approximate"] is True
            assert "no fixed error bound" in result["accuracy_note"]
            assert math.isfinite(actual)
            tolerance = max(1e-6, abs(expected) * 1e-4)
            assert abs(actual - expected) <= tolerance, (label, actual, expected)
    finally:
        if job_id:
            cleanup_he_job(job_id)

print("CSV CKKS POSITIVE/NEGATIVE/MIXED/SMALL/LARGE MEAN AND SUM: PASS")

with tempfile.TemporaryDirectory(dir=secure_plaintext_temp_root()) as root:
    sample = Path(root) / "sample_input"
    sample.mkdir()
    sample.joinpath("glucose_values.csv").write_text("123456.789\n2.0\n")
    process = subprocess.run([str(HOSPITAL_ENCRYPT)], cwd=root,
                             capture_output=True, text=True, check=True)
    assert "123456.789" not in process.stdout + process.stderr
    assert "Patient" not in process.stdout + process.stderr
print("CSV HOSPITAL ENCRYPTION OUTPUT CONTAINS NO PLAINTEXT VALUES: PASS")
