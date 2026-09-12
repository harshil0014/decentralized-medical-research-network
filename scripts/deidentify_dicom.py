import sys
from pathlib import Path

import pydicom
from pydicom.uid import generate_uid

src = Path(sys.argv[1])
dst = Path(sys.argv[2])

ds = pydicom.dcmread(src)

REMOVE = [
    "PatientName",
    "PatientID",
    "PatientBirthDate",
    "PatientSex",
    "PatientAddress",
    "PatientTelephoneNumbers",
    "OtherPatientIDsSequence",
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

for keyword in REMOVE:
    if keyword in ds:
        del ds[keyword]

# Strip private vendor tags
ds.remove_private_tags()

# Replace identifying UIDs
ds.StudyInstanceUID = generate_uid()
ds.SeriesInstanceUID = generate_uid()
ds.SOPInstanceUID = generate_uid()

if "MediaStorageSOPInstanceUID" in ds.file_meta:
    ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID

ds.PatientIdentityRemoved = "YES"
ds.DeidentificationMethod = (
    "Prototype PHI removal + private tags + UID remap"
)

ds.save_as(dst, enforce_file_format=True)

safe = {
    "modality": getattr(ds, "Modality", None),
    "study_description": getattr(ds, "StudyDescription", None),
    "series_description": getattr(ds, "SeriesDescription", None),
    "rows": getattr(ds, "Rows", None),
    "columns": getattr(ds, "Columns", None),
}

print("DEIDENTIFIED:", dst)
print("SAFE METADATA:", safe)
print("PatientIdentityRemoved:", ds.PatientIdentityRemoved)
