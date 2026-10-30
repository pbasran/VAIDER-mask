"""VAIDER-mask engine v0.3. No GUI, no network. Standard library + pydicom + numpy.

Input: one DICOM file, or a folder tree. Pinnacle-style patient folders (dicom_output_N + rtstruct_N.dcm, ImageSet_N.DICOM
reference copies) are understood; any other layout is grouped by DICOM series.

Output under --out:
  DEID_DATA/<NewPatientID>/set_<NN>/<MOD>/<MOD>_<nnnn>.dcm   one folder per scan set (CT series + its RTSTRUCT etc.)
  DEID_DATA/deid_index.csv, deid_sets.csv, run_report.json      new metadata only (no original identifiers)
CROSSWALK_RESTRICTED/ (or chosen folder), identifying, never share:
  crosswalk_patients.csv, crosswalk_sets.csv, crosswalk_files.csv, anonymization_report.csv, deid_secret.bin
"""
import csv, hashlib, hmac, json, os, re, secrets
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np
import pydicom
from pydicom.uid import UID

VERSION = "0.3.2"
DETACHED_STUDY = "1.2.840.10008.3.1.2.3.1"

@dataclass
class Options:
    src: str = ""
    out: str = ""
    crosswalk_dir: str = ""            # blank -> <out>/CROSSWALK_RESTRICTED
    source_by_folder: bool = False      # True: the top-level folder of each file is a separate clinic/source, so the same PatientID in two folders is two animals
    site_id: str = "1"
    site_label: str = "Site1"
    id_prefix: str = "Z00"
    name_suffix: str = "CornellRT"
    physician: str = "BigRed^Dr"
    institution: str = "CornellRT"
    remove_owner: bool = True
    remove_staff: bool = True
    remove_station: bool = True
    remove_birthdate: bool = True
    remove_other_ids: bool = True      # includes AccessionNumber
    remove_private: bool = True
    keep_vet_descriptors: bool = True
    redact_text: bool = True
    date_mode: str = "shift"           # shift | remove | keep
    orientation: str = "audit"         # audit | fill | fill_fix
    quadruped: bool = True             # AnatomicalOrientationType = QUADRUPED on image slices
    min_slices: int = 41               # distinct slice positions needed per image set (0 = no limit)
    skip_failed_sets: bool = True
    no_ce: bool = False                # dataset has no contrast-enhanced scans
    ce_report: str = ""                # optional set_ce_contour_report.txt
    operator: str = ""

OWNER = ["ResponsiblePerson","ResponsiblePersonRole","ResponsibleOrganization","PatientAddress","PatientTelephoneNumbers",
         "PatientMotherBirthName","PatientBirthName","OtherPatientIDs","OtherPatientNames","OtherPatientIDsSequence",
         "PatientComments","AdditionalPatientHistory","MedicalRecordLocator","BreedRegistrationSequence","PersonAddress",
         "PersonTelephoneNumbers","OrderCallbackPhoneNumber","ConfidentialityConstraintOnPatientDataDescription",
         "CurrentPatientLocation","PatientInstitutionResidence","RegionOfResidence","CountryOfResidence","MilitaryRank","BranchOfService"]
STAFF = ["ReferringPhysicianTelephoneNumbers","ReferringPhysicianAddress","PerformingPhysicianName","OperatorsName",
         "PhysiciansOfRecord","NameOfPhysiciansReadingStudy","RequestingPhysician","ReviewerName",
         "ReferringPhysicianIdentificationSequence","PerformingPhysicianIdentificationSequence",
         "OperatorIdentificationSequence","PhysiciansOfRecordIdentificationSequence"]
STATION = ["StationName","DeviceSerialNumber","InstitutionAddress","InstitutionalDepartmentName"]
BIRTH = ["PatientBirthDate","PatientBirthTime"]
OTHER_IDS = ["AccessionNumber","StudyID","IssuerOfPatientID","IssuerOfPatientIDQualifiersSequence",
             "PerformedProcedureStepID","RequestAttributesSequence"]
VET = ["PatientSpeciesDescription","PatientSpeciesCodeSequence","PatientBreedDescription","PatientBreedCodeSequence",
       "PatientSexNeutered","PatientSex","PatientAge","PatientSize","PatientWeight"]
UID_KW = {"StudyInstanceUID","SeriesInstanceUID","SOPInstanceUID","FrameOfReferenceUID","ReferencedSOPInstanceUID",
          "ReferencedFrameOfReferenceUID","RelatedFrameOfReferenceUID","ReferencedStudyInstanceUID",
          "ReferencedSeriesInstanceUID","MediaStorageSOPInstanceUID","StorageMediaFileSetUID","ConcatenationUID",
          "SynchronizationFrameOfReferenceUID","DeviceUID","DoseReferenceUID"}
TEXT_VR = {"LO","SH","ST","LT","UT","PN","UC"}
# Fields that are never text-redacted and never searched for leftovers (names we assign, and structure names).
NO_REDACT = {"PatientName","PatientID","ReferringPhysicianName","InstitutionName","ROIName","ROIObservationLabel"}
AUX = {"RTSTRUCT","RTPLAN","RTDOSE","RTRECORD","SR","PR","KO","REG","SEG"}
EXPECT = {"HFS": ((1,0,0),(0,1,0)), "HFP": ((-1,0,0),(0,-1,0)), "FFS": ((-1,0,0),(0,1,0)), "FFP": ((1,0,0),(0,-1,0))}
RE_CT = re.compile(r"^dicom_output_(\d+)$", re.I)
RE_RT = re.compile(r"^rt\w+_(\d+)\.dcm$", re.I)
RE_IS = re.compile(r"^ImageSet_(\d+)\.DICOM$", re.I)

