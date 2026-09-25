from __future__ import annotations

import hashlib
import io
import json
import math
import re
import shutil
import subprocess
import uuid
import zipfile
from pathlib import Path, PurePosixPath

import numpy as np
import pydicom
from pydicom.pixels import apply_modality_lut

try:
    import highdicom as hd
except ImportError:  # pragma: no cover - fallback remains usable
    hd = None

from backend.he_service import (
    _ipfs_add_file,
    _ipfs_cat_artifact,
)
from backend.secure_temp import secure_plaintext_temp_root
from backend.secure_temp import require_staging_capacity


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = PROJECT_ROOT / "seal_demo" / "build"

HOSPITAL_ENCRYPT = BUILD_DIR / "dicom_hospital_encrypt_stats"
RESEARCHER_COMPUTE = BUILD_DIR / "dicom_researcher_compute_stats"
RAW_HOSPITAL_ENCRYPT = BUILD_DIR / "dicom_hospital_encrypt_raw"
RAW_RESEARCHER_COMPUTE = BUILD_DIR / "dicom_researcher_compute_raw"
HOSPITAL_DECRYPT = BUILD_DIR / "dicom_hospital_decrypt_stats"

RUNTIME_ROOT = secure_plaintext_temp_root() / "medical-he-jobs"
RUNTIME_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
try:
    RUNTIME_ROOT.chmod(0o700)
except OSError:
    pass

JOB_PATTERN = re.compile(r"^[0-9a-f]{32}$")

SUPPORTED_ANALYSES = {
    "SUM",
    "MEAN",
    "ENERGY",
    "TOTAL_ENERGY",
    "SECOND_MOMENT",
    "ROOT_MEAN_SQUARED",
    "VARIANCE",
    "STANDARD_DEVIATION",
    "SKEWNESS",
    "KURTOSIS",
    "CENTRAL_MOMENT_3",
    "CENTRAL_MOMENT_4",
}
SUPPORTED_SCOPES = {"WHOLE_VOLUME", "SLICE", "ROI_BOX", "DICOM_SEG"}
SUPPORTED_MODALITIES = {"CT", "MR"}
SUPPORTED_HE_MODES = {"RAW_VOXELS", "BLOCK_STATS"}

MAX_DICOM_FILES = 5000
MAX_UNCOMPRESSED_ZIP = 2 * 1024 * 1024 * 1024
MAX_VOXELS = 150_000_000
MAX_RAW_VOXELS = MAX_VOXELS
MAX_STAT_BLOCKS = 2048


def _job_dir(job_id: str) -> Path:
    if not JOB_PATTERN.fullmatch(job_id):
        raise ValueError("Invalid HE job ID")
    return RUNTIME_ROOT / job_id


