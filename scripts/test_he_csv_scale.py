"""Synthetic CSV cohort boundary regression for the single-ciphertext demo path."""

from backend.he_service import extract_numeric_metric_from_csv


def csv_with_rows(count: int) -> bytes:
    return ("synthetic_metric\n" + "".join(f"{index / 10}\n" for index in range(count))).encode()


values = extract_numeric_metric_from_csv(csv_with_rows(1000), "synthetic_metric")
assert len(values) == 1000
assert values[0] == 0.0 and values[-1] == 99.9

try:
    extract_numeric_metric_from_csv(csv_with_rows(1001), "synthetic_metric")
except ValueError as exc:
    assert "more than 1000" in str(exc)
else:
    raise AssertionError("CSV cohort limit was not enforced")

print("SYNTHETIC 1000-ROW CSV COHORT BOUNDARY: PASS")