REGIONS = [("Head/Neck", ["brain","skull","mandible","maxilla","oral cavity","oral","tongue","larynx","pharynx","thyroid","parotid",
   "submandibular","neck","nasal","nose","nasopharynx","orbit","eye","globe","brainstem","cochlea","optic","tmj","ear canal"," ear",
   "retropharyngeal","cervical ln","cervical lymph","oropharynx"]),
  ("Thorax", ["lung","heart","esophagus","trachea","chest","thorax","thoracic","rib","sternum","mediastinum","carina","bronch","pulmonary","cardiac"]),
  ("Abdomen/Pelvis", ["liver","kidney","spleen","stomach","bladder","prostate","uterus","vagina","colon","rectum","bowel","pancreas","pelvis",
   "pelvic","abdomen","abdominal","ureter","urethra","testicle","testis","ovary","anal","anus","perineum","iliac","inguinal","femoral head",
   "femoral neck","sacrum","vulva","scrotum","perianal","adrenal","duodenum"]),
  ("Limbs", ["radius","ulna","tibia","fibula","humerus","carpus","tarsus","metacarpal","metatarsal","paw","digit","stifle","elbow joint",
   "forelimb","hindlimb","hind limb","fore limb","limb","leg","foot","phalan","antebrachium","brachium","tarsal"])]
ROI_EXCLUDE = {"body","gtv","ctv","ptv","skin","bb","ln","bones","spinalcord","spinalcord prv","couch","external","ring","dose"}

class Cancelled(Exception): pass

def code_hash(): return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
def inside(p, q): return p == q or q in p.parents

def resolve_paths(o):
    src, out = Path(o.src).expanduser().resolve(), Path(o.out).expanduser().resolve()
    data = out / "DEID_DATA"
    xw = Path(o.crosswalk_dir).expanduser().resolve() if str(o.crosswalk_dir).strip() else out / "CROSSWALK_RESTRICTED"
    return src, out, data, xw

def validate(o):
    if not o.src.strip(): raise ValueError("Choose the source file or folder.")
    if not o.out.strip(): raise ValueError("Choose the output folder.")
    src, out, data, xw = resolve_paths(o)
    if not src.exists(): raise ValueError("Source not found.")
    srcdir = src if src.is_dir() else src.parent
    if inside(out, srcdir) or inside(srcdir, out): raise ValueError("Output and source must be separate folders.")
    if inside(xw, data): raise ValueError("The crosswalk folder cannot be inside DEID_DATA.")
    if inside(xw, srcdir) or inside(srcdir, xw): raise ValueError("The crosswalk folder must be separate from the source.")
    if not re.fullmatch(r"\d", str(o.site_id)): raise ValueError("Site number must be one digit.")
    if not re.fullmatch(r"[A-Za-z0-9]+", o.site_label): raise ValueError("Site label: letters and digits only.")
    if not re.fullmatch(r"[A-Za-z0-9]+", o.id_prefix): raise ValueError("ID prefix: letters and digits only.")
    if o.date_mode not in ("shift", "remove", "keep"): raise ValueError("Bad date mode.")
    if o.orientation not in ("audit", "fill", "fill_fix"): raise ValueError("Bad orientation mode.")
    try: o.min_slices = max(0, int(o.min_slices))
    except Exception: raise ValueError("Minimum slice positions must be a whole number.")
    if str(o.ce_report).strip() and not Path(o.ce_report).expanduser().is_file(): raise ValueError("CE report file not found.")

# ---------- small DICOM helpers ----------
def present(ds, kw):
    v = getattr(ds, kw, None)
    return v is not None and str(v).strip() not in ("", "[]")

def vec(v, n):
    try:
        t = tuple(float(x) for x in v)
        return t if len(t) == n else None
    except Exception: return None

def walk(ds, fn):
    for el in list(ds):
        if el.VR == "SQ":
            for item in el.value: walk(item, fn)
        fn(ds, el)

def image_refs(ds):
    r = set()
    def f(d, el):
        if el.keyword == "ReferencedSOPInstanceUID" and el.value and str(d.get("ReferencedSOPClassUID", "")) != DETACHED_STUDY:
            r.add(str(el.value))
    walk(ds, f); return r

def snap3(v):
    out = []
    for x in v:
        if abs(abs(x) - round(abs(x))) >= 0.1: return None
        out.append(int(round(x)))
    return tuple(out)

def orient_from(iop, pos):
    """(status, coordinate_label). Only HFS/HFP/FFS/FFP are auto-checked."""
    if not iop: return "no_iop", ""
    r, c = snap3(iop[0:3]), snap3(iop[3:6])
    coord = next((k for k, (er, ec) in EXPECT.items() if r == er and c == ec), "")
    if not pos: return "no_position", coord
    if pos not in EXPECT: return "not_checked", coord
    if not coord: return ("oblique" if (r is None or c is None) else "not_checked"), ""
    return ("ok" if coord == pos else "mismatch"), coord

def make_rx(tokens):
    toks = sorted((t for t in tokens if len(t) >= 3), key=len, reverse=True)
    if not toks: return None
    return re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(t) for t in toks) + r")(?![A-Za-z0-9])", re.I)

def _as_list(v):
    return list(v) if (hasattr(v, "__iter__") and not isinstance(v, (str, bytes)) and getattr(v, "VM", 1) != 1 and not hasattr(v, "family_name")) else [v]

def tokens_from(ds):
    """Words from names (split) and whole ID strings that must not survive in free text."""
    t = set()
    for k in ("PatientName", "ResponsiblePerson", "OtherPatientNames"):
        v = ds.get(k)
        if v is not None:
            for part in (v if isinstance(v, (list, tuple)) else [v]):
                for w in re.split(r"[\^\s,;/_\-.]+", str(part)):
                    if len(w) >= 3: t.add(w)
    for k in ("PatientID", "OtherPatientIDs"):
        v = ds.get(k)
        if v is not None:
            for part in (v if isinstance(v, (list, tuple)) else [v]):
                if len(str(part).strip()) >= 3: t.add(str(part).strip())
    return t

def redactable(el):
    """The ONE rule for which fields are redacted and which are searched at verification."""
    return el.VR in TEXT_VR and el.keyword not in NO_REDACT and el.value not in (None, "", [])

