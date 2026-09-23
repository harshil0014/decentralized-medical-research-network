from __future__ import annotations

import io
import math
import zipfile

import numpy as np
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import (
    CTImageStorage,
    ExplicitVRLittleEndian,
    MRImageStorage,
    generate_uid,
)

from backend.dicom_he_service import (
    cleanup_dicom_he_job,
    compute_encrypted_dicom_analysis,
    create_encrypted_dicom_job,
    decrypt_dicom_analysis,
    extract_dicom_analysis_values,
)


def make_slice(
    *,
    modality: str,
    pixels: np.ndarray,
    series_uid: str,
    study_uid: str,
    instance: int,
    slope: float = 1.0,
    intercept: float = 0.0,
    burned_in: str = "NO",
) -> bytes:
    sop_class = CTImageStorage if modality == "CT" else MRImageStorage
    meta = Dataset()
    meta.MediaStorageSOPClassUID = sop_class
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.ImplementationClassUID = generate_uid()

    stream = io.BytesIO()
    ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID = sop_class
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.Modality = modality
    ds.InstanceNumber = instance
    ds.ImagePositionPatient = [0.0, 0.0, float(instance)]
    ds.Rows, ds.Columns = map(int, pixels.shape)
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 1
    ds.BurnedInAnnotation = burned_in
    if modality == "CT":
        ds.RescaleSlope = str(slope)
        ds.RescaleIntercept = str(intercept)
    ds.PixelData = np.asarray(pixels, dtype=np.int16).tobytes()
    ds.save_as(stream, enforce_file_format=True)
    return stream.getvalue()


def make_series_zip(
    modality: str,
    slices: list[np.ndarray],
    *,
    slope: float = 1.0,
    intercept: float = 0.0,
) -> bytes:
    study_uid = generate_uid()
    series_uid = generate_uid()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, pixels in enumerate(slices, start=1):
            archive.writestr(
                f"nested/slice_{index:04d}.dcm",
                make_slice(
                    modality=modality,
                    pixels=pixels,
                    study_uid=study_uid,
                    series_uid=series_uid,
                    instance=index,
                    slope=slope,
                    intercept=intercept,
                ),
            )
        archive.writestr("README.txt", b"non-DICOM helper")
    return output.getvalue()


def assert_close(actual: float, expected: float, label: str) -> None:
    tolerance = max(1e-4, abs(expected) * 2e-6)
    if not math.isclose(actual, expected, rel_tol=2e-6, abs_tol=tolerance):
        raise AssertionError(f"{label}: got {actual}, expected {expected}")


def verify_he(values: np.ndarray, metadata: dict) -> None:
    expected = {
        "SUM": float(values.sum()),
        "MEAN": float(values.mean()),
        "ENERGY": float(np.square(values).sum()),
        "SECOND_MOMENT": float(np.square(values).mean()),
        "VARIANCE": float(values.var()),
    }

    for analysis, reference in expected.items():
        job_id = None
        try:
            created = create_encrypted_dicom_job(values, analysis, metadata)
            job_id = created["job_id"]
            assert created["he_mode"] == "RAW_VOXELS"
            assert created["representation"] == "ENCRYPTED_RAW_VOXELS"
            assert created["researcher_has_plaintext_pixels"] is False
            assert created["researcher_has_raw_pixels"] is False
            assert created["researcher_has_encrypted_raw_voxels"] is True
            assert created["researcher_has_secret_key"] is False

            computed = compute_encrypted_dicom_analysis(job_id)
            assert computed["result_is_ciphertext"] is True
            assert computed["researcher_has_plaintext_pixels"] is False
            assert computed["researcher_has_raw_pixels"] is False
            assert computed["researcher_has_encrypted_raw_voxels"] is True
            assert computed["researcher_has_secret_key"] is False

            decrypted = decrypt_dicom_analysis(job_id)
            assert_close(float(decrypted["value"]), reference, analysis)
            print(
                f"{analysis}: PASS "
                f"({decrypted['value']:.6f} {decrypted['unit']})"
            )
        finally:
            if job_id:
                cleanup_dicom_he_job(job_id)


