from pathlib import Path
from zipfile import ZipFile
import tempfile

import pydicom
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import ExplicitVRLittleEndian, MRImageStorage, generate_uid

root = Path("demo_dicom/series")
root.mkdir(parents=True, exist_ok=True)

raw_dir = root / "raw"
clean_dir = root / "clean"
raw_dir.mkdir(exist_ok=True)
clean_dir.mkdir(exist_ok=True)

study_uid = generate_uid()
series_uid = generate_uid()

# ---------- CREATE 3-SLICE SYNTHETIC SERIES ----------
for i in range(1, 4):
    path = raw_dir / f"slice_{i}.dcm"

    meta = Dataset()
    meta.MediaStorageSOPClassUID = MRImageStorage
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.ImplementationClassUID = generate_uid()

    ds = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)

    ds.SOPClassUID = MRImageStorage
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID

    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid

    ds.PatientName = "SERIES^TEST"
    ds.PatientID = "SERIES-SECRET-123"
    ds.PatientBirthDate = "19900101"
    ds.InstitutionName = "Demo Hospital"

    ds.Modality = "MR"
    ds.StudyDescription = "Synthetic Brain MRI"
    ds.SeriesDescription = "Synthetic T1 Series"

    ds.InstanceNumber = i
    ds.Rows = 2
    ds.Columns = 2
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.PixelData = bytes([i, i + 1, i + 2, i + 3])

    ds.save_as(path, enforce_file_format=True)

print("RAW SERIES CREATED:", len(list(raw_dir.glob("*.dcm"))), "slices")

# ---------- CONSISTENT DE-ID ----------
uid_map = {}

def remap(uid):
    uid = str(uid)
    if uid not in uid_map:
        uid_map[uid] = generate_uid()
    return uid_map[uid]

sensitive = [
    "PatientName",
    "PatientID",
    "PatientBirthDate",
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
]

for src in sorted(raw_dir.glob("*.dcm")):
    ds = pydicom.dcmread(src)

    for keyword in sensitive:
        if keyword in ds:
            del ds[keyword]

    ds.remove_private_tags()

    if "StudyInstanceUID" in ds:
        ds.StudyInstanceUID = remap(ds.StudyInstanceUID)

    if "SeriesInstanceUID" in ds:
        ds.SeriesInstanceUID = remap(ds.SeriesInstanceUID)

    if "FrameOfReferenceUID" in ds:
        ds.FrameOfReferenceUID = remap(ds.FrameOfReferenceUID)

    ds.SOPInstanceUID = generate_uid()

    if "MediaStorageSOPInstanceUID" in ds.file_meta:
        ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID

    ds.PatientIdentityRemoved = "YES"
    ds.DeidentificationMethod = "Prototype series PHI removal + UID remap"

    # Do not preserve potentially identifying filenames
    dst = clean_dir / f"slice_{int(ds.InstanceNumber):04d}.dcm"
    ds.save_as(dst, enforce_file_format=True)

# ---------- VERIFY ----------
clean = [pydicom.dcmread(p) for p in sorted(clean_dir.glob("*.dcm"))]

study_uids = {str(ds.StudyInstanceUID) for ds in clean}
series_uids = {str(ds.SeriesInstanceUID) for ds in clean}
sop_uids = {str(ds.SOPInstanceUID) for ds in clean}

remaining = []
for ds in clean:
    for keyword in sensitive:
        if keyword in ds:
            remaining.append(keyword)

print("CLEAN SLICES:", len(clean))
print("SENSITIVE TAGS REMAINING:", sorted(set(remaining)))
print("UNIQUE STUDY UIDs:", len(study_uids))
print("UNIQUE SERIES UIDs:", len(series_uids))
print("UNIQUE SOP UIDs:", len(sop_uids))
print("MODALITY:", clean[0].Modality)

assert len(clean) == 3
assert remaining == []
assert len(study_uids) == 1
assert len(series_uids) == 1
assert len(sop_uids) == 3
assert all(ds.PatientIdentityRemoved == "YES" for ds in clean)

zip_path = root / "deidentified_series.zip"

with ZipFile(zip_path, "w") as z:
    for p in sorted(clean_dir.glob("*.dcm")):
        z.write(p, arcname=p.name)

print("ZIP CREATED:", zip_path)
print("DICOM SERIES DE-IDENTIFICATION: PASS")
