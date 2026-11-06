# Manual test: native workspace picker (#17)

The unit tests (`tests/test_picker.py`, `tests/test_workspace_api.py`) mock the OS dialog.
They check the exact argv, that user text never becomes script text, and the cancel and
error handling. This checklist covers the part a mock can't: the real dialog on each OS.

## Setup
1. `uv run opncafyr launch` (or start the installed app from its icon).
2. On the home page, use **Study folder → Choose study folder…**.

## Checks
| # | Step | Expected |
|---|---|---|
| 1 | Click *Choose study folder…* | The native folder dialog opens **in front of** the browser, titled/prompted "Choose or create a study folder" |
| 2 | Cancel the dialog | Page says "No folder chosen."; nothing is created anywhere |
| 3 | Pick an existing folder that has files in it | Panel shows the full path, "Can write here: Yes", the item count and "Create workspace here"; still nothing created |
| 4 | Click *Create workspace here* | Page says "Opened <name>." The folder gains only `.opncafyr/workspace.json`; every other file is unchanged |
| 5 | Pick again, use the dialog's **New Folder** button, choose the new folder | Panel shows state "not a workspace yet", 0 items; create works |
| 6 | Pick a folder whose name has non-ASCII characters (e.g. `Étude 研究`) | The path is shown correctly and the workspace opens |
| 7 | Reload the page | The workspace appears under *Recent study folders*; clicking it shows the panel without opening the picker |
| 8 | Click *Choose…* twice quickly | The second request says "A folder picker is already open." |
| 9 | Pick a read-only folder (Windows: a folder you only have read rights to) | "Can write here: No", and *Create* gives "You don't have permission…" with nothing created |
| 10 | Edit `.opncafyr/workspace.json` to `"schema_version": "2.0"` and pick it | The panel explains it was made by a newer opncafyr and there is no open button |

## Results
| Date | OS / version | Tester | Result | Notes |
|---|---|---|---|---|
| 2026-11-06 | macOS 26 (arm64) | Claude (automated, partial) | Partial | The controller started `/usr/bin/osascript` with the fixed script and the prompt as argv, and the dialog stayed open waiting for input. Killing it returned `picker_failed`; an unpicked path got `not_approved`. Checks 1–10 still need a person to click. |
| | Windows 11 | | Not run | |
