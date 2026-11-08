---
title: Limitations
parent: Reference
nav_order: 4
description: What opncafyr does not do, by design or not yet, and the known issues in this version.
permalink: /reference/limitations/
---

# Limitations

What opncafyr does not do, by design or not yet, and the known issues in this version.
{: .fs-5 .fw-300 }

opncafyr is pre-release software. This page lists the limits a user is most likely to meet. Items marked **Not available yet** are planned or under discussion; no dates are promised.

## By design

These are deliberate and are not expected to change.

| Limit | Why |
|---|---|
| **Research use only.** | The app has not been validated for clinical or regulatory decisions. A successful run, replay, report or deck shows that the work was done and assembled as recorded, not that the analysis is right. The installation report is not a validation either. |
| **One analyte per run.** | Each run analyses exactly one analyte, matched on the exact text of the analyte code. To analyse several, make one run each; a publication can then combine them. |
| **One specimen per run.** | If a specimen column is mapped, each run uses one specimen value. |
| **opncafyr never calculates NCA.** | opnca-r does every calculation, including the mean profiles. |
| **The app doesn't decide for you.** | It never sets BLQ to zero, puts nominal time in place of actual time, guesses units, fills in a blank route, or resolves duplicate rows by itself. When something is unclear, it asks, and the run waits until you choose. |
| **Completed runs never change.** | Any change to cleaning, mapping, exclusions or BLQ rules needs a new run. |
| **Only completed runs can be published.** | A run must have a matching replay, be confirmed, and still match its recorded fingerprints. |
| **One run open in opnca-r at a time.** | Opening another run closes the first. A run that has been saved can't be opened in opnca-r again. |
| **Source files are never changed.** | The app only reads them: cleaning results are written inside the study folder's own records. Importing records a file's fingerprint; it doesn't copy the file. |

## Not available yet

| Limit | What to do now |
|---|---|
| **CSV files only.** Other formats, such as Excel or SAS transport files, can't be imported. | Save your data as CSV first. |
| **Data files are found only directly in the study folder or its `source-data` folder.** Deeper folders are not scanned. | Put files in `source-data`. |
| **Large files are previewed from a sample.** Files over 50 MB are previewed from their first 100 000 rows (the preview says so). The fingerprint always covers the whole file. | — |
| **No automatic flag for direct identifiers.** The app does not yet point out columns that look like names, initials or dates of birth. | Check your data before you import it. Coded subject IDs are expected, and appear in some published tables and figures (labelled **shows subject IDs**). |
| **No reason is recorded for samples excluded inside opnca-r.** | The app adds a warning to the run. To record a reason, exclude the rows in a cleaning step instead. |
| **One report template and one deck template.** Both come with the app. Your organisation's own report shell or deck template can't be plugged in yet. | Edit the report text and the slides after setting them up. |
| **The installer and the app are not code-signed (Windows) or notarized (macOS).** Offline bundles have no detached signature. | Get the installer from the person who supports opncafyr at your site. For offline bundles, administrators can compare the installer payload digest with the value from whoever supplied the installer. |
| **No ready-made offline bundles are published with releases.** | An administrator builds one on a connected computer. See [Offline install]({{ '/guides/offline-install/' | relative_url }}). |
| **No native Linux install.** A container image for cloud computers is under discussion. | Use macOS or Windows. |
| **macOS 14 and earlier, Windows 10 and Windows on Arm are not supported.** | See [Platforms and versions]({{ '/reference/platforms/' | relative_url }}). |
| **No public download page.** The opnca-r release is not public. | Ask whoever provides your opncafyr install. |

## Known issues in this version

| Issue | Workaround |
|---|---|
| **Deck tables with empty cells fail to build** ("IndexError: tuple index out of range"). opnca-r's tables have empty cells whenever a parameter can't be calculated. | Build the deck from a publication with the figures only. The report has no such limit. See [Build reports and decks]({{ '/guides/reports-and-decks/' | relative_url }}). |
| **Word asks whether to update fields** when you open a report. | Expected; the app doesn't recalculate fields when it builds the report. |
| **Slide previews need LibreOffice**, which is optional and not installed by default. | Decks build without previews. |
| **Reports can't be built in a study folder inside a hidden folder** (one whose name starts with a dot). | Keep study folders in an ordinary folder, such as Documents. |
| **Runs saved without the mean concentration–time figures can't offer them** for publishing. | Make a new run and keep the mean figures ticked in opnca-r's **Save to run folder** box. |

For problems not listed here, see [Troubleshooting]({{ '/guides/troubleshooting/' | relative_url }}).
