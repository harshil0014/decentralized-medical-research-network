from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import shutil
import tempfile

import pydicom

from backend.dicom_utils import deidentify_dataset


MAX_SERIES_FILES = 5000
MAX_SERIES_BYTES = 2 * 1024 * 1024 * 1024


def deidentify_dicom_series_zip(path: str | Path) -> dict:
    path = Path(path)

    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        raw_dir = work / "raw"
        clean_dir = work / "clean"
        raw_dir.mkdir()
        clean_dir.mkdir()

        with ZipFile(path, "r") as archive:
            files = [item for item in archive.infolist() if not item.is_dir()]

            if not files:
                raise ValueError("ZIP is empty")
            if len(files) > MAX_SERIES_FILES:
                raise ValueError("Too many files in DICOM series ZIP")

            total_size = sum(item.file_size for item in files)
            if total_size > MAX_SERIES_BYTES:
                raise ValueError("DICOM ZIP is too large")

            for index, member in enumerate(files, start=1):
                name = Path(member.filename)
                if name.is_absolute() or ".." in name.parts:
                    raise ValueError("Unsafe ZIP path")

                target = raw_dir / f"input_{index:05d}.bin"
                with archive.open(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)

        uid_map: dict[str, str] = {}
        cleaned: list[Path] = []
        modalities: set[str] = set()
        original_study_uids: set[str] = set()
        original_series_uids: set[str] = set()

        rows = None
        columns = None

        candidates = sorted(raw_dir.iterdir())
        for src in candidates:
            try:
                ds = pydicom.dcmread(src, force=False)
            except Exception:
                # Hospital exports often include README files or other helpers.
                continue

            # Ignore DICOMDIR, SR, presentation states and other non-image objects.
            if "PixelData" not in ds:
                continue

            if "StudyInstanceUID" not in ds or "SeriesInstanceUID" not in ds:
                raise ValueError("DICOM image is missing Study/Series UID")

            original_study_uids.add(str(ds.StudyInstanceUID))
            original_series_uids.add(str(ds.SeriesInstanceUID))

            modality = str(getattr(ds, "Modality", "") or "").upper()
            if modality:
                modalities.add(modality)

            if rows is None:
                rows = getattr(ds, "Rows", None)
            if columns is None:
                columns = getattr(ds, "Columns", None)

            deidentify_dataset(ds, uid_map=uid_map)

            dst = clean_dir / f"slice_{len(cleaned) + 1:05d}.dcm"
            ds.save_as(dst, enforce_file_format=True)
            cleaned.append(dst)

        if not cleaned:
            raise ValueError("ZIP contains no image DICOM objects")
        if len(original_study_uids) != 1:
            raise ValueError("ZIP must contain one DICOM study")
        if len(original_series_uids) != 1:
            raise ValueError(
                "ZIP must contain exactly one DICOM series; upload each series separately"
            )
        if len(modalities) != 1:
            raise ValueError("ZIP must contain exactly one imaging modality")

        output = work / "cleaned.zip"
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            for item in cleaned:
                archive.write(item, arcname=item.name)

        shutil.copy2(output, path)

        modality = next(iter(modalities))

        return {
            "modality": modality,
            "rows": int(rows) if rows is not None else None,
            "columns": int(columns) if columns is not None else None,
            "slice_count": len(cleaned),
        }
