from __future__ import annotations

import io
import math
import zipfile

import numpy as np
import pydicom
from highdicom.seg import SegmentDescription, Segmentation
from highdicom.seg.enum import SegmentAlgorithmTypeValues, SegmentationTypeValues
from pydicom.dataset import Dataset, FileDataset
from pydicom.sr.coding import Code
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
    extract_dicom_seg_analysis_values,
    list_dicom_seg_segments,
)
from backend.dicom_utils import deidentify_dataset


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


def make_dicom_seg_fixture() -> tuple[bytes, bytes, np.ndarray, str]:
    study_uid = generate_uid()
    series_uid = generate_uid()
    frame_uid = generate_uid()
    raw = make_slice(
        modality="CT",
        pixels=np.arange(16, dtype=np.int16).reshape(4, 4),
        series_uid=series_uid,
        study_uid=study_uid,
        instance=1,
        slope=1.0,
        intercept=-1024.0,
    )
    source = pydicom.dcmread(io.BytesIO(raw))
    source.PatientID = "SEG-TEST"
    source.PatientName = "SEG^TEST"
    source.PatientBirthDate = "20000101"
    source.PatientSex = "O"
    source.StudyDate = "20260101"
    source.StudyTime = "120000"
    source.AccessionNumber = "SEGTEST"
    source.StudyID = "1"
    source.SeriesNumber = 1
    source.FrameOfReferenceUID = frame_uid
    source.PixelSpacing = [1.0, 1.0]
    source.SliceThickness = "1"
    source.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    source.ImageType = ["ORIGINAL", "PRIMARY", "AXIAL"]

    description = SegmentDescription(
        segment_number=1,
        segment_label="Test ROI",
        segmented_property_category=Code(
            "123037004", "SCT", "Anatomical Structure"
        ),
        segmented_property_type=Code(
            "91723000", "SCT", "Anatomical structure"
        ),
        algorithm_type=SegmentAlgorithmTypeValues.MANUAL,
    )
    mask = np.zeros((1, 4, 4), dtype=np.uint8)
    mask[0, :2, :2] = 1
    segmentation = Segmentation(
        source_images=[source],
        pixel_array=mask,
        segmentation_type=SegmentationTypeValues.BINARY,
        segment_descriptions=[description],
        series_instance_uid=generate_uid(),
        series_number=99,
        sop_instance_uid=generate_uid(),
        instance_number=1,
        manufacturer="OpenAI Test",
        manufacturer_model_name="Synthetic",
        software_versions="1",
        device_serial_number="1",
    )

    original_source_sop = str(source.SOPInstanceUID)
    source_clean = source.copy()
    seg_clean = segmentation.copy()
    deidentify_dataset(source_clean, uid_map={})
    deidentify_dataset(seg_clean, uid_map={})

    source_stream = io.BytesIO()
    source_clean.save_as(source_stream, enforce_file_format=True)
    seg_stream = io.BytesIO()
    seg_clean.save_as(seg_stream, enforce_file_format=True)

    expected = np.array([-1024.0, -1023.0, -1020.0, -1019.0])
    return source_stream.getvalue(), seg_stream.getvalue(), expected, original_source_sop