def text_hits(ds, rx):
    n = 0
    def f(d, el):
        nonlocal n
        if redactable(el) and el.VR != "SQ":
            vals = el.value if isinstance(el.value, (list, tuple)) or (hasattr(el.value, "__iter__") and not isinstance(el.value, (str, bytes)) and el.VM > 1) else [el.value]
            if any(rx.search(str(v)) for v in vals): n += 1
    walk(ds, f); return n

def active_roi_names(ds):
    status = {}
    for rc in getattr(ds, "ROIContourSequence", []):
        status[getattr(rc, "ReferencedROINumber", None)] = bool(getattr(rc, "ContourSequence", None))
    return [str(r.ROIName) for r in getattr(ds, "StructureSetROISequence", []) if getattr(r, "ROIName", None) and status.get(getattr(r, "ROINumber", None), False)]

def classify_region(names):
    scores, hit = {r: 0 for r, _ in REGIONS}, {}
    for raw in names:
        n = re.sub(r"[_\-]+", " ", str(raw).strip().lower()).strip()
        if n in ROI_EXCLUDE or n.startswith(("gtv", "ctv", "ptv")) or "spinal cord" in n or "spinalcord" in n: continue
        for r, kws in REGIONS:
            if any(k in n for k in kws): scores[r] += 1; break
    best = max(scores.values())
    if best == 0: return ""
    top = [r for r, v in scores.items() if v == best]
    return "Ambiguous" if len(top) > 1 else top[0]

def parse_ce_report(path):
    rep, pat, st = {}, None, None
    if not path or not Path(path).expanduser().is_file(): return rep
    for line in open(Path(path).expanduser(), encoding="utf-8", errors="replace"):
        line = line.strip()
        m = re.match(r"PATIENT:\s*(\S+)", line)
        if m: pat = m.group(1); rep.setdefault(pat, {}); st = None; continue
        m = re.match(r"Set\s+(\d+):", line)
        if m and pat: st = int(m.group(1)); continue
        m = re.match(r"CT scan type\s*:\s*(.+)", line)
        if m and pat and st is not None:
            t = m.group(1).strip(); rep[pat][st] = t if t in ("CE", "No CE") else "Unknown"
    return rep

# ---------- file discovery ----------
def list_files(src):
    """-> (dicom candidates, ImageSet reference files). ImageSet_N.DICOM folders are never part of the output."""
    if src.is_file(): return [src], []
    files, ref = [], []
    for root, dirs, fns in os.walk(src):
        keep = []
        for d in sorted(dirs):
            if d.startswith("."): continue
            if RE_IS.match(d):
                for r2, _, f2 in os.walk(Path(root) / d): ref += [Path(r2) / x for x in sorted(f2) if not x.startswith(".")]
            else: keep.append(d)
        dirs[:] = keep
        files += [Path(root) / f for f in sorted(fns) if not f.startswith(".") and f.upper() != "DICOMDIR"]
    return files, ref

def locate(p, srcdir):
    """-> (patient_folder, pinnacle_index_or_None)"""
    try: rel = p.relative_to(srcdir)
    except ValueError: return "", None
    parts = rel.parts
    if len(parts) >= 2 and RE_CT.match(parts[-2]):
        return (str(Path(*parts[:-2])) if len(parts) > 2 else srcdir.name), int(RE_CT.match(parts[-2]).group(1))
    m = RE_RT.match(parts[-1])
    if m: return (str(Path(*parts[:-1])) if len(parts) > 1 else srcdir.name), int(m.group(1))
    return (parts[0] if len(parts) > 1 else srcdir.name), None

def read_hdr(p):
    try: return pydicom.dcmread(p, stop_before_pixels=True), False
    except Exception: pass
    if p.suffix.lower() == ".dcm":
        try:
            ds = pydicom.dcmread(p, stop_before_pixels=True, force=True)
            if str(ds.get("Modality", "")): return ds, True
        except Exception: pass
    return None, False

