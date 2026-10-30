#!/usr/bin/env python3
"""De-identify veterinary DICOM into a new folder. Never modifies the source.
Dry run unless --execute. Requires pydicom (uv add pydicom)."""
import argparse, csv, hashlib, hmac, os, re, secrets, sys
from pathlib import Path
import pydicom
from pydicom.uid import UID

ap = argparse.ArgumentParser()
ap.add_argument("--src", required=True); ap.add_argument("--out", required=True)
ap.add_argument("--key-dir", required=True)
ap.add_argument("--site-id", required=True, help="one digit, e.g. 1")
ap.add_argument("--site-label", required=True, help="e.g. Site1")
ap.add_argument("--physician", default="BigRed^Dr")
ap.add_argument("--institution", default="CornellRT")
ap.add_argument("--keep-private", action="store_true")
ap.add_argument("--execute", action="store_true")
a = ap.parse_args()

src, out, keyd = Path(a.src).resolve(), Path(a.out).resolve(), Path(a.key_dir).resolve()
def inside(p, q): return q == p or q in p.parents
if inside(out, src) or inside(src, out): sys.exit("Output and source must be separate folders.")
if inside(keyd, out) or inside(keyd, src) or inside(out, keyd): sys.exit("Key folder must be outside source and output.")
if not re.fullmatch(r"\d", a.site_id): sys.exit("--site-id must be one digit")

REMOVE = ["OtherPatientIDs","OtherPatientNames","PatientAddress","PatientTelephoneNumbers","PatientMotherBirthName",
 "ResponsiblePerson","ResponsiblePersonRole","ResponsibleOrganization","PatientComments","AdditionalPatientHistory",
 "MedicalRecordLocator","PerformingPhysicianName","OperatorsName","PhysiciansOfRecord","NameOfPhysiciansReadingStudy",
 "RequestingPhysician","InstitutionAddress","InstitutionalDepartmentName","StationName","DeviceSerialNumber",
 "PatientBirthDate","PatientBirthTime","ReviewerName","IssuerOfPatientID","RequestAttributesSequence",
 "ReferringPhysicianTelephoneNumbers","ReferringPhysicianAddress","StudyID","AccessionNumber","PerformedProcedureStepID"]
UID_KW = {"StudyInstanceUID","SeriesInstanceUID","SOPInstanceUID","FrameOfReferenceUID","ReferencedSOPInstanceUID",
 "ReferencedFrameOfReferenceUID","RelatedFrameOfReferenceUID","ReferencedStudyInstanceUID","ReferencedSeriesInstanceUID",
 "MediaStorageSOPInstanceUID","StorageMediaFileSetUID","ConcatenationUID","SynchronizationFrameOfReferenceUID"}
TEXT_VR = {"LO","SH","ST","LT","UT","PN","UC"}
KEYF, SECF = keyd / "deid_key.csv", keyd / "deid_secret.bin"
secret = SECF.read_bytes() if SECF.exists() else secrets.token_bytes(32)   # reused so later batches match earlier ones
known, known_off = {}, {}
if KEYF.exists():
    for r in csv.DictReader(open(KEYF)):
        known[r["original_PatientID"]] = int(r["new_PatientID"][-3:]); known_off[r["original_PatientID"]] = int(r["date_offset_days"])
uid_cache = {}
def new_uid(u):
    if u not in uid_cache:
        h = hmac.new(secret, str(u).encode(), hashlib.sha256).hexdigest()
        uid_cache[u] = UID("2.25." + str(int(h[:30], 16)))
    return uid_cache[u]

files = [p for p in src.rglob("*") if p.is_file() and not p.name.startswith(".")]
meta, patients = [], dict(known)
for p in sorted(files):
    try: ds = pydicom.dcmread(p, force=False)
    except Exception: continue
    pid = str(ds.get("PatientID", "")); meta.append((p, ds)); patients.setdefault(pid, max(patients.values(), default=0) + 1)
print(f"DICOM files: {len(meta)}   patients: {len(patients)}   other files skipped: {len(files)-len(meta)}")
if not meta: sys.exit("No DICOM found.")

def tokens(ds):
    t = set()
    for k in ("PatientName","ResponsiblePerson","ReferringPhysicianName","PatientID","OtherPatientIDs"):
        v = ds.get(k)
        for w in re.split(r"[\^\s,;/_-]+", str(v) if v is not None else ""):
            if len(w) >= 3: t.add(w)
    return t

def walk(ds, fn):
    for el in list(ds):
        if el.VR == "SQ":
            for item in el.value: walk(item, fn)
        fn(ds, el)

