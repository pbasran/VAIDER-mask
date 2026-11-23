# VAIDER-mask for X-rays (radiographs)

VAIDER-mask has two modes. **CT / MR / radiotherapy** mode handles scan sets of slices with their structure sets, plans and doses. **X-ray** mode handles single radiographs, such as thoracic, orthopaedic or dental X-rays. This page explains X-ray mode.

In short: X-ray mode takes a folder of DICOM radiographs. It writes a copy of each one with the animal's name, ID, owner details, clinic, staff, dates and device IDs removed or replaced, and keeps the image and the details needed for research. The original files are never changed.

## How to use it

1. Start VAIDER-mask (`START_Mac.command` or `START_Windows.bat`). A page opens in your browser.
2. In **section 0**, choose **X-ray (radiographs)**. The page changes to show only the settings that apply to X-rays.
3. Choose the source folder (it is searched through all subfolders), an output folder, and ideally a separate restricted folder for the crosswalk (the key).
4. Click **Scan only (dry run)**. Nothing is written yet. Check the counts: radiographs found, animals, studies, views, and especially how many images have no burned-in tag.
5. Click **De-identify**.
6. **Look at the images before sharing them** (see "What it does not do" below).

## What it reads, and what it skips

| Read and de-identified | Skipped (not written) |
|---|---|
| DICOM radiographs with modality **DX** (digital radiography), **CR** (computed radiography), **RG** (radiographic imaging), **IO** (intra-oral dental) or **PX** (panoramic dental) | CT, MR, radiotherapy files, reports, presentation states (annotation layers), and any other DICOM type |
| Files with or without the usual DICOM file header, under any file name (for example `.dcm`, `.v2`, or no extension) | Images marked **BurnedInAnnotation = YES** (on by default; see below) |
| | **Screenshots** (secondary capture images): never written |
| | Non-DICOM files such as PNG, JPEG or TIFF |

All the views from one visit (one DICOM study, for example a lateral and a ventrodorsal thorax) stay together in one folder.

## What happens to the metadata

### Replaced

| Field | Becomes |
|---|---|
| Patient name (`PatientName`) | A new name, for example `Site1_001^CornellRT` |
| Patient ID (`PatientID`) | A new ID, for example `Z001001`. The same animal always gets the same ID, in later batches too. |
| Referring vet (`ReferringPhysicianName`) | A fixed replacement, by default `BigRed^Dr` |
| Clinic name (`InstitutionName`), everywhere in the file | A fixed replacement, by default `CornellRT` |
| Any other person name not removed below | `Staff_1`, `Staff_2`, … (the same person gets the same code) |
| All unique IDs of the study, series, image and references (UIDs) | New IDs, generated with a secret key that stays in the restricted folder. Links between files are kept. |
| The file header | Rebuilt from scratch: only the image type, the new image UID, the encoding and VAIDER-mask's name. Nothing from the original header is kept. |

### Removed

Removed wherever they appear in the file, including inside nested sequences. Each group can be switched off in section 5 of the page, but all are on by default.

| Group | Fields (DICOM keywords) |
|---|---|
| **Owner / client** | ResponsiblePerson, ResponsiblePersonRole, ResponsibleOrganization, PatientAddress, PatientTelephoneNumbers, PersonAddress, PersonTelephoneNumbers, OrderCallbackPhoneNumber, OtherPatientIDs, OtherPatientNames, OtherPatientIDsSequence, PatientComments, AdditionalPatientHistory, MedicalRecordLocator, BreedRegistrationSequence, PatientBirthName, PatientMotherBirthName, CurrentPatientLocation, PatientInstitutionResidence, RegionOfResidence, CountryOfResidence, ConfidentialityConstraintOnPatientDataDescription, MilitaryRank, BranchOfService, and **every field whose name contains "Comment" or "Signature"** (for example ImageComments) |
| **Staff** | PerformingPhysicianName, OperatorsName, PhysiciansOfRecord, NameOfPhysiciansReadingStudy, RequestingPhysician, ReviewerName, ReferringPhysicianAddress, ReferringPhysicianTelephoneNumbers, and their identification sequences |
| **Station and device** | StationName, DeviceSerialNumber, InstitutionAddress, InstitutionalDepartmentName |
| **Station and device, X-ray only** | PlateID, CassetteID, DetectorID (these identify the imaging plate or detector), PerformedStationName, PerformedStationAETitle, PerformedLocation, ScheduledStationName, ScheduledStationAETitle, ScheduledProcedureStepLocation, RequestingService |
| **Birth date** | PatientBirthDate, PatientBirthTime |
| **Other IDs** | AccessionNumber, StudyID, IssuerOfPatientID, IssuerOfPatientIDQualifiersSequence, PerformedProcedureStepID, RequestAttributesSequence |
| **Private (vendor) tags** | All private tags, plus overlays (graphics layered on top of the image, which can carry text) |

