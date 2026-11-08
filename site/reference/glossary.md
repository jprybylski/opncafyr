---
title: Glossary
parent: Reference
nav_order: 1
description: Short definitions of the terms you meet in opncafyr, as the app uses them.
permalink: /reference/glossary/
---

# Glossary

The terms you meet in opncafyr, defined the way the app uses them.
{: .fs-5 .fw-300 }

Acknowledge (a warning)
: Tick **I have reviewed this and want to go on** next to a warning from the checks. Every warning must be acknowledged before a run can start, and the acknowledgements are recorded with the run.

Actual time
: Elapsed time from the dose to the sample, as a number, as it really happened. It is the time NCA uses. The app never puts nominal time in its place, and mapping clock times (such as `08:30`) or nominal time as actual time blocks the run.

Analyte
: One substance measured in the samples, such as a parent drug or a metabolite. Each run analyses exactly one analyte, chosen by the exact text of the **Analyte code** column: `"METAB "` (with a space) is a different analyte from `"METAB"`.

App folder
: Where opncafyr is installed for your user account: `~/Library/Application Support/opncafyr` on macOS, `%LOCALAPPDATA%\opncafyr` on Windows. It holds the app, its own R and tools, settings, logs and a download cache, never your study data. See [Files and folders]({{ '/reference/files/' | relative_url }}).

BLQ (below the limit of quantification)
: A result too low for the assay to measure reliably, below the LLOQ. The app never sets a BLQ value to zero by itself. You choose how BLQ samples are handled before, after the dose and at the end of the profile (**Exclude the sample**, **Set to zero**, **Half the LLOQ** or **LLOQ / √2**), and the **BLQ method** (**Conventional (rules above)** or **Censored log-normal**). There is no default.

Blocking finding
: A check result that stops a run from starting until you change the cleaning recipe or the mapping, for example a negative concentration or two rows with the same subject and time. The app never fixes it for you.

Build
: One recorded DOCX or PPTX made from a publication, kept in `report/builds` or `deck/builds` with a record of the exact runs, file fingerprints and settings it used. A report build is marked **Out of date** when its inputs change after it was made; the build itself never changes.

Check (of a run's data)
: **Run checks** on the **Analysis** page tests the chosen analyte's cleaned rows and gives **Blocking** findings, warnings and notes.

Cleaning step
: One entry in a recipe, such as **Trim spaces**, **Convert units** or **Exclude rows**. Every step shows the rows in, out and rejected.

Completed run
: A run that opnca-r saved, whose replay matched, and that you confirmed with **Confirm and complete**. It is sealed: every file is recorded with its fingerprint and made read-only. Only completed runs can be published. Any change needs a new run.

Description file (metadata sidecar)
: A small `<name>_<ext>_metadata.json` file next to each published table or figure in `OUTPUTS`. Reports and decks take footnotes from it: where the result came from, the analyte and run, and abbreviations. The app checks it before every publish, report check, render, deck check and build.

Exclusion
: A row marked as not counting in the analysis, with a reason. Excluded rows stay in the run's data and opnca-r sees them as excluded. Compare **removed** rows, which go to the rejected-row log. Samples you exclude inside opnca-r are recorded too, but without a reason, and the app adds a warning to the run.

Fingerprint (SHA-256)
: A code computed from a file's exact bytes. If even one byte changes, the fingerprint changes. The app records fingerprints of source files, run files, published files and builds, and checks them again later.

Import
: Recording a source file's size and fingerprint in the study folder (**Import** on the **Data** page). It does not copy or change the file. A changed file imported again is recorded as a new source.

Job
: A task that runs in the background, such as cleaning, checks, a replay, a report render or a deck build. The **Jobs** page lists them. A failed or cancelled job keeps nothing half-made.

LLOQ (lower limit of quantification)
: The lowest concentration the assay can measure reliably. Results below it are BLQ. It may come from a column in your data.

Mapping
: Telling the app which column holds each role, such as **Subject (profile) ID**, **Actual time** or **Concentration**, and how units and flag values are given. The app suggests columns but never uses a suggestion until you pick it.

NCA (non-compartmental analysis)
: The standard way of summarising a concentration–time profile with parameters such as Cmax, Tmax and AUC, without fitting a model. opnca-r does every NCA calculation; opncafyr never calculates NCA parameters itself.

Nominal time
: The planned sampling time from the protocol, such as "2 h". It can be mapped as its own role. The mean concentration–time figures can use nominal time, which you choose in opnca-r when you save.

opnca-r
: The tool that does the NCA calculations. It opens inside the app on one run at a time.

OUTPUTS
: The folder in a study folder that holds published copies of tables and figures, in `OUTPUTS/tables` and `OUTPUTS/figures`. Reports and decks are built from it.

Profile
: One subject's concentration–time series for one analyte (split further by occasion when an occasion column is mapped). Each profile needs one dose and one route.

Publication
: A fixed set of tables and figures published from exact completed runs, with a record of every run ID and file fingerprint. A report or deck is always built from one publication, never from "the latest" run.

Recipe
: The ordered list of cleaning steps for one imported file. It is kept in the study folder and can be applied again. Changing it never changes a completed run.

Rejected-row log
: The list of rows that a **Remove rows** step took out of the cleaned data, each with its source line number, the step and the reason. No row leaves the data without a line in this log.

Replay
: After opnca-r saves a run, the app runs the run's saved script (`analysis.R`) again on its own copy of the frozen rows and compares every result table with the saved one. A matching replay shows the script reproduces the saved numbers; it does not show the analysis choices were right.

Run
: One analyte's analysis: a frozen copy of that analyte's rows, your mapping and BLQ choices, the warnings you acknowledged, and later opnca-r's script and results. Its state is **Preparing**, **Running**, **Reviewing**, **Completed** or **Failed**. A run whose records are damaged shows as **Incomplete**.

Source file
: Your original data file, in the study folder or its `source-data` folder. The app reads it but never changes, moves or rewrites it.

Specimen
: The sample matrix, such as plasma or urine. If you map a specimen column, each run uses exactly one specimen value.

Study folder
: A folder you choose that keeps one study's data, cleaning, runs, publications, reports and decks together. The app calls it a workspace in some buttons (**Create workspace here**, **Open workspace**). It adds a hidden `.opncafyr` folder for its records.

Support bundle
: A zip file that helps whoever supports opncafyr find out why something went wrong. It holds versions, tool health, recent job states and the end of the logs, never your data, results or file names. You see every part before it is saved; it stays on your computer until you send it yourself. See [Make a support bundle]({{ '/guides/troubleshooting/' | relative_url }}#make-a-support-bundle).

Verified bundle (offline bundle)
: A folder or zip that holds every file the installer needs, for computers that can't download them. The installer checks every file against the fingerprints built into it and refuses a missing or changed file. See [Offline install]({{ '/guides/offline-install/' | relative_url }}).

Warning
: A check result that may be fine for your data but that you must look at and acknowledge before the run can start, for example a concentration of 0 with no BLQ flag.