def test_ct_series() -> None:
    raw = [
        np.array([[0, 1], [2, 3]], dtype=np.int16),
        np.array([[4, 5], [6, 7]], dtype=np.int16),
        np.array([[8, 9], [10, 11]], dtype=np.int16),
    ]
    data = make_series_zip("CT", raw, slope=2.0, intercept=-1000.0)
    values, metadata = extract_dicom_analysis_values(data)
    expected = np.concatenate(
        [(x.astype(np.float64) * 2.0 - 1000.0).reshape(-1) for x in raw]
    )
    if not np.array_equal(values, expected):
        raise AssertionError("CT modality transformation failed")
    assert metadata["modality"] == "CT"
    assert metadata["unit"] == "HU"
    assert metadata["original_shape"] == [3, 2, 2]

    slice_values, slice_meta = extract_dicom_analysis_values(
        data,
        scope="SLICE",
        slice_index=1,
    )
    assert np.array_equal(slice_values, expected[4:8])
    assert slice_meta["slice_index"] == 1

    roi = {
        "slice_start": 1,
        "slice_end": 3,
        "row_start": 0,
        "row_end": 2,
        "col_start": 1,
        "col_end": 2,
    }
    roi_values, roi_meta = extract_dicom_analysis_values(
        data,
        scope="ROI_BOX",
        roi_box=roi,
    )
    expected_roi = np.array([-990.0, -986.0, -982.0, -978.0])
    assert np.array_equal(roi_values, expected_roi)
    assert roi_meta["roi_box"] == roi
    assert roi_meta["selected_shape"] == [2, 2, 1]

    print("CT SERIES + SLICE + ROI EXTRACTION: PASS")
    verify_he(values, metadata)


def test_mr_series() -> None:
    raw = [
        np.array([[10, 20], [30, 40]], dtype=np.int16),
        np.array([[50, 60], [70, 80]], dtype=np.int16),
    ]
    data = make_series_zip("MR", raw)
    values, metadata = extract_dicom_analysis_values(data)
    expected = np.concatenate([x.astype(np.float64).reshape(-1) for x in raw])
    if not np.array_equal(values, expected):
        raise AssertionError("MR intensity extraction failed")
    assert metadata["modality"] == "MR"
    assert metadata["unit"] == "relative_intensity"
    print("MR SERIES EXTRACTION: PASS")


def test_privacy_guards() -> None:
    study_uid = generate_uid()
    series_uid = generate_uid()
    burned = make_slice(
        modality="CT",
        pixels=np.array([[1, 2], [3, 4]], dtype=np.int16),
        study_uid=study_uid,
        series_uid=series_uid,
        instance=1,
        burned_in="YES",
    )
    try:
        extract_dicom_analysis_values(burned)
    except ValueError as exc:
        if "burned-in" not in str(exc):
            raise
    else:
        raise AssertionError("Burned-in annotation was not rejected")
    print("BURNED-IN ANNOTATION GUARD: PASS")


def test_raw_multichunk_and_block_fallback() -> None:
    values = np.linspace(-1000.0, 1000.0, 5000, dtype=np.float64)
    metadata = {
        "modality": "CT",
        "unit": "HU",
        "scope": "ROI_BOX",
        "slice_index": None,
        "roi_box": {
            "slice_start": 0,
            "slice_end": 1,
            "row_start": 0,
            "row_end": 50,
            "col_start": 0,
            "col_end": 100,
        },
        "original_shape": [1, 50, 100],
        "selected_shape": [1, 50, 100],
        "voxel_count": 5000,
        "dicom_loader": "synthetic",
    }

    raw_job = None
    block_job = None
    try:
        created = create_encrypted_dicom_job(
            values,
            "MEAN",
            metadata,
            he_mode="RAW_VOXELS",
        )
        raw_job = created["job_id"]
        assert created["chunk_count"] == 2
        assert created["researcher_has_encrypted_raw_voxels"] is True
        compute_encrypted_dicom_analysis(raw_job)
        decrypted = decrypt_dicom_analysis(raw_job)
        assert_close(float(decrypted["value"]), float(values.mean()), "RAW_MULTI_CHUNK")
        print("RAW VOXEL MULTI-CHUNK: PASS")

        created_block = create_encrypted_dicom_job(
            values,
            "MEAN",
            metadata,
            he_mode="BLOCK_STATS",
        )
        block_job = created_block["job_id"]
        assert created_block["representation"] == "ENCRYPTED_BLOCK_SUFFICIENT_STATISTICS"
        assert created_block["researcher_has_encrypted_raw_voxels"] is False
        compute_encrypted_dicom_analysis(block_job)
        decrypted_block = decrypt_dicom_analysis(block_job)
        assert_close(
            float(decrypted_block["value"]),
            float(values.mean()),
            "BLOCK_STATS_FALLBACK",
        )
        print("BLOCK STATS FALLBACK: PASS")
    finally:
        if raw_job:
            cleanup_dicom_he_job(raw_job)
        if block_job:
            cleanup_dicom_he_job(block_job)


def main() -> None:
    test_ct_series()
    test_mr_series()
    test_privacy_guards()
    test_raw_multichunk_and_block_fallback()
    print("DICOM HE STATISTICS: PASS")


if __name__ == "__main__":
    main()