# ---------- scan: headers -> plan ----------
def scan(o, progress=lambda *a: None, cancel=None):
    validate(o); src, out, data, xw = resolve_paths(o)
    srcdir = src if src.is_dir() else src.parent
    files, reffiles = list_files(src)
    entries, skipped = [], 0
    for i, p in enumerate(files):
        if cancel and cancel.is_set(): raise Cancelled()
        if i % 25 == 0: progress("scan", i, len(files), f"Reading headers {i}/{len(files)}")
        ds, forced = read_hdr(p)
        if ds is None: skipped += 1; continue
        pf, pidx = locate(p, srcdir)
        mod = str(ds.get("Modality", "OT")) or "OT"
        pid = str(ds.get("PatientID", "")).strip() or "NOID_" + hashlib.sha1(str(ds.get("StudyInstanceUID", p)).encode()).hexdigest()[:8]
        inst = " ".join(str(ds.get("InstitutionName", "") or "").split())
        top = ""
        if o.source_by_folder:
            try:
                rp = p.relative_to(srcdir).parts; top = rp[0] if len(rp) > 1 else ""
            except ValueError: pass
        src_id = "/".join(x for x in (inst, top) if x)          # clinic / source: institution name and, if asked, the top-level folder
        pkey = f"{src_id}||{pid}" if src_id else pid             # patients are matched on source + PatientID, never PatientID alone
        e = dict(path=p, forced=forced, pf=pf, pidx=pidx, pid=pid, pkey=pkey, src=src_id, pname=str(ds.get("PatientName", "")).strip(), mod=mod, sop=str(ds.get("SOPInstanceUID", "")),
                 series=str(ds.get("SeriesInstanceUID", "")), for_uid=str(ds.get("FrameOfReferenceUID", "") or ""),
                 iop=vec(ds.get("ImageOrientationPatient", []) or [], 6), ipp=vec(ds.get("ImagePositionPatient", []) or [], 3),
                 inst=int(ds.InstanceNumber) if present(ds, "InstanceNumber") and str(ds.InstanceNumber).lstrip("-").isdigit() else None,
                 pos=str(ds.get("PatientPosition", "") or "").strip(), thick=str(ds.get("SliceThickness", "") or ""),
                 refs=set(), fors=set(), rois=[], nroi_all=0, ce=False, burned=str(ds.get("BurnedInAnnotation", "")).upper() == "YES",
                 sdesc=str(ds.get("SeriesDescription", "")), sdate=str(ds.get("StudyDate", "")),
                 rows=str(ds.get("Rows", "")), cols=str(ds.get("Columns", "")), filled=set(), fixpos="", tokens=tokens_from(ds),
                 vet={k: str(ds.get(k, "")) for k in ("PatientSpeciesDescription", "PatientBreedDescription", "PatientSex", "PatientSexNeutered", "PatientAge")})
        e["ce"] = bool(str(ds.get("ContrastBolusAgent", "")).strip() or str(ds.get("ContrastBolusVolume", "")).strip() or str(ds.get("ContrastBolusRoute", "")).strip())
        if mod in AUX:
            e["refs"] = image_refs(ds)
            if mod == "RTSTRUCT":
                e["fors"] = {str(it.get("FrameOfReferenceUID", "")) for it in ds.get("ReferencedFrameOfReferenceSequence", [])}
                e["fors"] |= {str(it.get("ReferencedFrameOfReferenceUID", "")) for it in ds.get("StructureSetROISequence", [])}
                if e["for_uid"]: e["fors"].add(e["for_uid"])
                e["fors"].discard("")
                e["rois"] = active_roi_names(ds); e["nroi_all"] = len(ds.get("StructureSetROISequence", []))
            elif e["for_uid"]: e["fors"].add(e["for_uid"])
        entries.append(e)
    progress("scan", len(files), len(files), "Grouping scan sets")

    refhdrs = {}                                     # (patient_folder, N) -> ImageSet headers, read only when tags may be filled
    if o.orientation != "audit":
        for p in reffiles:
            ds, _ = read_hdr(p)
            if ds is None or str(ds.get("Modality", "")) in AUX: continue
            parts = p.relative_to(srcdir).parts
            i0 = next(i for i, x in enumerate(parts) if RE_IS.match(x))
            idx = int(RE_IS.match(parts[i0]).group(1)); pf = str(Path(*parts[:i0])) if i0 > 0 else srcdir.name
            refhdrs.setdefault((pf, idx), []).append(dict(sop=str(ds.get("SOPInstanceUID", "")),
                inst=str(ds.get("InstanceNumber", "")), pos=str(ds.get("PatientPosition", "") or "").strip(),
                iop=vec(ds.get("ImageOrientationPatient", []) or [], 6), ipp=vec(ds.get("ImagePositionPatient", []) or [], 3),
                for_uid=str(ds.get("FrameOfReferenceUID", "") or "")))

    ce_rep = parse_ce_report(o.ce_report) if str(o.ce_report).strip() else {}
    bypid = {}
    for e in entries: bypid.setdefault(e["pkey"], []).append(e)
    patients = []
    for pid in sorted(bypid):
        lst = bypid[pid]
        groups = {}
        for e in lst:
            if e["mod"] in AUX: continue
            gk = ("P", e["pf"], e["pidx"]) if e["pidx"] is not None else ("S", e["series"] or e["sop"])
            groups.setdefault(gk, []).append(e)
        sets = {gk: dict(gk=gk, images=imgs, aux=[], pf=imgs[0]["pf"], pidx=imgs[0]["pidx"]) for gk, imgs in groups.items()}
        orphans = []
        for a in lst:
            if a["mod"] not in AUX: continue
            tgt = sets.get(("P", a["pf"], a["pidx"])) if a["pidx"] is not None else None
            if tgt is None and a["refs"]:
                best = max(sets.values(), key=lambda s: len(a["refs"] & {x["sop"] for x in s["images"]}), default=None)
                if best and a["refs"] & {x["sop"] for x in best["images"]}: tgt = best
            if tgt is None and a["fors"]:
                tgt = next((s for s in sets.values() if a["fors"] & {x["for_uid"] for x in s["images"]}), None)
            if tgt is None: orphans.append(a)
            else: tgt["aux"].append(a)
        if orphans:
            sets[("A", pid)] = dict(gk=("A", pid), images=[], aux=orphans, pf=orphans[0]["pf"], pidx=orphans[0]["pidx"])
        slist = []
        for gk, s in sorted(sets.items(), key=lambda kv: (kv[1]["pf"], kv[1]["pidx"] if kv[1]["pidx"] is not None else 9999, str(kv[0]))):
            s["key"] = f"{s['pf']}|dicom_output_{s['pidx']}" if gk[0] == "P" else (f"{pid}|{gk[1]}" if gk[0] == "S" else f"{pid}|aux")
            s["label"] = f"dicom_output_{s['pidx']}" if gk[0] == "P" else (gk[1] if gk[0] == "S" else "aux-only")
            qc_set(o, s, refhdrs, ce_rep)
            slist.append(s)
        patients.append(dict(pid=lst[0]["pid"], pkey=pid, src=lst[0]["src"], pfs={e["pf"] for e in lst}, sets=slist, tokens=set().union(*[e["tokens"] for e in lst])))
    for p in patients:
        p["keep"] = [s for s in p["sets"] if s["pass"] or not o.skip_failed_sets]
    progress("scan", len(files), len(files), "Scan complete")
    return dict(patients=patients, skipped=skipped, imageset_files=len(reffiles), nfiles=len(files)), summarize(o, entries, patients, skipped, len(reffiles), len(files))

