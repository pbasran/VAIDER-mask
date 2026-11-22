"""VAIDER-mask self-test. Builds the synthetic test patients in a temporary folder, runs the engine with default settings,
and checks the result independently of the engine's own verification. Nothing outside the temporary folder is touched.
Usage:  python selftest.py            (prints PASS/FAIL per check; exit code 0 only if all pass)
        python selftest.py --keep     (keep the temporary folder to inspect it)"""
import re, sys, shutil, tempfile
from collections import Counter
from pathlib import Path
import pydicom
import core, make_test_data

ID_KW = {"PatientName", "PatientID", "ResponsiblePerson", "InstitutionName", "InstitutionalDepartmentName", "StationName",
         "OperatorsName", "PerformingPhysicianName", "ReferringPhysicianName", "AccessionNumber", "DeviceSerialNumber"}
SKIP_FIELDS = {"ROIName", "ROIObservationLabel"}
ALLOWED_META = {"FileMetaInformationGroupLength", "FileMetaInformationVersion", "MediaStorageSOPClassUID", "MediaStorageSOPInstanceUID",
                "TransferSyntaxUID", "ImplementationClassUID", "ImplementationVersionName"}          # structure names are kept on purpose
results = []
def check(name, ok, detail=""):
    results.append(ok); print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   ({detail})" if detail and not ok else ""))

def source_identifiers(src):
    words, uids = set(), set()
    for f in src.rglob("*"):
        if not f.is_file() or f.suffix == ".txt": continue
        ds = pydicom.dcmread(f, force=True)
        for el in ds.iterall():
            if el.keyword in ID_KW and el.value:
                for w in re.split(r"[\^\s,;/_.]+", str(el.value)):
                    if len(w) >= 3: words.add(w)
            if el.VR == "UI" and el.value and not str(el.value).startswith("1.2.840.10008."): uids.add(str(el.value))
    return words, uids

def main():
    keep = "--keep" in sys.argv
    tmp = Path(tempfile.mkdtemp(prefix="vaider_selftest_")); src, out = tmp / "src", tmp / "out"
    try:
        make_test_data.make(src)
        words, uids = source_identifiers(src)
        r = core.run(core.Options(src=str(src), out=str(out)))
        s, v, c = r["summary"], r["verification"], r["counters"]
        check("engine reports ok", r["ok"], str(v))
        check("5 of 8 patients written", (s["patients"], s["patients_kept"]) == (8, 5), f"{s['patients']} / {s['patients_kept']}")
        check("the 3 deliberate failures and the screenshot are excluded, nothing else",
              s["excluded_reasons"] == {"fewer than 41 slice positions": 2, "geometry unverifiable": 1, "structure set frame does not match images": 1,
                                        "screenshot or secondary capture (may show identifiers in the pixels)": 1}, str(s["excluded_reasons"]))
        check("contour named after the animal is flagged", s.get("roi_names_with_identifiers") == 1, str(s.get("roi_names_with_identifiers")))
        check("no write errors", c["errors"] == 0, str(r.get("errors")))

        data = out / "DEID_DATA"; files = sorted(data.rglob("*.dcm"))
        sets = {}
        for f in files: sets.setdefault(f.parent.parent, Counter())[f.parent.name] += 1
        chain = [m for m in sets.values() if {"RTPLAN", "RTDOSE", "RTIMAGE"} <= set(m)]
        check("RT chain kept together (CT + RTSTRUCT + RTPLAN + RTDOSE + RTIMAGE in one set)",
              len(chain) == 1 and chain[0]["CT"] == 45 and chain[0]["RTSTRUCT"] == 1, str([dict(m) for m in sets.values()]))
        check("raw (no preamble, .v2) export read and written", sum(1 for m in sets.values() if set(m) == {"CT"} and m["CT"] == 45) >= 1)

        leaks, uid_leaks, not_part10, meta_bad = Counter(), 0, 0, 0
        rx = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + r")(?![A-Za-z0-9])", re.I)
        for f in files:
            if f.read_bytes()[128:132] != b"DICM": not_part10 += 1
            ds = pydicom.dcmread(f)
            if ds.file_meta.MediaStorageSOPInstanceUID != ds.SOPInstanceUID: meta_bad += 1
            if {el.keyword for el in ds.file_meta} - ALLOWED_META: meta_bad += 1
            for el in list(ds.iterall()) + list(ds.file_meta):
                if el.VR in ("SQ", "OB", "OW", "OF", "OD", "UN") or el.keyword in SKIP_FIELDS: continue
                vals = el.value if isinstance(el.value, (list, pydicom.multival.MultiValue)) else [el.value]
                for x in vals:
                    if el.VR == "UI": uid_leaks += str(x) in uids
                    elif el.VR in ("PN", "LO", "SH", "ST", "LT", "UT", "UC", "CS", "AE"):
                        m = rx.search(str(x))
                        if m: leaks[f"{el.keyword}: {m.group(0)}"] += 1
        check("no source names, IDs, owners, clinics or staff left in any tag (nested and file meta included)", not leaks, str(dict(leaks.most_common(5))))
        check("no original UIDs left (file meta included)", uid_leaks == 0, f"{uid_leaks} values")
        check("file header is new: only class, new SOP UID, transfer syntax, VAIDER-mask's name", meta_bad == 0, f"{meta_bad} files")
        check("every output file is standard DICOM (preamble + DICM)", not_part10 == 0, f"{not_part10} files")
        names_in_paths = [w for w in words if any(w.lower() in str(f.relative_to(out)).lower() for f in out.rglob("*") if "CROSSWALK" not in str(f))]
        check("no source names in DEID_DATA file or folder names", not names_in_paths, str(names_in_paths[:5]))

        import csv
        offs = [int(r["date_offset_days"]) for r in csv.DictReader(open(out / "CROSSWALK_RESTRICTED" / "crosswalk_patients.csv"))]
        check("every date shift is at least 30 days", offs and min(offs) >= 30, str(offs))
        r2 = core.run(core.Options(src=str(src), out=str(out)))      # second batch: same IDs must be reused
        xw = (out / "CROSSWALK_RESTRICTED" / "crosswalk_patients.csv").read_text().splitlines()
        check("re-running reuses the same patient IDs", len(xw) - 1 == 5 and r2["ok"], f"{len(xw) - 1} rows")
    finally:
        if keep: print("kept:", tmp)
        else: shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)

if __name__ == "__main__": main()
