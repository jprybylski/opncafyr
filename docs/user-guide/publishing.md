# Publishing tables and figures

Publishing copies chosen tables and figures from completed runs into the study folder's `OUTPUTS` folder. Reports and decks are then built from those copies. Publishing never changes a run.

All of this happens on the **Publish** page.

## Which runs can be published

Only **Completed** runs can be published: runs whose replay matched, that you confirmed, and whose files still match their recorded fingerprints. Any other run is listed under a table of runs that can't be published, with the reason.

One publication can combine runs of different analytes.

## 1. Choose tables and figures

![The Publish page, choosing tables and figures from two completed runs](images/08-publish.png)

Each completed run has a box with its analyte, its run ID, when it was made, and "Replay passed". Inside are the tables and figures opnca-r saved:

| Offered | Ticked to begin with | Shows subject IDs |
|---|---|---|
| Parameter summary (table) | Yes | No |
| Individual parameter listing (table) | Yes | Yes |
| Individual concentration–time profiles, linear and log (figures) | Yes | Yes |
| Mean concentration–time profiles, linear and log (figures) | Yes | No |
| Mean concentration–time table | No | No |
| Parameter summary by group (table) | No | No |
| Boxplots of parameters (figures) | No | No |

Items that show subject IDs are marked **shows subject IDs**. If a run was saved without the mean figures, the box says so; make a new run and keep them ticked in opnca-r's **Save to run folder** box to include them.

1. Tick the items you want, from one run or several.
2. Type a **Label (optional)**, for example "PARENT and METAB".
3. Select **Check**. The app shows the name each file will have in `OUTPUTS`, which run it comes from, whether it shows subject IDs, and whether it is a **New copy** or **Already published (same bytes)**. Nothing is written yet.
4. If the check is clean, select **Publish**. If you change the selection after checking, check again first.

The message then names the new publication and counts its files and runs, for example "12 file(s) from 2 run(s)".

## What publishing does

- It copies each file into `OUTPUTS/tables` or `OUTPUTS/figures`. The name holds the analyte, the run ID and the item. The copy is byte for byte the same as the run's file.
- It writes a small description file next to each copy. Reports and decks use it for footnotes: where the result came from, the analyte and run, and abbreviations.
- It checks every copy and description file.
- It records the publication, with the exact runs and fingerprints of every file it used. Reports and decks always name this record, never "the latest" run.

A published file is never replaced. If a file name in `OUTPUTS` already holds something different, for example because someone edited the file, the whole publication is refused and nothing is written. Make a new run, or ask for help.

If publishing is interrupted, the files already copied are not part of any publication, and reports and decks never use them.

## 2. Publications

This card lists every publication: its ID, label, analytes, number of files and when it was made. A report or deck is built from one publication.

## 3. Published files

Select **Check published files** to check everything in `OUTPUTS`: that each file has its description file, and that they still match. The card lists any problem, with **Blocks** or **Warning**. Run this check if you suspect a file in `OUTPUTS` was changed.

Next: [Word reports](reports.md) and [PowerPoint decks](decks.md).
