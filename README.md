# VAIDER-mask

**Veterinary DICOM anonymizer** from the VAIDER Lab (Veterinary AI in Diagnostic Imaging and Radiotherapy).

VAIDER-mask removes owner, patient and clinic identifiers from veterinary CT, MR and radiotherapy DICOM (images, RTSTRUCT, RTPLAN, RTDOSE). It runs only on your own computer: no network calls, no telemetry, no AI in the loop. It starts a small local web page (127.0.0.1) for choosing folders and reviewing results.

> **Status: draft v0.3. Not validated on real data.** Tested on synthetic CT and RTSTRUCT only. Test on phantom or synthetic data first, have a second person check the output against a tag dump, and confirm with your own institution's rules before sharing any data. No pixel scrubbing: burned-in text in images is not removed. Provided as is, with no warranty (see the license).

## Quick start

1. Install Python 3.
2. Mac: double-click `START_Mac.command` (if blocked, right-click, Open). Windows: double-click `START_Windows.bat`.
3. The first run creates a private Python environment in your home folder and installs `pydicom` and `numpy` (needs internet once). A page opens in your browser.
4. Try it on synthetic data first: `python3 scripts/make_test_data.py <folder>` creates Pinnacle-style test patients with deliberate problems (all identifiers invented).

## What it does

- Reads a file or a folder tree. Pinnacle-style patient folders (`dicom_output_N` with `rtstruct_N.dcm`) form one scan set each; `ImageSet_N.DICOM` copies are never output. Other layouts are grouped by DICOM series.
- Writes `DEID_DATA/<NewID>/set_NN/<MOD>/<MOD>_nnnn.dcm`, one folder per scan set, plus `deid_sets.csv` (per set: species, breed, sex, age, region, CE / No CE, orientation, frame match, checks), `deid_index.csv` and `run_report.json`. No original names appear in `DEID_DATA`.
- Writes the key (`crosswalk_*.csv`, `anonymization_report.csv`, UID secret) to a separate restricted folder. Never share or commit it.
- New patient IDs are assigned once, stored in the key, and reused in later batches. Each site numbers its own patients.
- Replaces name, ID and physician; removes owner, staff, station, accession and private tags; shifts dates per animal; remaps UIDs consistently (HMAC); redacts the patient's own words in free text by whole-word match, never touching structure names (ROIName).
- Verifies the output from disk: leftover identifying text, removed tags, unchanged pixels, unresolved references, structure-set frame matches images.
- QC gates per set (skipped by default if failed): minimum distinct slice positions (default 41), verifiable geometry, matching frame of reference.
- Optional filling of missing orientation tags, and a quadruped option.

## Defaults you may want to change

Replacement values such as the name suffix and institution (`CornellRT`), physician (`BigRed^Dr`), ID prefix (`Z00`) and site label are defaults in the page; set your own.

## Files

- `scripts/core.py` engine (all de-identification logic)
- `scripts/server.py` local server, `scripts/index.html` the page
- `scripts/make_test_data.py` synthetic DICOM generator
- `scripts/old/` earlier command-line prototype

## Known limits

No pixel scrubbing. Multi-frame and compressed images not tested. Deeply nested sequences are walked, but not every vendor layout is tested. Free text is scanned only for the patient's own words. Not a substitute for institutional review.

## Acknowledgements

VAIDER-mask is built on the Pinnacle de-identification pipeline developed by **Morwen Xu** in the VAIDER Lab. Thank you, Morwen, for the original scripts and for the problems you found and fixed along the way.

## License

GNU General Public License v3.0. See `LICENSE`.