def make_multislice_dicom_seg_fixture() -> tuple[bytes, bytes, np.ndarray]:
    study_uid = generate_uid()
    series_uid = generate_uid()
    frame_uid = generate_uid()
    sources = []

    for index in range(3):
        raw = make_slice(
            modality="CT",
            pixels=(
                np.arange(16, dtype=np.int16).reshape(4, 4)
                + index * 16
            ),
            series_uid=series_uid,
            study_uid=study_uid,
            instance=index + 1,
            slope=1.0,
            intercept=-1024.0,
        )
        source = pydicom.dcmread(io.BytesIO(raw))
        source.PatientID = "SEG-MULTI"
        source.PatientName = "SEG^MULTI"
        source.PatientBirthDate = "20000101"
        source.PatientSex = "O"
        source.StudyDate = "20260101"
        source.StudyTime = "120000"
        source.AccessionNumber = "SEG-MULTI"
        source.StudyID = "2"
        source.SeriesNumber = 2
        source.FrameOfReferenceUID = frame_uid
        source.PixelSpacing = [1.0, 1.0]
        source.SliceThickness = "1"
        source.SpacingBetweenSlices = "1"
        source.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        source.ImagePositionPatient = [0.0, 0.0, float(index)]
        source.ImageType = ["ORIGINAL", "PRIMARY", "AXIAL"]
        sources.append(source)

    description = SegmentDescription(
        segment_number=1,
        segment_label="Multi Slice ROI",
        segmented_property_category=Code(
            "123037004", "SCT", "Anatomical Structure"
        ),
        segmented_property_type=Code(
            "91723000", "SCT", "Anatomical structure"
        ),
        algorithm_type=SegmentAlgorithmTypeValues.MANUAL,
    )

    mask = np.zeros((3, 4, 4), dtype=np.uint8)
    mask[:, 0, 0] = 1
    mask[:, 1, 1] = 1

    segmentation = Segmentation(
        source_images=sources,
        pixel_array=mask,
        segmentation_type=SegmentationTypeValues.BINARY,
        segment_descriptions=[description],
        series_instance_uid=generate_uid(),
        series_number=100,
        sop_instance_uid=generate_uid(),
        instance_number=1,
        manufacturer="OpenAI Test",
        manufacturer_model_name="Synthetic",
        software_versions="1",
        device_serial_number="1",
    )

    # Match the real upload path: one shared UID map inside the CT
    # series, but an independent map for the separately uploaded SEG.
    source_uid_map: dict[str, str] = {}
    source_zip = io.BytesIO()
    with zipfile.ZipFile(source_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, source in enumerate(sources, start=1):
            cleaned = source.copy()
            deidentify_dataset(cleaned, uid_map=source_uid_map)
            stream = io.BytesIO()
            cleaned.save_as(stream, enforce_file_format=True)
            archive.writestr(f"slice_{index:04d}.dcm", stream.getvalue())

    seg_clean = segmentation.copy()
    deidentify_dataset(seg_clean, uid_map={})
    seg_stream = io.BytesIO()
    seg_clean.save_as(seg_stream, enforce_file_format=True)

    expected = np.array(
        [-1024.0, -1019.0, -1008.0, -1003.0, -992.0, -987.0]
    )
    return source_zip.getvalue(), seg_stream.getvalue(), expected


def assert_close(actual: float, expected: float, label: str) -> None:
    if label in {
        "SKEWNESS",
        "KURTOSIS",
        "CENTRAL_MOMENT_3",
        "CENTRAL_MOMENT_4",
    }:
        tolerance = max(5e-3, abs(expected) * 5e-5)
        relative = 5e-5
    else:
        tolerance = max(1e-4, abs(expected) * 5e-6)
        relative = 5e-6
    if not math.isclose(actual, expected, rel_tol=relative, abs_tol=tolerance):
        raise AssertionError(f"{label}: got {actual}, expected {expected}")


def verify_he(values: np.ndarray, metadata: dict) -> None:
    mean = float(values.mean())
    centered = values.astype(np.float64) - mean
    variance = float(np.mean(centered ** 2))
    m3 = float(np.mean(centered ** 3))
    m4 = float(np.mean(centered ** 4))
    energy = float(np.square(values).sum())
    voxel_volume = float(metadata.get("voxel_volume_mm3", 1.0))
    expected = {
        "SUM": float(values.sum()),
        "MEAN": mean,
        "ENERGY": energy,
        "TOTAL_ENERGY": energy * voxel_volume,
        "SECOND_MOMENT": float(np.square(values).mean()),
        "ROOT_MEAN_SQUARED": float(np.sqrt(np.square(values).mean())),
        "VARIANCE": variance,
        "STANDARD_DEVIATION": float(np.sqrt(variance)),
        "CENTRAL_MOMENT_3": m3,
        "CENTRAL_MOMENT_4": m4,
        "SKEWNESS": 0.0 if variance == 0 else m3 / (variance ** 1.5),
        "KURTOSIS": 0.0 if variance == 0 else m4 / (variance ** 2),
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


def test_dicom_seg_roi() -> None:
    source_bytes, seg_bytes, expected, original_source_sop = make_dicom_seg_fixture()

    cleaned_seg = pydicom.dcmread(io.BytesIO(seg_bytes))
    ui_values = {
        str(value)
        for element in cleaned_seg.iterall()
        if element.VR == "UI"
        for value in (
            element.value if isinstance(element.value, (list, tuple)) else [element.value]
        )
    }
    assert original_source_sop not in ui_values

    catalog = list_dicom_seg_segments(seg_bytes)
    assert catalog["segmentation_type"] == "BINARY"
    assert catalog["segment_count"] == 1
    assert catalog["segments"][0]["segment_number"] == 1
    assert catalog["segments"][0]["segment_label"] == "Test ROI"

    values, metadata = extract_dicom_seg_analysis_values(
        source_bytes,
        seg_bytes,
        1,
    )
    assert np.array_equal(values, expected)
    assert metadata["scope"] == "DICOM_SEG"
    assert metadata["segment_number"] == 1
    assert metadata["segment_label"] == "Test ROI"
    assert metadata["segment_voxel_count"] == 4

    job_id = None
    try:
        created = create_encrypted_dicom_job(
            values,
            "MEAN",
            metadata,
            he_mode="RAW_VOXELS",
        )
        job_id = created["job_id"]
        assert created["representation"] == "ENCRYPTED_RAW_VOXELS"
        computed = compute_encrypted_dicom_analysis(job_id)
        assert computed["result_is_ciphertext"] is True
        decrypted = decrypt_dicom_analysis(job_id)
        assert_close(
            float(decrypted["value"]),
            float(expected.mean()),
            "DICOM_SEG_MEAN",
        )
    finally:
        if job_id:
            cleanup_dicom_he_job(job_id)

    print("DICOM SEG RAW-VOXEL ROI: PASS")


def test_multislice_dicom_seg_roi() -> None:
    source_zip, seg_bytes, expected = make_multislice_dicom_seg_fixture()

    catalog = list_dicom_seg_segments(seg_bytes)
    assert catalog["segment_count"] == 1
    assert catalog["segments"][0]["segment_label"] == "Multi Slice ROI"

    values, metadata = extract_dicom_seg_analysis_values(
        source_zip,
        seg_bytes,
        1,
    )
    # highdicom may store the volume with the slice axis reversed;
    # ROI statistics are order-independent, so compare the selected set.
    assert np.array_equal(np.sort(values), np.sort(expected))
    assert metadata["original_shape"] == [3, 4, 4]
    assert metadata["segment_voxel_count"] == 6
    assert metadata["segment_mask_shape"] == [3, 4, 4]
    assert metadata["segment_label"] == "Multi Slice ROI"

    job_id = None
    try:
        created = create_encrypted_dicom_job(
            values,
            "VARIANCE",
            metadata,
            he_mode="RAW_VOXELS",
        )
        job_id = created["job_id"]
        computed = compute_encrypted_dicom_analysis(job_id)
        assert computed["result_is_ciphertext"] is True
        decrypted = decrypt_dicom_analysis(job_id)
        assert_close(
            float(decrypted["value"]),
            float(expected.var()),
            "DICOM_SEG_MULTI_VARIANCE",
        )
    finally:
        if job_id:
            cleanup_dicom_he_job(job_id)

    print("DICOM SEG MULTI-SLICE RAW-VOXEL ROI: PASS")


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

        block_expectations = {
            "MEAN": float(values.mean()),
            "STANDARD_DEVIATION": float(values.std()),
            "ROOT_MEAN_SQUARED": float(np.sqrt(np.mean(values ** 2))),
            "TOTAL_ENERGY": float(np.sum(values ** 2)),
        }
        for block_analysis, block_reference in block_expectations.items():
            created_block = create_encrypted_dicom_job(
                values,
                block_analysis,
                metadata,
                he_mode="BLOCK_STATS",
            )
            block_job = created_block["job_id"]
            assert (
                created_block["representation"]
                == "ENCRYPTED_BLOCK_SUFFICIENT_STATISTICS"
            )
            assert created_block["researcher_has_encrypted_raw_voxels"] is False
            compute_encrypted_dicom_analysis(block_job)
            decrypted_block = decrypt_dicom_analysis(block_job)
            assert_close(
                float(decrypted_block["value"]),
                block_reference,
                f"BLOCK_STATS_{block_analysis}",
            )
            cleanup_dicom_he_job(block_job)
            block_job = None
        print("BLOCK STATS V2 FALLBACKS: PASS")
    finally:
        if raw_job:
            cleanup_dicom_he_job(raw_job)
        if block_job:
            cleanup_dicom_he_job(block_job)


def main() -> None:
    test_ct_series()
    test_mr_series()
    test_privacy_guards()
    test_dicom_seg_roi()
    test_multislice_dicom_seg_roi()
    test_raw_multichunk_and_block_fallback()
    print("DICOM HE STATISTICS: PASS")


if __name__ == "__main__":
    main()
