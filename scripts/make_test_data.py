"""Synthetic Pinnacle-style DICOM for testing VAIDER-mask. All names are invented.
Usage: python make_test_data.py <outdir>
Creates 5 patient folders: dicom_output_N + rtstruct_N.dcm (+ ImageSet_N.DICOM reference copies with a different frame),
a dog named Max with a structure called Maxilla, an owner named Ana with a structure called PLANAR, a short series,
a series with a missing orientation tag, a label/axes mismatch, and a structure set whose frame matches no CT."""
import sys, numpy as np
from pathlib import Path
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.uid import generate_uid, ExplicitVRLittleEndian

def mk(path, cls, mod, sop):
    m = FileMetaDataset(); m.TransferSyntaxUID = ExplicitVRLittleEndian; m.MediaStorageSOPClassUID = cls; m.MediaStorageSOPInstanceUID = sop
    ds = FileDataset(str(path), {}, file_meta=m, preamble=b"\0" * 128); ds.is_little_endian, ds.is_implicit_VR = True, False
    ds.SOPClassUID, ds.SOPInstanceUID, ds.Modality = cls, sop, mod; return ds

def common(ds, name, pid, owner, study, series):
    ds.PatientName, ds.PatientID, ds.ResponsiblePerson, ds.PatientBirthDate = name, pid, owner, "20150301"
    ds.PatientSpeciesDescription, ds.PatientBreedDescription, ds.PatientSex, ds.PatientAge = "Canine", "Labrador", "M", "009Y"
    ds.StudyInstanceUID, ds.SeriesInstanceUID = study, series; ds.StudyDate = ds.SeriesDate = "20240110"
    ds.AccessionNumber = "ACC" + pid; ds.StationName = "CTROOM1"; ds.OperatorsName = "Tech^Pat"; ds.PerformingPhysicianName = "Doc^Who"

def ct(path, name, pid, owner, study, series, frame, pos, iop, z, ce=False, drop=(), sop=None):
    sop = sop or generate_uid(); ds = mk(path, "1.2.840.10008.5.1.4.1.1.2", "CT", sop); common(ds, name, pid, owner, study, series)
    ds.FrameOfReferenceUID = frame; ds.InstanceNumber = str(int(z) + 1); ds.SliceThickness = "2"
    ds.StudyDescription = f"Head CT {name.split('^')[0]} {owner.split('^')[0]}"
    if pos: ds.PatientPosition = pos
    if iop: ds.ImageOrientationPatient = iop
    ds.ImagePositionPatient = [0, 0, float(z)]; ds.PixelSpacing = [1, 1]
    if ce: ds.ContrastBolusAgent = "Iohexol"
    ds.add_new(0x00110010, "LO", "VENDOR"); ds.add_new(0x00111001, "LO", name)
    ds.Rows = ds.Columns = 16; ds.SamplesPerPixel = 1; ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 16, 15, 0
    ds.PixelData = (np.arange(256, dtype=np.uint16) * (int(z) + 7)).tobytes()
    for k in drop:
        if k in ds: del ds[k]
    ds.save_as(path); return sop

def rs(path, name, pid, owner, study, frame, refs, rois):
    sop = generate_uid(); ds = mk(path, "1.2.840.10008.5.1.4.1.1.481.3", "RTSTRUCT", sop); common(ds, name, pid, owner, study, generate_uid())
    ds.StudyDate = "20240111"; ds.StructureSetLabel = f"Plan {name.split('^')[0]}"; ds.FrameOfReferenceUID = frame
    f = Dataset(); f.FrameOfReferenceUID = frame; ds.ReferencedFrameOfReferenceSequence = [f]
    ds.StructureSetROISequence, ds.ROIContourSequence = [], []
    for k, r in enumerate(rois, 1):
        a = Dataset(); a.ROINumber = k; a.ROIName = r; a.ReferencedFrameOfReferenceUID = frame; ds.StructureSetROISequence.append(a)
        c = Dataset(); c.ReferencedROINumber = k; cs = Dataset(); cs.ContourData = [0, 0, 0, 1, 1, 1, 2, 2, 2]; c.ContourSequence = [cs]; ds.ROIContourSequence.append(c)
    items = []
    for u in refs:
        i = Dataset(); i.ReferencedSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"; i.ReferencedSOPInstanceUID = u; items.append(i)
    c = Dataset(); c.ReferencedSOPClassUID = "1.2.840.10008.3.1.2.3.1"; c.ReferencedSOPInstanceUID = study; c.ContourImageSequence = items
    ds.ReferencedStudySequence = [c]; ds.save_as(path)

HFS, FFS = [1, 0, 0, 0, 1, 0], [-1, 0, 0, 0, 1, 0]
def make(root, n_slices=5, prefix="VET"):
    root = Path(root)
    cases = [  # name, pid, owner, patient folder, rois, special
        ("Max^Rover", prefix + "1001", "Ana^Smith", "Patient_101", ["Maxilla", "PLANAR", "Brain", "GTV"], {}),
        ("Bella^Rover", prefix + "1002", "Bob^Jones", "Patient_102", ["Liver", "Kidney_L"], {"mismatch": True}),
        ("Coco^Dog", prefix + "1003", "Lee^Ann", "Patient_103", ["Lung", "Heart"], {"short": True}),
        ("Duke^Dog", prefix + "1004", "Kim^Lou", "Patient_104", ["Femur", "Tibia"], {"noiop": True}),
        ("Echo^Dog", prefix + "1005", "Ray^Sue", "Patient_105", ["Brain", "Skull"], {"badframe": True})]
    for name, pid, ow, folder, rois, sp in cases:
        d = root / folder; d.mkdir(parents=True, exist_ok=True)
        for idx, ce in ((0, False), (1, True)):
            if idx == 1 and sp: continue
            st, se, fr = generate_uid(), generate_uid(), generate_uid(); out = d / f"dicom_output_{idx}"; out.mkdir(); refs = []
            ns = 2 if sp.get("short") else n_slices
            for z in range(ns):
                refs.append(ct(out / f"s{z}.dcm", name, pid, ow, st, se, fr, "HFS" if not sp.get("noiop") else None,
                               FFS if sp.get("mismatch") else (None if sp.get("noiop") and z == 0 else HFS), z, ce))
            rs(d / f"rtstruct_{idx}.dcm", name, pid, ow, st, generate_uid() if sp.get("badframe") else fr, refs, rois)
            if idx == 0:   # Pinnacle ImageSet copy: same SOPs, different frame of reference (must never reach the output)
                isd = d / "ImageSet_0.DICOM"; isd.mkdir(); other = generate_uid()
                for z in range(ns): ct(isd / f"s{z}.dcm", name, pid, ow, st, se, other, "HFS", HFS, z, ce, sop=refs[z])
        (d / "notes.txt").write_text("not dicom")
if __name__ == "__main__": make(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 5, sys.argv[3] if len(sys.argv) > 3 else "VET")