### Changed

| What | How |
|---|---|
| **Dates** (every date field, for example StudyDate, SeriesDate, AcquisitionDate) | Shifted by the same random number of days (30 to 364) for every image of the same animal. Intervals between an animal's visits are kept. You can also choose to remove all dates. Times of day are not changed. |
| **Free text** (for example StudyDescription, SeriesDescription, ProtocolName, codes inside sequences) | The animal's name, the owner's name, staff names, the IDs and the accession number are replaced with `REDACTED`, as whole words only. Anything else typed into these fields stays. |

### Added

`PatientIdentityRemoved = YES` and `DeidentificationMethod = VAIDER-mask <version>`. If the quadruped option is ticked, `AnatomicalOrientationType = QUADRUPED` is also added.

### Kept

| Kept | Why |
|---|---|
| The **image pixels**, unchanged (checked after writing) | The image is the data |
| Species, breed, sex, neutered status, age, weight, size | Useful for research. This can be switched off; breed plus age can help re-identify a rare animal. |
| View (`ViewPosition`, for example LL or VD), body part (`BodyPartExamined`), side (`ImageLaterality`), pixel spacing (`ImagerPixelSpacing`, `PixelSpacing`), exposure settings (kVp, mAs, exposure time), manufacturer and model | Needed to read and compare radiographs; not identifying |

## What it does NOT do

- **It does not remove text burned into the image.** Radiographs often have the clinic name, date, side marker or even the patient's or owner's name printed on the image itself. VAIDER-mask cannot see or remove these.
  - It skips images whose `BurnedInAnnotation` tag says **YES**.
  - Many X-ray systems leave that tag empty. Those images **are** written, and the scan page and `deid_xray.csv` count them ("no burned-in tag").
  - Look at every such image before sharing, or crop or mask the text with another tool first.
- It does not read PNG, JPEG or TIFF images. Only DICOM.
- It does not catch identifying text that is not a name or ID it already knows. For example, an address typed into a description field stays, unless the whole field is on the removal lists above.
- Multi-frame images and fluoroscopy have not been tested.

## What you get

```
DEID_DATA/                      share this, after review
  Z001001/study_01/DX/DX_0000.dcm, DX_0001.dcm …   one folder per animal and per study
  deid_xray.csv                 one row per study (see below)
  deid_index.csv                one row per file
  run_report.json               settings, counts and the checks below
CROSSWALK_RESTRICTED/           NEVER share: links new IDs back to the real animals
```

`deid_xray.csv` columns: new patient ID, study number, modalities, number of images, views, body parts, laterality, species, breed, sex, neutered, age, images skipped for burned-in text, screenshots skipped, **images written without a burned-in tag (check these)**, QC pass, QC notes.

Keep the crosswalk folder outside the output folder, or at least never zip or share the whole output folder. Share `DEID_DATA` only.

## Checks after writing

VAIDER-mask reads every written file back and checks that:
- no known name or ID is left in any text;
- removed fields are really gone, including inside sequences;
- the pixels are unchanged;
- the file header carries the new image UID;
- references between files still resolve.

The page shows the results. Have a second person review a tag dump and the images before sharing.

## Testing it

- `python3 scripts/selftest.py` tests both modes on invented data and should end with `31/31 checks passed`.
- `python3 scripts/make_xray_test_data.py <folder>` creates invented radiographs to try the page with. It makes 4 dogs, including one study where every image is burned-in, plus a screenshot, a CT slice and an annotation file that must be skipped. A default run writes 4 images for 3 animals.