def _run(binary: Path, cwd: Path, timeout: int = 180) -> str:
    if not binary.exists():
        raise RuntimeError(
            f"Microsoft SEAL binary is not built: {binary.name}"
        )

    result = subprocess.run(
        [str(binary)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        message = (
            result.stderr.strip()
            or result.stdout.strip()
            or "Unknown DICOM HE process error"
        )
        raise RuntimeError(f"{binary.name} failed: {message}")

    return result.stdout


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_dicom_from_bytes(raw: bytes) -> pydicom.Dataset:
    try:
        dataset = pydicom.dcmread(
            io.BytesIO(raw),
            force=False,
        )
    except Exception as exc:
        raise ValueError("Invalid DICOM object") from exc

    if "PixelData" not in dataset:
        raise ValueError("DICOM object has no PixelData")

    return dataset


def _read_dicom_datasets(data: bytes) -> list[pydicom.Dataset]:
    buffer = io.BytesIO(data)

    if zipfile.is_zipfile(buffer):
        buffer.seek(0)
        datasets: list[pydicom.Dataset] = []

        with zipfile.ZipFile(buffer, "r") as archive:
            infos = [
                info
                for info in archive.infolist()
                if not info.is_dir()
            ]

            if not infos:
                raise ValueError("DICOM ZIP is empty")

            if len(infos) > MAX_DICOM_FILES:
                raise ValueError("DICOM ZIP contains too many files")

            total_size = sum(info.file_size for info in infos)
            if total_size > MAX_UNCOMPRESSED_ZIP:
                raise ValueError("DICOM ZIP expands beyond the safety limit")

            for info in infos:
                relative = PurePosixPath(info.filename)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("Unsafe path in DICOM ZIP")

                raw = archive.read(info)
                try:
                    dataset = _safe_dicom_from_bytes(raw)
                except ValueError:
                    # Ignore non-image helpers such as DICOMDIR.
                    continue

                datasets.append(dataset)

        if not datasets:
            raise ValueError("No image DICOM objects found in ZIP")

        return datasets

    return [_safe_dicom_from_bytes(data)]


def _validate_dicom_series(
    datasets: list[pydicom.Dataset],
) -> str:
    modalities = {
        str(getattr(ds, "Modality", "")).upper()
        for ds in datasets
        if getattr(ds, "Modality", None)
    }

    if len(modalities) != 1:
        raise ValueError("DICOM input must contain exactly one modality")

    modality = next(iter(modalities))
    if modality not in SUPPORTED_MODALITIES:
        raise ValueError(
            "DICOM HE V1 supports CT and MR modalities only"
        )

    series_uids = {
        str(ds.SeriesInstanceUID)
        for ds in datasets
        if getattr(ds, "SeriesInstanceUID", None)
    }
    if len(series_uids) > 1:
        raise ValueError("DICOM input contains multiple series")

    for ds in datasets:
        if str(getattr(ds, "BurnedInAnnotation", "")).upper() == "YES":
            raise ValueError(
                "DICOM reports burned-in annotation; clean pixel data first"
            )

        if int(getattr(ds, "SamplesPerPixel", 1)) != 1:
            raise ValueError(
                "DICOM HE V1 supports monochrome CT/MR images only"
            )

    return modality


def _ordered_slices(datasets: list[pydicom.Dataset]) -> list[pydicom.Dataset]:
    """Order classic slices along their shared patient-space plane normal."""
    if len(datasets) < 2:
        return list(datasets)

    has_geometry = [
        getattr(ds, "ImagePositionPatient", None) is not None
        or getattr(ds, "ImageOrientationPatient", None) is not None
        for ds in datasets
    ]
    if not any(has_geometry):
        # Legacy classic images without any geometry can only be ordered by
        # explicit, unique instance numbers. This does not imply spatial volume.
        try:
            numbered = [(int(ds.InstanceNumber), ds) for ds in datasets]
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("DICOM slices lack geometry and unique InstanceNumber") from exc
        if len({number for number, _ in numbered}) != len(numbered):
            raise ValueError("DICOM slices lack geometry and unique InstanceNumber")
        return [ds for _, ds in sorted(numbered, key=lambda item: item[0])]
    if not all(has_geometry):
        raise ValueError("DICOM series has incomplete slice geometry")

    reference_normal = None
    reference_orientation = None
    reference_spacing = None
    reference_frame = None
    entries = []
    for ds in datasets:
        try:
            orientation = np.asarray(ds.ImageOrientationPatient, dtype=np.float64)
            position = np.asarray(ds.ImagePositionPatient, dtype=np.float64)
            spacing = np.asarray(ds.PixelSpacing, dtype=np.float64)
            if orientation.shape != (6,) or position.shape != (3,) or spacing.shape != (2,):
                raise ValueError()
            if not np.isfinite(orientation).all() or not np.isfinite(position).all():
                raise ValueError()
            if not np.isfinite(spacing).all() or np.any(spacing <= 0):
                raise ValueError()
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("DICOM series has incomplete slice geometry") from exc
        row, column = orientation[:3], orientation[3:]
        if (abs(np.linalg.norm(row) - 1) > 1e-3
                or abs(np.linalg.norm(column) - 1) > 1e-3
                or abs(np.dot(row, column)) > 1e-3):
            raise ValueError("DICOM slice orientation is invalid")
        normal = np.cross(row, column)
        frame = str(getattr(ds, "FrameOfReferenceUID", ""))
        if reference_normal is None:
            reference_normal = normal
            reference_orientation = orientation
            reference_spacing = spacing
            reference_frame = frame
        elif (not np.allclose(orientation, reference_orientation, atol=1e-3, rtol=0)
              or not np.allclose(spacing, reference_spacing, atol=1e-3, rtol=0)
              or frame != reference_frame):
            raise ValueError("DICOM slices have inconsistent orientation, spacing or frame")
        entries.append((float(np.dot(position, reference_normal)), position, ds))

    entries.sort(key=lambda item: item[0])
    coordinates = [entry[0] for entry in entries]
    if any(b - a <= 1e-3 for a, b in zip(coordinates, coordinates[1:])):
        raise ValueError("DICOM slices have duplicate or overlapping planes")
    anchor = entries[0][1]
    for coordinate, position, _ in entries:
        residual = position - anchor - (coordinate - coordinates[0]) * reference_normal
        if np.linalg.norm(residual) > 1e-2:
            raise ValueError("DICOM slice positions shift within the image plane")
    return [entry[2] for entry in entries]


def _voxel_spacing_mm(
    datasets: list[pydicom.Dataset],
) -> tuple[float, float, float] | None:
    ordered = _ordered_slices(datasets)
    first = ordered[0]
    try:
        row_spacing, col_spacing = [float(x) for x in first.PixelSpacing]
        if not (math.isfinite(row_spacing) and math.isfinite(col_spacing)):
            return None
        if row_spacing <= 0 or col_spacing <= 0:
            return None
        if len(ordered) > 1:
            normal = np.cross(
                np.asarray(first.ImageOrientationPatient[:3], dtype=np.float64),
                np.asarray(first.ImageOrientationPatient[3:], dtype=np.float64),
            )
            projected = [float(np.dot(np.asarray(ds.ImagePositionPatient, dtype=np.float64), normal)) for ds in ordered]
            diffs = np.diff(projected)
            spacing = float(np.median(diffs))
            if not np.allclose(diffs, spacing, rtol=0.01, atol=0.01):
                return None
        else:
            spacing = float(getattr(first, "SpacingBetweenSlices", None) or getattr(first, "SliceThickness", 0))
        if not math.isfinite(spacing) or spacing <= 0:
            return None
        return spacing, row_spacing, col_spacing
    except (AttributeError, TypeError, ValueError):
        return None


def _fallback_volume(
    datasets: list[pydicom.Dataset],
) -> np.ndarray:
    ordered = _ordered_slices(datasets)

    if len(ordered) == 1:
        ds = ordered[0]
        try:
            array = np.asarray(ds.pixel_array)
        except Exception as exc:
            raise ValueError(
                "Unable to decode DICOM PixelData; "
                "install the required pydicom pixel decoder"
            ) from exc

        transformed = np.asarray(
            apply_modality_lut(array, ds),
            dtype=np.float64,
        )

        if transformed.ndim == 2:
            return transformed[np.newaxis, :, :]
        if transformed.ndim == 3:
            return transformed

        raise ValueError("Unsupported DICOM pixel dimensions")

    slices: list[np.ndarray] = []
    expected_shape: tuple[int, int] | None = None

    for ds in ordered:
        try:
            array = np.asarray(ds.pixel_array)
        except Exception as exc:
            raise ValueError(
                "Unable to decode DICOM PixelData; "
                "install the required pydicom pixel decoder"
            ) from exc

        transformed = np.asarray(
            apply_modality_lut(array, ds),
            dtype=np.float64,
        )

        if transformed.ndim != 2:
            raise ValueError(
                "Mixed or multi-frame DICOM series is unsupported "
                "by the fallback loader"
            )

        shape = (int(transformed.shape[0]), int(transformed.shape[1]))
        if expected_shape is None:
            expected_shape = shape
        elif shape != expected_shape:
            raise ValueError("DICOM slices do not share one matrix size")

        slices.append(transformed)

    return np.stack(slices, axis=0)


def _build_volume(
    datasets: list[pydicom.Dataset],
) -> tuple[np.ndarray, str]:
    # Classic series retain one explicit normal-projection ordering, including
    # when highdicom is installed. SEG alignment uses highdicom separately.
    return _fallback_volume(datasets), "pydicom"


def _segment_code_meaning(description, keyword: str) -> str | None:
    sequence = getattr(description, keyword, None)
    if not sequence:
        return None
    item = sequence[0]
    meaning = getattr(item, "CodeMeaning", None)
    return str(meaning) if meaning not in (None, "") else None


def _read_single_segmentation(data: bytes):
    if hd is None:
        raise RuntimeError("highdicom is required for DICOM SEG analysis")

    datasets = _read_dicom_datasets(data)
    if len(datasets) != 1:
        raise ValueError(
            "Segmentation dataset must contain exactly one DICOM SEG object"
        )

    ds = datasets[0]
    if str(getattr(ds, "Modality", "")).upper() != "SEG":
        raise ValueError("Segmentation dataset must have DICOM Modality SEG")

    if str(getattr(ds, "SegmentationType", "")).upper() != "BINARY":
        raise ValueError(
            "DICOM SEG HE currently supports binary segmentations only"
        )

    try:
        segmentation = hd.seg.Segmentation.from_dataset(ds, copy=True)
    except Exception as exc:
        raise ValueError("Invalid or unsupported DICOM SEG object") from exc

    return segmentation


def list_dicom_seg_segments(data: bytes) -> dict:
    segmentation = _read_single_segmentation(data)
    segments = []

    for number in segmentation.segment_numbers:
        description = segmentation.get_segment_description(int(number))
        segments.append(
            {
                "segment_number": int(number),
                "segment_label": str(
                    getattr(description, "SegmentLabel", f"Segment {number}")
                ),
                "property_category": _segment_code_meaning(
                    description,
                    "SegmentedPropertyCategoryCodeSequence",
                ),
                "property_type": _segment_code_meaning(
                    description,
                    "SegmentedPropertyTypeCodeSequence",
                ),
                "algorithm_type": str(
                    getattr(description, "SegmentAlgorithmType", "") or ""
                ),
            }
        )

    return {
        "segmentation_type": "BINARY",
        "segment_count": len(segments),
        "segments": segments,
    }


def extract_dicom_seg_analysis_values(
    source_data: bytes,
    segmentation_data: bytes,
    segment_number: int,
) -> tuple[np.ndarray, dict]:
    if hd is None:
        raise RuntimeError("highdicom is required for DICOM SEG analysis")

    source_datasets = _read_dicom_datasets(source_data)
    modality = _validate_dicom_series(source_datasets)
    source_datasets = _ordered_slices(source_datasets)

    try:
        source_volume = hd.get_volume_from_series(
            source_datasets,
            dtype=np.float64,
            apply_modality_transform=None,
            apply_voi_transform=False,
        )
    except Exception as exc:
        raise ValueError(
            "DICOM SEG analysis requires complete source image geometry "
            "(orientation, position, spacing, and frame of reference)"
        ) from exc

    source_array = np.asarray(source_volume.array, dtype=np.float64)
    if source_array.ndim != 3:
        raise ValueError("DICOM source volume must be three-dimensional")
    if not np.isfinite(source_array).all():
        raise ValueError("DICOM source volume contains non-finite values")
    if source_array.size > MAX_VOXELS:
        raise ValueError(f"DICOM volume exceeds {MAX_VOXELS:,} voxels")

    segmentation = _read_single_segmentation(segmentation_data)
    source_frame = str(getattr(source_datasets[0], "FrameOfReferenceUID", ""))
    if not source_frame or str(getattr(segmentation, "FrameOfReferenceUID", "")) != source_frame:
        raise ValueError("DICOM SEG frame of reference does not match source")
    source_series = str(getattr(source_datasets[0], "SeriesInstanceUID", ""))
    source_sops = {str(ds.SOPInstanceUID) for ds in source_datasets}
    references = getattr(segmentation, "ReferencedSeriesSequence", [])
    if len(references) != 1 or str(getattr(references[0], "SeriesInstanceUID", "")) != source_series:
        raise ValueError("DICOM SEG references the wrong source series")
    referenced_sops = {
        str(getattr(item, "ReferencedSOPInstanceUID", ""))
        for item in getattr(references[0], "ReferencedInstanceSequence", [])
    }
    if referenced_sops != source_sops:
        raise ValueError("DICOM SEG source image references are incomplete or mismatched")

    try:
        source_positions = [np.asarray(ds.ImagePositionPatient, dtype=np.float64) for ds in source_datasets]
        source_orientation = np.asarray(source_datasets[0].ImageOrientationPatient, dtype=np.float64)
        source_spacing = np.asarray(source_datasets[0].PixelSpacing, dtype=np.float64)
        shared = segmentation.SharedFunctionalGroupsSequence[0]
        seg_orientation = np.asarray(shared.PlaneOrientationSequence[0].ImageOrientationPatient, dtype=np.float64)
        seg_spacing = np.asarray(shared.PixelMeasuresSequence[0].PixelSpacing, dtype=np.float64)
        frame_positions = [
            np.asarray(frame.PlanePositionSequence[0].ImagePositionPatient, dtype=np.float64)
            for frame in segmentation.PerFrameFunctionalGroupsSequence
        ]
    except (AttributeError, IndexError, TypeError, ValueError) as exc:
        raise ValueError("DICOM SEG lacks required frame position or orientation") from exc
    if (segmentation.Rows != source_datasets[0].Rows
            or segmentation.Columns != source_datasets[0].Columns
            or not np.allclose(seg_orientation, source_orientation, atol=1e-3, rtol=0)
            or not np.allclose(seg_spacing, source_spacing, atol=1e-3, rtol=0)):
        raise ValueError("DICOM SEG orientation or matrix geometry does not match source")
    if len(frame_positions) != len(source_positions) or not all(
        np.allclose(frame, source, atol=1e-2, rtol=0)
        for frame, source in zip(sorted(frame_positions, key=lambda x: tuple(x)),
                                 sorted(source_positions, key=lambda x: tuple(x)))
    ):
        raise ValueError("DICOM SEG frame positions do not cover source planes exactly")
    available = [int(value) for value in segmentation.segment_numbers]
    requested = int(segment_number)
    if requested not in available:
        raise ValueError(
            "segment_number is not present in the DICOM SEG object; "
            f"available segments: {available}"
        )

    try:
        segment_volume = segmentation.get_volume(
            segment_numbers=[requested],
            combine_segments=True,
            relabel=False,
            dtype=np.uint8,
            allow_missing_positions=False,
        )
        aligned_segment = segment_volume.match_geometry(source_volume)
    except Exception as exc:
        raise ValueError(
            "DICOM SEG geometry does not align with the source image series"
        ) from exc

    mask = np.asarray(aligned_segment.array) > 0
    if mask.ndim != 3 or mask.shape != source_array.shape:
        raise ValueError("Aligned DICOM SEG mask does not match source volume shape")

    voxel_count = int(np.count_nonzero(mask))
    if voxel_count < 2:
        raise ValueError("Selected DICOM segment contains fewer than two voxels")

    selected = np.ascontiguousarray(source_array[mask], dtype=np.float64)
    description = segmentation.get_segment_description(requested)
    label = str(
        getattr(description, "SegmentLabel", f"Segment {requested}")
    )
    unit = "HU" if modality == "CT" else "relative_intensity"
    voxel_spacing_mm = _voxel_spacing_mm(source_datasets)
    voxel_volume_mm3 = float(np.prod(voxel_spacing_mm)) if voxel_spacing_mm else None

    metadata = {
        "modality": modality,
        "unit": unit,
        "scope": "DICOM_SEG",
        "slice_index": None,
        "roi_box": None,
        "original_shape": [int(x) for x in source_array.shape],
        "selected_shape": [voxel_count],
        "voxel_count": voxel_count,
        "dicom_loader": "highdicom",
        "segmentation_type": "BINARY",
        "segment_number": requested,
        "segment_label": label,
        "segment_voxel_count": voxel_count,
        "segment_mask_shape": [int(x) for x in mask.shape],
        "voxel_spacing_mm": list(voxel_spacing_mm) if voxel_spacing_mm else None,
        "voxel_volume_mm3": voxel_volume_mm3,
        "physical_volume_valid": voxel_volume_mm3 is not None,
        "segment_physical_volume_mm3": float(voxel_count) * voxel_volume_mm3 if voxel_volume_mm3 is not None else None,
        "segment_property_category": _segment_code_meaning(
            description,
            "SegmentedPropertyCategoryCodeSequence",
        ),
        "segment_property_type": _segment_code_meaning(
            description,
            "SegmentedPropertyTypeCodeSequence",
        ),
    }

    return selected, metadata


def extract_dicom_analysis_values(
    data: bytes,
    scope: str = "WHOLE_VOLUME",
    slice_index: int | None = None,
    roi_box: dict | None = None,
) -> tuple[np.ndarray, dict]:
    datasets = _read_dicom_datasets(data)
    modality = _validate_dicom_series(datasets)
    volume, loader = _build_volume(datasets)

    if volume.ndim != 3:
        raise ValueError("DICOM volume must be three-dimensional")

    if not np.isfinite(volume).all():
        raise ValueError("DICOM volume contains non-finite pixel values")

    voxel_count = int(volume.size)
    if voxel_count < 2:
        raise ValueError("DICOM volume is too small for analysis")
    if voxel_count > MAX_VOXELS:
        raise ValueError(
            f"DICOM volume exceeds {MAX_VOXELS:,} voxels"
        )

    normalized_scope = scope.strip().upper()
    if normalized_scope not in SUPPORTED_SCOPES:
        raise ValueError(
            "scope must be WHOLE_VOLUME, SLICE, ROI_BOX, or DICOM_SEG"
        )

    if normalized_scope == "DICOM_SEG":
        raise ValueError(
            "DICOM_SEG scope requires a separate segmentation dataset"
        )

    original_shape = tuple(int(x) for x in volume.shape)

    selected = volume
    selected_slice: int | None = None
    normalized_roi: dict | None = None

    if normalized_scope == "SLICE":
        if slice_index is None:
            raise ValueError("slice_index is required for SLICE scope")
        if slice_index < 0 or slice_index >= volume.shape[0]:
            raise ValueError("slice_index is outside the DICOM volume")
        selected_slice = int(slice_index)
        selected = volume[selected_slice:selected_slice + 1, :, :]

    elif normalized_scope == "ROI_BOX":
        if not isinstance(roi_box, dict):
            raise ValueError("roi_box is required for ROI_BOX scope")

        names = (
            "slice_start",
            "slice_end",
            "row_start",
            "row_end",
            "col_start",
            "col_end",
        )
        try:
            values = {name: int(roi_box[name]) for name in names}
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "ROI box requires integer slice/row/col start and end values"
            ) from exc

        s0, s1 = values["slice_start"], values["slice_end"]
        r0, r1 = values["row_start"], values["row_end"]
        c0, c1 = values["col_start"], values["col_end"]

        if not (0 <= s0 < s1 <= volume.shape[0]):
            raise ValueError("ROI slice range is outside the DICOM volume")
        if not (0 <= r0 < r1 <= volume.shape[1]):
            raise ValueError("ROI row range is outside the DICOM volume")
        if not (0 <= c0 < c1 <= volume.shape[2]):
            raise ValueError("ROI column range is outside the DICOM volume")

        normalized_roi = values
        selected = volume[s0:s1, r0:r1, c0:c1]

    selected = np.ascontiguousarray(
        selected,
        dtype=np.float64,
    )

    if selected.size < 2:
        raise ValueError("Selected DICOM region is too small for analysis")

    unit = "HU" if modality == "CT" else "relative_intensity"
    voxel_spacing_mm = _voxel_spacing_mm(datasets)
    voxel_volume_mm3 = float(np.prod(voxel_spacing_mm)) if voxel_spacing_mm else None

    metadata = {
        "modality": modality,
        "unit": unit,
        "scope": normalized_scope,
        "slice_index": selected_slice,
        "roi_box": normalized_roi,
        "original_shape": list(original_shape),
        "selected_shape": [int(x) for x in selected.shape],
        "voxel_count": int(selected.size),
        "voxel_spacing_mm": list(voxel_spacing_mm) if voxel_spacing_mm else None,
        "voxel_volume_mm3": voxel_volume_mm3,
        "physical_volume_valid": voxel_volume_mm3 is not None,
        "dicom_loader": loader,
    }

    return selected.reshape(-1), metadata


def _normalization_scale(values: np.ndarray) -> float:
    max_abs = float(np.max(np.abs(values)))
    if not math.isfinite(max_abs):
        raise ValueError("DICOM values contain non-finite numbers")
    if max_abs == 0.0:
        return 1.0

    exponent = math.ceil(math.log2(max_abs))
    scale = float(2 ** exponent)
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("DICOM value range cannot be normalized for CKKS")
    return scale


def build_block_statistics(
    values: np.ndarray,
    center_offset: float = 0.0,
) -> tuple[list[tuple[float, float]], float]:
    flat = np.asarray(values, dtype=np.float64).reshape(-1)
    if center_offset:
        flat = flat - float(center_offset)

    if flat.size < 2:
        raise ValueError("At least two DICOM values are required")
    if not np.isfinite(flat).all():
        raise ValueError("DICOM values contain non-finite numbers")

    normalization = _normalization_scale(flat)

    block_size = max(
        1,
        math.ceil(flat.size / MAX_STAT_BLOCKS),
    )

    rows: list[tuple[float, float]] = []

    for start in range(0, flat.size, block_size):
        chunk = flat[start:start + block_size] / normalization
        block_sum = float(np.sum(chunk, dtype=np.float64))
        block_sumsq = float(np.dot(chunk, chunk))
        rows.append((block_sum, block_sumsq))

    if len(rows) > MAX_STAT_BLOCKS:
        raise RuntimeError("Internal DICOM block packing overflow")

    return rows, normalization


def build_raw_voxel_input(
    values: np.ndarray,
    analysis: str,
) -> tuple[np.ndarray, float, float]:
    flat = np.asarray(values, dtype=np.float64).reshape(-1)
    if flat.size < 2:
        raise ValueError("At least two DICOM values are required")
    if flat.size > MAX_RAW_VOXELS:
        raise ValueError(
            f"DICOM analysis supports at most {MAX_RAW_VOXELS:,} selected voxels"
        )
    # Each 4096-slot CKKS ciphertext and its temporary evaluation state can
    # consume substantially more space than the input doubles. Reject early
    # using a conservative per-chunk reservation rather than a voxel ceiling.
    chunks = math.ceil(flat.size / 4096)
    require_staging_capacity(chunks * 1024 * 1024 + flat.nbytes * 2)
    if not np.isfinite(flat).all():
        raise ValueError("DICOM values contain non-finite numbers")

    center_offset = 0.0
    if analysis in {"VARIANCE", "STANDARD_DEVIATION"}:
        center_offset = float((np.min(flat) + np.max(flat)) / 2.0)
        flat = flat - center_offset
    elif analysis in {
        "SKEWNESS",
        "KURTOSIS",
        "CENTRAL_MOMENT_3",
        "CENTRAL_MOMENT_4",
    }:
        center_offset = float(np.mean(flat, dtype=np.float64))
        flat = flat - center_offset

    normalization = _normalization_scale(flat)
    normalized = np.ascontiguousarray(flat / normalization, dtype="<f8")
    return normalized, normalization, center_offset


def _manifest_hash(exchange: Path) -> str:
    files = [
        path
        for path in sorted(exchange.rglob("*"))
        if path.is_file() and path.name != "result.ct"
    ]

    if not files:
        raise RuntimeError("No DICOM HE artifacts found")

    digest = hashlib.sha256()

    for path in files:
        relative = path.relative_to(exchange).as_posix()
        if "secret" in relative.lower():
            raise RuntimeError(
                "Secret material detected in researcher exchange"
            )

        digest.update(relative.encode("utf-8"))
        digest.update(b":")
        digest.update(_sha256_file(path).encode("ascii"))
        digest.update(b"\n")

    return digest.hexdigest()


def create_encrypted_dicom_job(
    values: np.ndarray,
    analysis: str,
    metadata: dict,
    he_mode: str = "RAW_VOXELS",
) -> dict:
    normalized_analysis = analysis.strip().upper()
    if normalized_analysis not in SUPPORTED_ANALYSES:
        raise ValueError(
            "Unsupported DICOM HE analysis: "
            + normalized_analysis
        )

    normalized_mode = he_mode.strip().upper()
    if normalized_mode not in SUPPORTED_HE_MODES:
        raise ValueError(
            "he_mode must be RAW_VOXELS or BLOCK_STATS"
        )

    if normalized_mode == "BLOCK_STATS" and normalized_analysis in {
        "SKEWNESS",
        "KURTOSIS",
        "CENTRAL_MOMENT_3",
        "CENTRAL_MOMENT_4",
    }:
        raise ValueError(
            normalized_analysis
            + " requires RAW_VOXELS so higher moments are computed "
              "by the researcher over encrypted voxels"
        )

    if normalized_analysis == "TOTAL_ENERGY" and not metadata.get("physical_volume_valid", metadata.get("voxel_volume_mm3") is not None):
        raise ValueError("TOTAL_ENERGY requires proven regular physical voxel spacing")

    job_id = uuid.uuid4().hex
    job = _job_dir(job_id)
    sample_input = job / "sample_input"
    sample_input.mkdir(parents=True)

    representation: str
    normalization: float
    center_offset: float
    block_count: int | None = None
    chunk_count: int | None = None

    try:
        if normalized_mode == "RAW_VOXELS":
            normalized, normalization, center_offset = build_raw_voxel_input(
                values,
                normalized_analysis,
            )
            normalized.tofile(sample_input / "raw_voxels.f64")
            _run(RAW_HOSPITAL_ENCRYPT, job, timeout=300)
            representation = "ENCRYPTED_RAW_VOXELS"
            count_path = job / "research_exchange" / "chunk_count.txt"
            if not count_path.exists():
                raise RuntimeError("Raw-voxel CKKS chunk count is missing")
            chunk_count = int(count_path.read_text(encoding="utf-8").strip())
        else:
            center_offset = 0.0
            if normalized_analysis in {"VARIANCE", "STANDARD_DEVIATION"}:
                raw = np.asarray(values, dtype=np.float64).reshape(-1)
                center_offset = float(
                    (np.min(raw) + np.max(raw)) / 2.0
                )

            rows, normalization = build_block_statistics(
                values,
                center_offset=center_offset,
            )
            block_count = len(rows)
            stats_path = sample_input / "dicom_stats.csv"
            stats_path.write_text(
                "".join(
                    f"{block_sum:.17g},{block_sumsq:.17g}\n"
                    for block_sum, block_sumsq in rows
                ),
                encoding="utf-8",
            )
            _run(HOSPITAL_ENCRYPT, job)
            representation = "ENCRYPTED_BLOCK_SUFFICIENT_STATISTICS"

    except Exception:
        shutil.rmtree(job, ignore_errors=True)
        raise

    exchange = job / "research_exchange"
    private = job / "hospital_private"

    if not (private / "secret.key").exists():
        shutil.rmtree(job, ignore_errors=True)
        raise RuntimeError("Hospital HE secret key was not created")

    shutil.rmtree(sample_input, ignore_errors=True)

    voxel_count = int(np.asarray(values).size)

    (exchange / "analysis.txt").write_text(
        normalized_analysis + "\n",
        encoding="utf-8",
    )
    (exchange / "voxel_count.txt").write_text(
        str(voxel_count) + "\n",
        encoding="utf-8",
    )

    public_metadata = dict(metadata)
    public_metadata.update(
        {
            "analysis": normalized_analysis,
            "he_mode": normalized_mode,
            "representation": representation,
            "block_count": block_count,
            "chunk_count": chunk_count,
            "researcher_has_plaintext_pixels": False,
            "researcher_has_raw_pixels": False,
            "researcher_has_encrypted_raw_voxels": (
                normalized_mode == "RAW_VOXELS"
            ),
            "hospital_computed_plaintext_sufficient_statistics": (
                normalized_mode == "BLOCK_STATS"
            ),
            "researcher_has_secret_key": False,
        }
    )

    (exchange / "public_metadata.json").write_text(
        json.dumps(public_metadata, sort_keys=True),
        encoding="utf-8",
    )

    private_metadata = dict(public_metadata)
    private_metadata["normalization_scale"] = normalization
    private_metadata["center_offset"] = center_offset

    (private / "job_metadata.json").write_text(
        json.dumps(private_metadata, sort_keys=True),
        encoding="utf-8",
    )

    if any(
        "secret" in path.name.lower()
        for path in exchange.rglob("*")
        if path.is_file()
    ):
        shutil.rmtree(job, ignore_errors=True)
        raise RuntimeError(
            "Secret material leaked into researcher exchange"
        )

    return {
        "job_id": job_id,
        "state": "ENCRYPTED",
        "analysis": normalized_analysis,
        "voxel_count": voxel_count,
        "block_count": block_count,
        "chunk_count": chunk_count,
        "ciphertext_manifest_sha256": _manifest_hash(exchange),
        "he_mode": normalized_mode,
        "representation": representation,
        "researcher_has_plaintext_pixels": False,
        "researcher_has_raw_pixels": False,
        "researcher_has_encrypted_raw_voxels": (
            normalized_mode == "RAW_VOXELS"
        ),
        "hospital_computed_plaintext_sufficient_statistics": (
            normalized_mode == "BLOCK_STATS"
        ),
        "researcher_has_secret_key": False,
        **metadata,
    }


def compute_encrypted_dicom_analysis(job_id: str) -> dict:
    job = _job_dir(job_id)
    exchange = job / "research_exchange"

    if not exchange.exists():
        raise RuntimeError("Research exchange is missing")

    if any(
        "secret" in path.name.lower()
        for path in exchange.rglob("*")
        if path.is_file()
    ):
        raise RuntimeError(
            "Secret material detected in researcher exchange"
        )

    analysis = (
        exchange / "analysis.txt"
    ).read_text(encoding="utf-8").strip()
    metadata = json.loads(
        (exchange / "public_metadata.json").read_text(encoding="utf-8")
    )
    representation = str(metadata.get("representation") or "")

    if representation == "ENCRYPTED_RAW_VOXELS":
        _run(RAW_RESEARCHER_COMPUTE, job, timeout=600)
    elif representation == "ENCRYPTED_BLOCK_SUFFICIENT_STATISTICS":
        _run(RESEARCHER_COMPUTE, job)
    else:
        raise RuntimeError("Unknown DICOM HE representation")

    result = exchange / "result.ct"
    if not result.exists():
        raise RuntimeError(
            "Encrypted DICOM analysis result was not produced"
        )

    return {
        "job_id": job_id,
        "analysis": analysis,
        "state": "ENCRYPTED_RESULT_READY",
        "result_sha256": _sha256_file(result),
        "result_is_ciphertext": True,
        "he_mode": metadata.get("he_mode", "BLOCK_STATS"),
        "representation": representation,
        "researcher_has_plaintext_pixels": False,
        "researcher_has_raw_pixels": False,
        "researcher_has_encrypted_raw_voxels": bool(
            metadata.get("researcher_has_encrypted_raw_voxels", False)
        ),
        "hospital_computed_plaintext_sufficient_statistics": bool(
            metadata.get("hospital_computed_plaintext_sufficient_statistics", False)
        ),
        "researcher_has_secret_key": False,
    }


def _result_unit(analysis: str, base_unit: str) -> str:
    if analysis == "SUM":
        return f"{base_unit}*voxel"
    if analysis == "TOTAL_ENERGY":
        return f"{base_unit}^2*mm^3"
    if analysis in {"ENERGY", "SECOND_MOMENT", "VARIANCE"}:
        return f"{base_unit}^2"
    if analysis == "CENTRAL_MOMENT_3":
        return f"{base_unit}^3"
    if analysis == "CENTRAL_MOMENT_4":
        return f"{base_unit}^4"
    if analysis in {"SKEWNESS", "KURTOSIS"}:
        return "dimensionless"
    return base_unit


def _nonnegative_ckks(value: float, label: str) -> float:
    if value >= 0:
        return value
    if abs(value) <= 1e-6:
        return 0.0
    raise RuntimeError(f"{label} became negative after CKKS evaluation: {value}")


def decrypt_dicom_analysis(job_id: str) -> dict:
    job = _job_dir(job_id)
    result = job / "research_exchange" / "result.ct"

    if not result.exists():
        raise ValueError("Encrypted DICOM result is not ready")

    metadata_path = (
        job / "hospital_private" / "job_metadata.json"
    )
    if not metadata_path.exists():
        raise RuntimeError(
            "Hospital DICOM HE metadata is unavailable"
        )

    metadata = json.loads(
        metadata_path.read_text(encoding="utf-8")
    )

    output = _run(HOSPITAL_DECRYPT, job)

    match = re.search(
        r"Decrypted result:\s*"
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)"
        r"(?:[eE][-+]?\d+)?)",
        output,
    )
    if not match:
        raise RuntimeError(
            "Could not parse decrypted DICOM HE result"
        )

    auxiliary_match = re.search(
        r"Decrypted auxiliary result:\s*"
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)"
        r"(?:[eE][-+]?\d+)?)",
        output,
    )
    normalized_auxiliary = (
        float(auxiliary_match.group(1))
        if auxiliary_match
        else None
    )

    normalized_value = float(match.group(1))
    analysis = str(metadata["analysis"])
    scale = float(metadata["normalization_scale"])
    voxel_count = int(metadata["voxel_count"])

    if analysis in {"SUM", "MEAN"}:
        value = normalized_value * scale
    elif analysis in {"ENERGY", "SECOND_MOMENT"}:
        value = normalized_value * scale * scale
    elif analysis == "TOTAL_ENERGY":
        value = (
            normalized_value
            * scale
            * scale
            * float(metadata["voxel_volume_mm3"])
        )
    elif analysis == "VARIANCE":
        normalized_variance = _nonnegative_ckks(
            normalized_value,
            "Variance",
        )
        value = normalized_variance * scale * scale
    elif analysis == "STANDARD_DEVIATION":
        normalized_variance = _nonnegative_ckks(
            normalized_value,
            "Variance",
        )
        value = math.sqrt(normalized_variance) * scale
    elif analysis == "ROOT_MEAN_SQUARED":
        normalized_second = _nonnegative_ckks(
            normalized_value,
            "Second moment",
        )
        value = math.sqrt(normalized_second) * scale
    elif analysis == "CENTRAL_MOMENT_3":
        value = (
            normalized_value
            / float(voxel_count)
            * (scale ** 3)
        )
    elif analysis == "CENTRAL_MOMENT_4":
        value = (
            normalized_value
            / float(voxel_count)
            * (scale ** 4)
        )
    elif analysis in {"SKEWNESS", "KURTOSIS"}:
        if normalized_auxiliary is None:
            raise RuntimeError(
                f"{analysis} requires two encrypted central moments"
            )
        m2 = normalized_value / float(voxel_count)
        m2 = _nonnegative_ckks(m2, "Second central moment")
        if m2 <= 1e-18:
            value = 0.0
        elif analysis == "SKEWNESS":
            m3 = normalized_auxiliary / float(voxel_count)
            value = m3 / (m2 ** 1.5)
        else:
            m4 = normalized_auxiliary / float(voxel_count)
            m4 = _nonnegative_ckks(m4, "Fourth central moment")
            value = m4 / (m2 * m2)
    else:
        raise RuntimeError(
            f"Unsupported decrypted DICOM HE analysis: {analysis}"
        )

    if not math.isfinite(value):
        raise RuntimeError("CKKS DICOM result is not finite")

    return {
        "job_id": job_id,
        "state": "DECRYPTED",
        "analysis": analysis,
        "value": value,
        "unit": _result_unit(
            analysis,
            str(metadata["unit"]),
        ),
        "ckks_approximate": True,
        "accuracy_note": "Approximate CKKS result; no fixed error bound",
        "he_mode": metadata.get("he_mode", "BLOCK_STATS"),
        "representation": metadata["representation"],
        "modality": metadata["modality"],
        "scope": metadata["scope"],
        "slice_index": metadata["slice_index"],
        "roi_box": metadata.get("roi_box"),
        "segmentation_dataset_id": metadata.get("segmentation_dataset_id"),
        "segment_number": metadata.get("segment_number"),
        "segment_label": metadata.get("segment_label"),
        "segment_voxel_count": metadata.get("segment_voxel_count"),
        "segment_physical_volume_mm3": metadata.get(
            "segment_physical_volume_mm3"
        ),
        "selected_shape": metadata["selected_shape"],
        "voxel_count": metadata["voxel_count"],
        "voxel_spacing_mm": metadata.get("voxel_spacing_mm"),
        "voxel_volume_mm3": metadata.get("voxel_volume_mm3"),
        "physical_volume_valid": metadata.get("physical_volume_valid", False),
        "researcher_has_plaintext_pixels": False,
        "researcher_has_encrypted_raw_voxels": bool(
            metadata.get("researcher_has_encrypted_raw_voxels", False)
        ),
        "hospital_computed_plaintext_sufficient_statistics": bool(
            metadata.get("hospital_computed_plaintext_sufficient_statistics", False)
        ),
    }

