from pathlib import Path
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import ExplicitVRLittleEndian, MRImageStorage, generate_uid

out = Path("demo_dicom/raw_test.dcm")

file_meta = Dataset()
file_meta.MediaStorageSOPClassUID = MRImageStorage
file_meta.MediaStorageSOPInstanceUID = generate_uid()
file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
file_meta.ImplementationClassUID = generate_uid()

ds = FileDataset(str(out), {}, file_meta=file_meta, preamble=b"\0" * 128)

ds.SOPClassUID = MRImageStorage
ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
ds.StudyInstanceUID = generate_uid()
ds.SeriesInstanceUID = generate_uid()

# Deliberate FAKE PHI for de-identification testing
ds.PatientName = "TEST^PATIENT"
ds.PatientID = "SECRET-123"
ds.PatientBirthDate = "19900101"
ds.PatientSex = "M"
ds.InstitutionName = "Demo Hospital"
ds.ReferringPhysicianName = "DOCTOR^TEST"
ds.AccessionNumber = "ACC-SECRET-001"
ds.StudyID = "STUDY-SECRET-001"

# Safe imaging metadata
ds.Modality = "MR"
ds.StudyDescription = "Synthetic Brain MRI"
ds.SeriesDescription = "Synthetic T1"
ds.Rows = 2
ds.Columns = 2
ds.SamplesPerPixel = 1
ds.PhotometricInterpretation = "MONOCHROME2"
ds.BitsAllocated = 8
ds.BitsStored = 8
ds.HighBit = 7
ds.PixelRepresentation = 0
ds.PixelData = bytes([0, 64, 128, 255])

ds.save_as(out, enforce_file_format=True)

print("CREATED:", out)
print("PatientName:", ds.PatientName)
print("PatientID:", ds.PatientID)
print("Institution:", ds.InstitutionName)
print("Modality:", ds.Modality)