def interp_ipp(imgs, log):
    miss = [i for i, e in enumerate(imgs) if e["ipp"] is None]
    if not miss: return
    known = [(i, np.array(e["ipp"])) for i, e in enumerate(imgs) if e["ipp"] is not None]
    if len(known) >= 2:
        idx = np.array([k for k, _ in known], float); pos = np.array([p for _, p in known])
        co, *_ = np.linalg.lstsq(np.vstack([idx, np.ones(len(idx))]).T, pos, rcond=None)
        for i in miss: imgs[i]["ipp"] = tuple(round(float(x), 3) for x in co[0] * i + co[1]); imgs[i]["filled"].add("ipp")
        log.append(f"ipp:interpolated({len(miss)})")
    elif len(known) == 1:
        iop = next((e["iop"] for e in imgs if e["iop"]), None); th = next((float(e["thick"]) for e in imgs if e["thick"]), None)
        if iop and th:
            n = np.cross(iop[0:3], iop[3:6]); k0, p0 = known[0]
            for i in miss: imgs[i]["ipp"] = tuple(round(float(x), 3) for x in p0 + (i - k0) * th * n); imgs[i]["filled"].add("ipp")
            log.append(f"ipp:extrapolated({len(miss)})")

def fill_set(imgs, aux, refs, log):
    """Stage-1 rules: fill missing tags from other slices, then ImageSet reference (values only), then RTSTRUCT."""
    imgs.sort(key=lambda e: (e["inst"] is None, e["inst"] or 0, str(e["path"])))
    rt = next((a for a in aux if a["mod"] == "RTSTRUCT"), None)
    for key in ("pos", "iop", "for_uid"):
        vals = [e[key] for e in imgs if e[key]]
        fill, src = (Counter(vals).most_common(1)[0][0], "series") if vals else (None, "")
        if fill is None and refs:
            rv = [r[key] for r in refs if r[key]]
            if rv: fill, src = Counter(rv).most_common(1)[0][0], "imageset"
        if fill is None and rt is not None and key in ("pos", "for_uid"):
            v = rt[key] if key == "pos" else (rt["for_uid"] or (sorted(rt["fors"])[0] if rt["fors"] else ""))
            if v: fill, src = v, "rtstruct"
        if fill is not None:
            n = 0
            for e in imgs:
                if not e[key]: e[key] = fill; e["filled"].add(key); n += 1
            if n: log.append(f"{key}:{src}({n})")
    if refs and any(e["ipp"] is None for e in imgs):
        bysop = {r["sop"]: r for r in refs}; byinst = {r["inst"]: r for r in refs}; n = 0
        for e in imgs:
            if e["ipp"] is None:
                r = bysop.get(e["sop"]) or byinst.get(str(e["inst"]))
                if r and r["ipp"]: e["ipp"] = r["ipp"]; e["filled"].add("ipp"); n += 1
        if n: log.append(f"ipp:imageset({n})")
    interp_ipp(imgs, log)

def qc_set(o, s, refhdrs, ce_rep):
    imgs, aux = s["images"], s["aux"]; reasons, log = [], []
    if imgs and o.orientation != "audit":
        fill_set(imgs, aux, refhdrs.get((s["pf"], s["pidx"]), []), log)
    else:
        imgs.sort(key=lambda e: (e["inst"] is None, e["inst"] or 0, str(e["path"])))
    s["fills"] = ";".join(log)
    nd, geo_ok, order = 0, True, imgs
    if imgs:
        iops = {tuple(round(x, 3) for x in e["iop"]) for e in imgs if e["iop"]}
        if len(iops) != 1 or any(e["iop"] is None or e["ipp"] is None for e in imgs): geo_ok = False
        else:
            n = np.cross(imgs[0]["iop"][0:3], imgs[0]["iop"][3:6])
            pos = [float(np.dot(e["ipp"], n)) for e in imgs]
            nd = len({round(p, 2) for p in pos})
            order = [imgs[i] for i in sorted(range(len(imgs)), key=lambda i: pos[i])]
    s["order"] = order; s["n_images"] = len(imgs); s["n_distinct"] = nd
    if imgs and not geo_ok: reasons.append("geometry unverifiable")
    if imgs and geo_ok and o.min_slices and nd < o.min_slices: reasons.append(f"fewer than {o.min_slices} slice positions")
    ifor = {e["for_uid"] for e in imgs if e["for_uid"]}
    s["for_consistent"] = len(ifor) == 1
    rts = [a for a in aux if a["mod"] == "RTSTRUCT"]
    s["frame_match"] = ""
    if rts:
        ok = bool(ifor) and all(a["fors"] & ifor for a in rts)
        s["frame_match"] = "yes" if ok else "no"
        if imgs and not ok: reasons.append("structure set frame does not match images")
    sops = {e["sop"] for e in imgs}; rr = set().union(*[a["refs"] for a in rts]) if rts else set()
    s["refs_found"] = f"{len(rr & sops)}/{len(rr)}" if rr else ""
    s["n_rois"] = sum(len(a["rois"]) for a in rts)
    s["region"] = classify_region([n for a in rts for n in a["rois"]]) if rts else ""
    first = imgs[0] if imgs else None
    st, coord = orient_from(first["iop"], first["pos"]) if first else ("", "")
    s["orient"], s["orient_action"] = st, ""
    if st == "mismatch" and o.orientation == "fill_fix" and coord:
        for e in imgs: e["fixpos"] = coord
        s["orient_action"] = f"label {first['pos']}->{coord}"
    if first and s["pidx"] is not None and s["pf"] in ce_rep and s["pidx"] in ce_rep[s["pf"]]: s["ce"], s["ce_src"] = ce_rep[s["pf"]][s["pidx"]], "report"
    elif first and o.no_ce: s["ce"], s["ce_src"] = "No CE", "declared no contrast in dataset"
    elif first and any(e["ce"] for e in imgs): s["ce"], s["ce_src"] = "CE", "DICOM contrast tag"
    else: s["ce"], s["ce_src"] = ("Unknown", "no report, no contrast tag") if first else ("", "")
    s["reasons"] = reasons; s["pass"] = not reasons

