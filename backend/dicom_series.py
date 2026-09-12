from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import shutil
import tempfile

import pydicom
from pydicom.uid import generate_uid


SENSITIVE_KEYWORDS = [
    "PatientName",
    "PatientID",
    "PatientBirthDate",
    "PatientBirthTime",
    "PatientSex",
    "PatientAddress",
    "PatientTelephoneNumbers",
    "InstitutionName",
    "InstitutionAddress",
    "ReferringPhysicianName",
    "PerformingPhysicianName",
    "OperatorsName",
    "AccessionNumber",
    "StudyID",
    "StudyDate",
    "SeriesDate",
    "AcquisitionDate",
    "ContentDate",
]


def deidentify_dicom_series_zip(path: str | Path) -> dict:
    path = Path(path)

    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        raw_dir = work / "raw"
        clean_dir = work / "clean"
        raw_dir.mkdir()
        clean_dir.mkdir()

        with ZipFile(path, "r") as z:
            files = [x for x in z.infolist() if not x.is_dir()]

            if not files:
                raise ValueError("ZIP is empty")

            if len(files) > 2000:
                raise ValueError("Too many files in DICOM series ZIP")

            total_size = sum(x.file_size for x in files)
            if total_size > 2 * 1024 * 1024 * 1024:
                raise ValueError("DICOM ZIP is too large")

            for index, member in enumerate(files, start=1):
                name = Path(member.filename)

                if name.is_absolute() or ".." in name.parts:
                    raise ValueError("Unsafe ZIP path")

                target = raw_dir / f"input_{index:04d}.dcm"

                with z.open(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)

        uid_map = {}

        def remap(uid):
            uid = str(uid)
            if uid not in uid_map:
                uid_map[uid] = generate_uid()
            return uid_map[uid]

        cleaned = []
        modalities = set()
        original_study_uids = set()
        original_series_uids = set()

        study_description = None
        series_description = None
        rows = None
        columns = None

        for index, src in enumerate(sorted(raw_dir.glob("*.dcm")), start=1):
            try:
                ds = pydicom.dcmread(src)
            except Exception as exc:
                raise ValueError("ZIP contains invalid DICOM") from exc

            if "StudyInstanceUID" not in ds or "SeriesInstanceUID" not in ds:
                raise ValueError("DICOM slice missing Study/Series UID")

            original_study_uids.add(str(ds.StudyInstanceUID))
            original_series_uids.add(str(ds.SeriesInstanceUID))

            modality = getattr(ds, "Modality", None)
            if modality:
                modalities.add(str(modality))

            if study_description is None:
                study_description = getattr(ds, "StudyDescription", None)

            if series_description is None:
                series_description = getattr(ds, "SeriesDescription", None)

            if rows is None:
                rows = getattr(ds, "Rows", None)

            if columns is None:
                columns = getattr(ds, "Columns", None)

            for keyword in SENSITIVE_KEYWORDS:
                if keyword in ds:
                    del ds[keyword]

            ds.remove_private_tags()

            ds.StudyInstanceUID = remap(ds.StudyInstanceUID)
            ds.SeriesInstanceUID = remap(ds.SeriesInstanceUID)

            if "FrameOfReferenceUID" in ds:
                ds.FrameOfReferenceUID = remap(ds.FrameOfReferenceUID)

            ds.SOPInstanceUID = generate_uid()

            if "MediaStorageSOPInstanceUID" in ds.file_meta:
                ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID

            ds.PatientIdentityRemoved = "YES"
            ds.DeidentificationMethod = "Prototype series PHI removal + UID remap"

            dst = clean_dir / f"slice_{index:04d}.dcm"
            ds.save_as(dst, enforce_file_format=True)
            cleaned.append(dst)

        if len(original_study_uids) != 1:
            raise ValueError("ZIP must contain one DICOM study")

        if len(original_series_uids) != 1:
            raise ValueError("ZIP must contain one DICOM series")

        if len(modalities) > 1:
            raise ValueError("ZIP contains mixed modalities")

        output = work / "cleaned.zip"

        with ZipFile(output, "w", ZIP_DEFLATED) as z:
            for item in cleaned:
                z.write(item, arcname=item.name)

        shutil.copy2(output, path)

        modality = next(iter(modalities), None)

        return {
            "modality": modality,
            "study_description": str(study_description) if study_description else None,
            "series_description": str(series_description) if series_description else None,
            "rows": int(rows) if rows is not None else None,
            "columns": int(columns) if columns is not None else None,
            "slice_count": len(cleaned),
        }