def cleanup_dicom_he_job(job_id: str) -> None:
    shutil.rmtree(
        _job_dir(job_id),
        ignore_errors=True,
    )


def discard_encrypted_dicom_result(job_id: str) -> None:
    path = _job_dir(job_id) / "research_exchange" / "result.ct"
    if path.exists():
        path.unlink()


def remove_dicom_research_exchange(job_id: str) -> None:
    shutil.rmtree(
        _job_dir(job_id) / "research_exchange",
        ignore_errors=True,
    )


def publish_dicom_ciphertext_bundle(job_id: str) -> str:
    job = _job_dir(job_id)
    exchange = job / "research_exchange"

    if not exchange.exists():
        raise FileNotFoundError(
            "DICOM HE researcher exchange is missing"
        )

    files = [
        path
        for path in sorted(exchange.rglob("*"))
        if path.is_file() and path.name != "result.ct"
    ]

    if not files:
        raise RuntimeError(
            "No DICOM HE artifacts available to publish"
        )

    bundle = job / f".{job_id}.dicom_he_bundle.zip"

    try:
        with zipfile.ZipFile(
            bundle,
            "w",
            compression=zipfile.ZIP_STORED,
        ) as archive:
            for path in files:
                relative = path.relative_to(exchange).as_posix()
                if "secret" in relative.lower():
                    raise RuntimeError(
                        "Secret material detected in DICOM HE bundle"
                    )
                archive.write(path, arcname=relative)

        return _ipfs_add_file(bundle)
    finally:
        try:
            bundle.unlink()
        except FileNotFoundError:
            pass