def summarize(o, entries, patients, skipped, nref, nfiles):
    sets = [s for p in patients for s in p["sets"]]
    cnt = lambda it: dict(Counter(x for x in it if x))
    reasons = Counter(r for s in sets for r in s["reasons"])
    kept = [s for p in patients for s in p["keep"]]
    nm = {}
    for e in entries:
        if e["pname"]: nm.setdefault(e["pkey"], set()).add(e["pname"])
    id_conflicts = sum(1 for v in nm.values() if len(v) > 1)   # one PatientID carrying more than one animal name
    return dict(id_name_conflicts=id_conflicts, files_seen=nfiles, dicom_files=len(entries), skipped_not_dicom=skipped, imageset_files_skipped=nref,
                patients=len(patients), patients_kept=sum(1 for p in patients if p["keep"]), sets=len(sets), sets_kept=len(kept),
                excluded_reasons=dict(reasons), modalities=cnt(e["mod"] for e in entries),
                orientation=cnt(s["orient"] for s in sets), ce=cnt(s["ce"] for s in sets),
                frame_mismatch_sets=sum(1 for s in sets if s["frame_match"] == "no"),
                tags_filled_sets=sum(1 for s in sets if s["fills"]), burned_in_flagged=sum(e["burned"] for e in entries))

# ---------- crosswalk tables ----------
def load_xw(xw):
    P, S = {}, {}
    f = xw / "crosswalk_patients.csv"
    if f.exists():
        for r in csv.DictReader(open(f, newline="")):
            r.setdefault("source", ""); r["source"] = r["source"] or ""
            P[f"{r['source']}||{r['original_PatientID']}" if r["source"] else r["original_PatientID"]] = r
    f = xw / "crosswalk_sets.csv"
    if f.exists():
        for r in csv.DictReader(open(f, newline="")): S[r["set_key"]] = r
    return P, S

def write_table(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)

def merge_table(path, header, rows, keyfn):
    old = {}
    if path.exists():
        for r in list(csv.reader(open(path, newline="")))[1:]:
            if len(r) == len(header): old[keyfn(r)] = r
    for r in rows: old[keyfn(r)] = r
    write_table(path, header, list(old.values()))

PH = ["original_PatientID", "source", "original_patient_folder", "site_id", "patient_number", "new_PatientID", "new_PatientName", "date_offset_days"]
SH = ["set_key", "new_PatientID", "new_set", "original_patient_folder", "original_set_label"]
FH = ["original_relative_path", "original_SOPInstanceUID", "new_file", "new_SOPInstanceUID", "original_PatientID", "new_PatientID"]
RH = ["original_patient_folder", "original_PatientID", "original_set_label", "new_PatientID", "new_set", "included", "reasons", "n_images", "n_distinct_slice_positions", "frame_match", "ce_status", "tags_filled"]
IH = ["file", "new_patient_id", "new_set", "modality", "series_description", "study_date", "new_study_uid", "new_series_uid", "new_sop_uid", "rows", "columns", "instance_number"]
DH = ["new_patient_id", "new_set", "modalities", "n_images", "n_distinct_slice_positions", "slice_thickness", "rows", "columns", "species", "breed", "sex", "neutered", "age",
      "anatomical_region", "contoured_rois", "ce_status", "ce_source", "orientation_status", "orientation_action", "frame_match", "structure_refs_found", "tags_filled", "qc_pass", "qc_notes", "burned_in_flag"]

# ---------- run ----------
class NothingToWrite(ValueError):
    """Raised when no scan set passes the checks. Carries the scan summary so the page can show the reasons."""
    def __init__(self, msg, summary):
        super().__init__(msg); self.summary = summary


