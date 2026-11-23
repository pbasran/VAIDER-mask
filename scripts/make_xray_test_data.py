"""Synthetic veterinary radiographs (DICOM) for testing VAIDER-mask in X-ray mode. All names are invented.
Usage: python make_xray_test_data.py <outdir>

Creates 4 animals plus two files that are not radiographs:
  Clinic_A/Biscotto  DX thorax, lateral + ventrodorsal. Owner, clinic, vet in free text, plate / detector / X-ray room IDs,
                     a private tag, the dog's name inside a nested sequence. Must be written, with all of that removed.
  Clinic_A/Nala      CR thorax: one clean image (raw file: no preamble, no DICM, '.v2' name), one image marked
                     BurnedInAnnotation=YES (must be skipped) and one screenshot (must be skipped).
  Clinic_A/Oscar     two images, both BurnedInAnnotation=YES: nothing to write, so the animal gets no ID.
  Clinic_B/Pippa     intra-oral dental radiograph (IO). Must be written.
  Clinic_B/other     a CT slice and a presentation state carrying the patient name: both skipped in X-ray mode.
A run with default settings writes 4 images for 3 animals; 3 burned-in images, 1 screenshot and 2 other files are skipped."""
import sys, numpy as np, pydicom
from pathlib import Path
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.uid import generate_uid, ExplicitVRLittleEndian

DX, CR, IO, SC = "1.2.840.10008.5.1.4.1.1.1.1", "1.2.840.10008.5.1.4.1.1", "1.2.840.10008.5.1.4.1.1.1.3", "1.2.840.10008.5.1.4.1.1.7"

def image(path, cls, mod, name, pid, owner, clinic, study, view="", body="CHEST", burned=None, raw=False, extra=None):
    sop = generate_uid(); m = FileMetaDataset(); m.TransferSyntaxUID = ExplicitVRLittleEndian; m.MediaStorageSOPClassUID = cls; m.MediaStorageSOPInstanceUID = sop
    ds = FileDataset(str(path), {}, file_meta=m, preamble=b"\0" * 128); ds.is_little_endian, ds.is_implicit_VR = True, False
    ds.SOPClassUID, ds.SOPInstanceUID, ds.Modality = cls, sop, mod
    ds.PatientName, ds.PatientID, ds.ResponsiblePerson, ds.PatientBirthDate = name, pid, owner, "20180502"
    ds.PatientSpeciesDescription, ds.PatientBreedDescription, ds.PatientSex, ds.PatientAge = "Canine", "Beagle", "F", "006Y"
    ds.PatientTelephoneNumbers = "+39 049 555 0101"; ds.PatientAddress = "Via Roma 12, Padova"
    ds.InstitutionName, ds.InstitutionAddress, ds.StationName = clinic, "Viale Venezia 5", "XR-ROOM2"
    ds.StudyInstanceUID, ds.SeriesInstanceUID = study, generate_uid(); ds.StudyDate = ds.SeriesDate = ds.AcquisitionDate = "20240611"
    ds.StudyTime = "101500"; ds.AccessionNumber = "ACC" + pid; ds.StudyID = "4471"
    ds.ReferringPhysicianName = "Conti^Marco"; ds.OperatorsName = "Greco^Sara"
    ds.StudyDescription = f"Torace {name.split('^')[0]} - ref Dr Conti"
    ds.PlateID, ds.CassetteID, ds.DetectorID = "PLATE-0091", "CAS-17", "DET-55A1"
    ds.PerformedStationName, ds.PerformedLocation, ds.DeviceSerialNumber = "XR-ROOM2", "Radiology wing B", "SN-99812"
    if view: ds.ViewPosition = view
    if body: ds.BodyPartExamined = body
    if burned is not None: ds.BurnedInAnnotation = burned
    ds.Rows = ds.Columns = 32; ds.SamplesPerPixel = 1; ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 12, 11, 0
    ds.PixelData = (np.arange(1024, dtype=np.uint16) * 3 % 4000).tobytes(); ds.ImagerPixelSpacing = [0.15, 0.15]
    ds.add_new(0x00191010, "LO", "XRVENDOR"); ds.add_new(0x00191011, "LO", f"{name} {owner}")
    it = Dataset(); it.CodeValue = "XR1"; it.CodingSchemeDesignator = "99VET"; it.CodeMeaning = f"{name.split('^')[0]} thorax 2 views"
    ds.ProcedureCodeSequence = [it]
    for k, v in (extra or {}).items(): setattr(ds, k, v)
    path.parent.mkdir(parents=True, exist_ok=True); ds.save_as(path)
    if raw:
        r = pydicom.dcmread(path); r.preamble = None; del r.file_meta
        pydicom.dcmwrite(path, r, enforce_file_format=False, implicit_vr=True, little_endian=True)

def make(root):
    root = Path(root)
    st = generate_uid(); a = ("Biscotto^Ferrari", "VX1001", "Ferrari^Giulia", "Clinica Veterinaria San Marco", st)
    image(root / "Clinic_A/Biscotto/LL.dcm", DX, "DX", *a, view="LL", burned="NO")
    image(root / "Clinic_A/Biscotto/VD.dcm", DX, "DX", *a, view="VD")
    st = generate_uid(); b = ("Nala^Russo", "VX1002", "Russo^Luca", "Clinica Veterinaria San Marco", st)
    image(root / "Clinic_A/Nala/img_001.v2", CR, "CR", *b, view="LL", raw=True)
    image(root / "Clinic_A/Nala/img_002.dcm", CR, "CR", *b, view="VD", burned="YES")
    image(root / "Clinic_A/Nala/screen.dcm", SC, "CR", *b, extra={"ImageType": ["DERIVED", "SECONDARY", "SCREEN SAVE"]})
    st = generate_uid(); c = ("Oscar^Bruno", "VX1003", "Bruno^Elena", "Clinica Veterinaria San Marco", st)
    image(root / "Clinic_A/Oscar/LL.dcm", DX, "DX", *c, view="LL", burned="YES")
    image(root / "Clinic_A/Oscar/VD.dcm", DX, "DX", *c, view="VD", burned="YES")
    st = generate_uid(); d = ("Pippa^Gallo", "VX2001", "Gallo^Anna", "Ambulatorio Dentale Gallo", st)
    image(root / "Clinic_B/Pippa/dental.dcm", IO, "IO", *d, body="JAW", burned="NO")
    image(root / "Clinic_B/other/ct_slice.dcm", "1.2.840.10008.5.1.4.1.1.2", "CT", "Rex^Marino", "VX2002", "Marino^Paolo", "Ambulatorio Dentale Gallo", generate_uid(), body="HEAD")
    pr = Dataset(); m = FileMetaDataset(); m.TransferSyntaxUID = ExplicitVRLittleEndian; m.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.11.1"
    m.MediaStorageSOPInstanceUID = generate_uid(); pr = FileDataset(str(root / "Clinic_B/other/annotations.dcm"), {}, file_meta=m, preamble=b"\0" * 128)
    pr.SOPClassUID, pr.SOPInstanceUID, pr.Modality = m.MediaStorageSOPClassUID, m.MediaStorageSOPInstanceUID, "PR"
    pr.PatientName, pr.PatientID, pr.StudyInstanceUID, pr.SeriesInstanceUID = "Pippa^Gallo", "VX2001", st, generate_uid()
    pr.ContentLabel = "PIPPA"; pr.save_as(root / "Clinic_B/other/annotations.dcm")
    (root / "Clinic_B/readme.txt").write_text("not dicom")

if __name__ == "__main__": make(sys.argv[1])