summary = {"burned_in": 0, "text_hits": 0, "private_removed": 0}
def process(ds, pid_old, n, offset, toks):
    new_pid = f"Z00{a.site_id}{n:03d}"
    from datetime import datetime, timedelta
    def fn(d, el):
        kw = el.keyword
        if kw in UID_KW and el.VR == "UI" and el.value:
            el.value = [str(new_uid(v)) for v in el.value] if el.VM > 1 else str(new_uid(el.value))
        elif el.VR == "DA" and el.value:
            vals = el.value if el.VM > 1 else [el.value]
            res = []
            for v in vals:
                try: res.append((datetime.strptime(str(v), "%Y%m%d") + timedelta(days=offset)).strftime("%Y%m%d"))
                except Exception: res.append("")
            el.value = res if el.VM > 1 else res[0]
        elif el.VR == "DT" and el.value:
            try: el.value = (datetime.strptime(str(el.value)[:8], "%Y%m%d") + timedelta(days=offset)).strftime("%Y%m%d")
            except Exception: el.value = ""
        elif el.VR in TEXT_VR and el.value and kw not in ("PatientName","PatientID","ReferringPhysicianName","InstitutionName"):
            s = str(el.value); s2 = s
            for t in toks: s2 = re.sub(re.escape(t), "REDACTED", s2, flags=re.I)
            if s2 != s: summary["text_hits"] += 1; el.value = s2
    walk(ds, fn)
    for kw in REMOVE:
        if kw in ds: del ds[kw]
    ds.PatientName = f"{a.site_label}_{n:03d}^CornellRT"; ds.PatientID = new_pid
    ds.ReferringPhysicianName = a.physician; ds.InstitutionName = a.institution
    if not a.keep_private:
        before = sum(1 for e in ds.iterall() if e.tag.is_private)
        ds.remove_private_tags(); summary["private_removed"] += before
    ds.PatientIdentityRemoved = "YES"; ds.DeidentificationMethod = "VAIDER lab-deidentify v0.1"
    if str(ds.get("BurnedInAnnotation", "")).upper() == "YES": summary["burned_in"] += 1
    return new_pid

DETACHED_STUDY = "1.2.840.10008.3.1.2.3.1"
def image_refs(ds):
    """Referenced instance UIDs that should exist in the set (study-level references excluded)."""
    r = set()
    def f(d, el):
        if el.keyword == "ReferencedSOPInstanceUID" and el.value and str(d.get("ReferencedSOPClassUID", "")) != DETACHED_STUDY:
            r.add(str(el.value))
    walk(ds, f); return r
rows, keyrows = [], []
offsets = {pid: known_off.get(pid) or (secrets.randbelow(364) + 1) for pid in patients}
sop_out = set(); refs = set(); pending = []
for p, ds in meta:
    pid_old = str(ds.get("PatientID", "")); n = patients[pid_old]
    pix_before = hashlib.sha256(ds.PixelData).hexdigest() if "PixelData" in ds else ""
    toks = tokens(ds)
    old_sop = ds.get("SOPInstanceUID", "")
    new_pid = process(ds, pid_old, n, offsets[pid_old], toks)
    mod = str(ds.get("Modality", "OT"))
    pix_after = hashlib.sha256(ds.PixelData).hexdigest() if "PixelData" in ds else ""
    assert pix_before == pix_after, "pixel data changed"
    sop_out.add(str(ds.SOPInstanceUID))
    refs |= image_refs(ds)
    dest = out / new_pid / mod / f"{ds.SOPInstanceUID}.dcm"
    rows.append([str(dest.relative_to(out)), mod, new_pid, "YES" if str(ds.get("BurnedInAnnotation","")).upper()=="YES" else "", ""])
    pending.append((dest, ds, toks))
    keyrows.append([pid_old, new_pid, offsets[pid_old]])

unresolved = refs - sop_out
def leftover(ds, toks):
    for e in ds.iterall():
        if e.VR in ("SQ","OB","OW","OF","OD","OL","UN") or e.value is None or e.keyword in ("PatientName","PatientID"): continue
        v = str(e.value).lower()
        if any(t.lower() in v for t in toks): return True
    return False
bad = []
if not a.execute:
    bad = [d for d, ds, t in pending if leftover(ds, t)]
print(f"Patients -> {', '.join(sorted({r[2] for r in rows}))}")
print(f"Private elements removed: {summary['private_removed']}   free-text redactions: {summary['text_hits']}   burned-in flagged files: {summary['burned_in']}")
print(f"References not found in this set: {len(unresolved)}   leftover identifier text (in memory): {len(bad)}")
if not a.execute:
    sys.exit("Dry run: nothing written. Add --execute to write the output and key file.")
for d, ds, t in pending:
    d.parent.mkdir(parents=True, exist_ok=True); ds.save_as(d)
disk_sops, disk_refs = set(), set()
for d, ds0, t in pending:                      # verify from what is on disk
    chk = pydicom.dcmread(d)
    disk_sops.add(str(chk.SOPInstanceUID))
    disk_refs |= image_refs(chk)
    if leftover(chk, t): bad.append(d)
unresolved = disk_refs - disk_sops
print(f"On-disk check: unresolved references {len(unresolved)}; files with leftover identifier text {len(bad)}")
keyd.mkdir(parents=True, exist_ok=True); out.mkdir(parents=True, exist_ok=True)
allrows = {(o, f"Z00{a.site_id}{n:03d}", offsets[o]) for o, n in patients.items()}
with open(KEYF, "w", newline="") as f:
    w = csv.writer(f); w.writerow(["original_PatientID", "new_PatientID", "date_offset_days"]); w.writerows(sorted(allrows))
SECF.write_bytes(secret); os.chmod(KEYF, 0o600); os.chmod(SECF, 0o600)
with open(out / "_deid_log.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["file", "modality", "new_PatientID", "burned_in_annotation", "leftover"]); w.writerows(rows)
print(f"Wrote {len(rows)} files to {out}. Key file in {keyd} (identifying: store apart from the data).")
if unresolved or bad: sys.exit("Completed with warnings: review before use.")
print("Verification passed. Manual check and second-person sign-off still required.")