def restore_dicom_ciphertext_bundle(
    job_id: str,
    cid: str,
    expected_manifest_sha256: str,
) -> str:
    job = _job_dir(job_id)
    job.mkdir(parents=True, exist_ok=True)

    exchange = job / "research_exchange"
    shutil.rmtree(exchange, ignore_errors=True)
    exchange.mkdir(parents=True, exist_ok=True)

    data = _ipfs_cat_artifact(cid)

    if len(data) > 512 * 1024 * 1024:
        raise RuntimeError("DICOM HE bundle is too large")

    try:
        with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
            infos = [
                info
                for info in archive.infolist()
                if not info.is_dir()
            ]

            if not infos or len(infos) > 100:
                raise RuntimeError("Invalid DICOM HE bundle")

            total_size = sum(info.file_size for info in infos)
            if total_size > 512 * 1024 * 1024:
                raise RuntimeError(
                    "DICOM HE bundle expands too large"
                )

            for info in infos:
                relative = PurePosixPath(info.filename)
                if relative.is_absolute() or ".." in relative.parts:
                    raise RuntimeError(
                        "Unsafe path in DICOM HE bundle"
                    )
                if "secret" in info.filename.lower():
                    raise RuntimeError(
                        "Secret material found in DICOM HE bundle"
                    )

                target = exchange.joinpath(*relative.parts)
                target.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )
                with archive.open(info) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)

        actual_manifest = _manifest_hash(exchange)
        if (
            actual_manifest.lower()
            != expected_manifest_sha256.lower()
        ):
            raise RuntimeError(
                "DICOM HE manifest does not match Ethereum ledger"
            )

        return actual_manifest

    except Exception:
        shutil.rmtree(exchange, ignore_errors=True)
        raise


def publish_encrypted_dicom_result(job_id: str) -> dict:
    result = _job_dir(job_id) / "research_exchange" / "result.ct"

    if not result.exists():
        raise FileNotFoundError(
            "Encrypted DICOM HE result not found"
        )

    return {
        "cid": _ipfs_add_file(result),
        "sha256": _sha256_file(result),
    }


def restore_encrypted_dicom_result(
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
        raise RuntimeError(
            "DICOM HE result SHA256 does not match Ethereum ledger"
        )

    (exchange / "result.ct").write_bytes(data)
    return actual