def run(o, progress=lambda *a: None, cancel=None):
    plan, summary = scan(o, progress, cancel)
    src, out, data, xw = resolve_paths(o); srcdir = src if src.is_dir() else src.parent
    pats = [p for p in plan["patients"] if p["keep"]]
    if not pats:
        why = "; ".join(f"{k} ({v})" for k, v in (summary.get("excluded_reasons") or {}).items()) or "no DICOM image sets were found"
        raise NothingToWrite(f"No scan sets passed the checks, so nothing was written. Reasons: {why}. Scroll down for the scan table, or lower the minimum slice count.", summary)
    xw.mkdir(parents=True, exist_ok=True); data.mkdir(parents=True, exist_ok=True)
    secf = xw / "deid_secret.bin"; secret = secf.read_bytes() if secf.exists() else secrets.token_bytes(32)
    P, S = load_xw(xw)
    for p in pats:                                   # IDs are fixed the first time a patient is seen and never recomputed
        r = P.get(p["pkey"])
        if r is None:
            n = max([int(x["patient_number"]) for x in P.values() if x["site_id"] == str(o.site_id)], default=0) + 1
            r = dict(original_PatientID=p["pid"], source=p["src"], original_patient_folder="", site_id=str(o.site_id), patient_number=n,
                     new_PatientID=f"{o.id_prefix}{o.site_id}{n:03d}", new_PatientName=f"{o.site_label}_{n:03d}^{o.name_suffix}",
                     date_offset_days=secrets.randbelow(364) + 1)
            P[p["pkey"]] = r
        r["original_patient_folder"] = ";".join(sorted(set(filter(None, r["original_patient_folder"].split(";"))) | {x for x in p["pfs"] if x}))
        p["x"] = r
        for s in p["keep"]:
            rs = S.get(s["key"])
            if rs is None:
                k = max([int(x["new_set"]) for x in S.values() if x["new_PatientID"] == r["new_PatientID"]], default=0) + 1
                rs = dict(set_key=s["key"], new_PatientID=r["new_PatientID"], new_set=k, original_patient_folder=s["pf"], original_set_label=s["label"])
                S[s["key"]] = rs
            s["x"] = rs
    cache = {}
    def new_uid(u):
        if u not in cache: cache[u] = str(UID("2.25." + str(int(hmac.new(secret, str(u).encode(), hashlib.sha256).hexdigest()[:30], 16))))
        return cache[u]
    remove = []
    if o.remove_owner: remove += OWNER
    if o.remove_staff: remove += STAFF
    if o.remove_station: remove += STATION
    if o.remove_birthdate: remove += BIRTH
    if o.remove_other_ids: remove += OTHER_IDS
    if not o.keep_vet_descriptors: remove += VET
    nfile = sum(len(s["images"]) + len(s["aux"]) for p in pats for s in p["keep"]); total = nfile * 2; done = 0
    C = dict(files_written=0, duplicates_skipped=0, private_removed=0, text_redactions=0, orientation_labels_fixed=0, tags_filled_files=0, errors=0)
    index_rows, set_rows, file_rows, rep_rows, pixh, errors = [], [], [], [], {}, []
    ok_sets = []
    try:
        for p in pats:
            r = p["x"]; rx = make_rx(p["tokens"]) if o.redact_text else None; staff = {}; seen = set()
            offset = int(r["date_offset_days"]); nid, nname = r["new_PatientID"], r["new_PatientName"]
            for s in p["keep"]:
                sd = f"set_{int(s['x']['new_set']):02d}"; groups = {}
                for e in s["order"] or s["images"]: groups.setdefault(e["mod"], []).append(e)
                for a in sorted(s["aux"], key=lambda a: (a["mod"], str(a["path"]))): groups.setdefault(a["mod"], []).append(a)
                written = Counter()
                for mod, lst in groups.items():
                    for i, e in enumerate(lst):
                        if cancel and cancel.is_set(): raise Cancelled()
                        done += 1
                        if done % 5 == 0: progress("write", done, total, f"Writing {done}/{nfile}")
                        try:
                            if e["sop"] in seen: C["duplicates_skipped"] += 1; continue
                            seen.add(e["sop"])
                            ds = pydicom.dcmread(e["path"], force=e["forced"])
                            before = hashlib.sha256(ds.PixelData).hexdigest() if "PixelData" in ds else ""
                            osop = str(ds.get("SOPInstanceUID", ""))
                            if e["filled"]:
                                C["tags_filled_files"] += 1
                                if "pos" in e["filled"] and not present(ds, "PatientPosition"): ds.PatientPosition = e["pos"]
                                if "iop" in e["filled"] and e["iop"]: ds.ImageOrientationPatient = list(e["iop"])
                                if "for_uid" in e["filled"] and e["for_uid"]: ds.FrameOfReferenceUID = e["for_uid"]
                                if "ipp" in e["filled"] and e["ipp"]: ds.ImagePositionPatient = list(e["ipp"])
                            if e["fixpos"]: ds.PatientPosition = e["fixpos"]; C["orientation_labels_fixed"] += 1
                            if o.quadruped and e["mod"] not in AUX: ds.AnatomicalOrientationType = "QUADRUPED"
                            def fn(d, el):
                                kw = el.keyword
                                if kw in UID_KW and el.VR == "UI" and el.value:
                                    el.value = [new_uid(v) for v in el.value] if el.VM > 1 else new_uid(el.value)
                                elif el.VR in ("DA", "DT") and el.value and o.date_mode != "keep":
                                    if o.date_mode == "remove": el.value = ""; return
                                    vals = el.value if el.VM > 1 else [el.value]; res = []
                                    for v in vals:
                                        try:
                                            dt = datetime.strptime(str(v)[:8], "%Y%m%d") + timedelta(days=offset)
                                            res.append(dt.strftime("%Y%m%d") + (str(v)[8:] if el.VR == "DT" else ""))
                                        except Exception: res.append("")
                                    el.value = res if el.VM > 1 else res[0]
                                elif el.VR == "PN" and kw not in NO_REDACT and el.value:
                                    def code(v):
                                        k = str(v).strip()
                                        if k and k not in staff: staff[k] = f"Staff_{len(staff) + 1}"
                                        return staff.get(k, v)
                                    el.value = [code(v) for v in el.value] if el.VM > 1 else code(el.value)
                                elif rx and redactable(el) and el.VR != "PN":
                                    sv = str(el.value); s2 = rx.sub("REDACTED", sv)
                                    if s2 != sv: C["text_redactions"] += 1; el.value = s2
                            walk(ds, fn)
                            for kw in remove:
                                if kw in ds: del ds[kw]
                            if o.remove_owner:
                                for el in list(ds.iterall()):
                                    if el.keyword and ("Comment" in el.keyword or "Signature" in el.keyword):
                                        try: del ds[el.tag]
                                        except Exception: pass
                            ds.PatientName, ds.PatientID = nname, nid
                            ds.ReferringPhysicianName, ds.InstitutionName = o.physician, o.institution
                            if o.remove_private:
                                C["private_removed"] += sum(1 for x in ds.iterall() if x.tag.is_private); ds.remove_private_tags()
                                for t in [t for t in list(ds.keys()) if 0x6000 <= t.group <= 0x60FF]: del ds[t]
                            ds.PatientIdentityRemoved = "YES"; ds.DeidentificationMethod = f"VAIDER-mask {VERSION}"
                            dest = data / nid / sd / mod / f"{mod}_{i:04d}.dcm"
                            dest.parent.mkdir(parents=True, exist_ok=True); ds.save_as(dest)
                            rel = str(dest.relative_to(data)); pixh[rel] = before; C["files_written"] += 1; written[mod] += 1
                            index_rows.append([rel, nid, int(s["x"]["new_set"]), mod, str(ds.get("SeriesDescription", "")), str(ds.get("StudyDate", "")),
                                               str(ds.get("StudyInstanceUID", "")), str(ds.get("SeriesInstanceUID", "")), str(ds.SOPInstanceUID),
                                               str(ds.get("Rows", "")), str(ds.get("Columns", "")), str(ds.get("InstanceNumber", ""))])
                            file_rows.append([str(e["path"].relative_to(srcdir)) if inside(e["path"], srcdir) else e["path"].name, osop, rel, str(ds.SOPInstanceUID), p["pid"], nid])
                        except Cancelled: raise
                        except Exception as ex:
                            C["errors"] += 1; errors.append(f"{e['path'].name}: {type(ex).__name__}")
                img0 = (s["order"] or [None])[0]
                v = img0["vet"] if (img0 and o.keep_vet_descriptors) else {}
                set_rows.append([nid, int(s["x"]["new_set"]), ";".join(f"{m}:{n}" for m, n in written.items()), s["n_images"], s["n_distinct"],
                                 img0["thick"] if img0 else "", img0["rows"] if img0 else "", img0["cols"] if img0 else "",
                                 v.get("PatientSpeciesDescription", ""), v.get("PatientBreedDescription", ""), v.get("PatientSex", ""), v.get("PatientSexNeutered", ""),
                                 v.get("PatientAge", ""), s["region"], s["n_rois"], s["ce"], s["ce_src"], s["orient"], s["orient_action"], s["frame_match"],
                                 s["refs_found"], s["fills"], "yes" if s["pass"] else "no", "; ".join(s["reasons"]), "YES" if any(e["burned"] for e in s["images"]) else ""])
                ok_sets.append(s)
        for p in plan["patients"]:                    # every set considered, including excluded ones, goes in the restricted report
            for s in p["sets"]:
                x = s.get("x", {}); inc = s in p["keep"]
                rep_rows.append([s["pf"], p["pid"], s["label"], p.get("x", {}).get("new_PatientID", "") if inc else "", x.get("new_set", ""), "yes" if inc else "no",
                                 "; ".join(s["reasons"]), s["n_images"], s["n_distinct"], s["frame_match"], s["ce"], s["fills"]])
    finally:                                         # never lose the crosswalk, even on cancel or error
        write_table(xw / "crosswalk_patients.csv", PH, [[r.get(k, "") for k in PH] for r in sorted(P.values(), key=lambda r: (r["site_id"], int(r["patient_number"])))])
        write_table(xw / "crosswalk_sets.csv", SH, [[r.get(k, "") for k in SH] for r in S.values()])
        merge_table(xw / "crosswalk_files.csv", FH, file_rows, lambda r: r[2])
        merge_table(xw / "anonymization_report.csv", RH, rep_rows, lambda r: r[0] + "|" + r[1] + "|" + r[2])
        secf.write_bytes(secret)
        for q in ("crosswalk_patients.csv", "crosswalk_sets.csv", "crosswalk_files.csv", "anonymization_report.csv", "deid_secret.bin"):
            try: os.chmod(xw / q, 0o600)
            except OSError: pass
        (xw / "README_RESTRICTED.txt").write_text(
            "RESTRICTED. These files link de-identified data back to real animals and clients, and name the original folders.\n"
            "Do not share them, put them next to analysis data, commit them to Git, or open them in any AI tool.\n"
            "Keep them on approved lab storage. Reuse this same folder for every later batch of this project:\n"
            "deleting it breaks the link between batches (pseudonyms, date shifts, set numbers and UIDs).\n"
            "Share only the DEID_DATA folder, after review and sign-off.\n")
        merge_table(data / "deid_index.csv", IH, index_rows, lambda r: r[0])
        merge_table(data / "deid_sets.csv", DH, set_rows, lambda r: f"{r[0]}|{r[1]}")
    # verify from disk with the same field rule and whole-word rule used for redaction
    bad_text = bad_pix = bad_removed = 0; sops, refs = set(), set(); setfor = {}
    rxs = {p["x"]["new_PatientID"]: (make_rx(p["tokens"]) if o.redact_text else None) for p in pats}
    for i, row in enumerate(index_rows):
        if cancel and cancel.is_set(): raise Cancelled()
        progress("verify", nfile + i, total, f"Verifying {i + 1}/{len(index_rows)}")
        chk = pydicom.dcmread(data / row[0])
        if "PixelData" in chk and hashlib.sha256(chk.PixelData).hexdigest() != pixh.get(row[0]): bad_pix += 1
        rx = rxs.get(str(chk.PatientID))
        if rx and text_hits(chk, rx): bad_text += 1
        if any(kw in chk for kw in remove): bad_removed += 1
        sops.add(str(chk.SOPInstanceUID)); refs |= image_refs(chk)
        d = setfor.setdefault(str(Path(row[0]).parent.parent), dict(img=set(), rts=[]))
        if row[3] == "RTSTRUCT":
            d["rts"].append({str(it.get("FrameOfReferenceUID", "")) for it in chk.get("ReferencedFrameOfReferenceSequence", [])} | ({str(chk.FrameOfReferenceUID)} if "FrameOfReferenceUID" in chk else set()))
        elif "FrameOfReferenceUID" in chk and row[3] not in AUX: d["img"].add(str(chk.FrameOfReferenceUID))
    frame_bad = sum(1 for d in setfor.values() if d["rts"] and d["img"] and not all(r & d["img"] for r in d["rts"]))
    progress("verify", total, total, "Verification complete")
    summary["excluded_sets"] = sum(1 for p in plan["patients"] for s in p["sets"] if s not in p["keep"])
    report = dict(tool="VAIDER-mask", version=VERSION, code_sha256=code_hash(), run_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  operator=o.operator, reviewer_signoff=None, summary=summary, counters=C,
                  verification=dict(files_with_leftover_identifier_text=bad_text, files_with_removed_tags_still_present=bad_removed, changed_pixel_data=bad_pix,
                                    unresolved_references=len(refs - sops), structure_sets_frame_mismatch_in_output=frame_bad),
                  options={k: v for k, v in asdict(o).items() if k not in ("src", "out", "crosswalk_dir", "operator", "ce_report")})
    (data / "run_report.json").write_text(json.dumps(report, indent=2))
    report["errors"] = errors[:20]
    v = report["verification"]
    report["ok"] = not any((v["files_with_leftover_identifier_text"], v["files_with_removed_tags_still_present"], v["changed_pixel_data"],
                            v["unresolved_references"], v["structure_sets_frame_mismatch_in_output"], C["errors"]))
    return report
