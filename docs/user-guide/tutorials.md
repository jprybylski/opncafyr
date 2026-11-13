# Tutorials

A tutorial walks you through the app. A panel in the corner of the page says what to do, a ring highlights the control to use, and the tutorial waits for you to do the step. You do every step yourself: a tutorial never clicks, chooses, runs or publishes anything. It is a guide to the screens. Finishing one does not show that an analysis is right.

## Take a tutorial

1. Select **Tutorials** on the left.
2. Select **Start** beside a tutorial. **Your first analysis** goes from a data file to a publication.
3. Read the step in the panel, then do it on the page. The highlighted control has a ring around it, and the page moves to the right screen for you.
4. Select **Next** when you are done. Some steps wait until the page shows the step as done, such as "the study has an imported data file". Until then **Next** is greyed out and the panel says what it waits for. **Skip** always moves on.

![The tutorial panel on the step Choose exactly one analyte, with a ring around the analyte select and Next waiting for the checks](images/17-tutorial.png)

The panel also has **Back**, **Pause** and **Exit tutorial**. **Escape** closes the panel too.

- **Pause** removes the highlight and shrinks the panel. **Resume** carries on from the same step.
- **Exit tutorial** closes the panel and keeps your place. **Resume** on the Tutorials page brings it back.
- Your place is saved by the app, not the browser, so it is still there after a reload or the next time you open the app. **Reset progress** forgets it. **Reset all tutorial progress** forgets every tutorial's place.

## When the control isn't there

Some controls appear only after an earlier step. For example, **Import** shows only after a preview. Others may not be in this version of the app. The panel then says the control isn't on the page right now, and offers two ways on:

- **Look again** looks for it once more.
- **Continue without highlight** keeps the directions without the ring.

Inside an app with its own screens, such as opnca-r, the tutorial can't point at controls. It gives directions in the panel instead.

## Write your own

1. Under **Examples**, select **Copy into this study**. The copy is saved as `tutorials/<name>.yaml` in the study folder. Copying never replaces a file.
2. Edit it in any text editor, then select **Check again** on the Tutorials page. If the file has a mistake, the page lists each problem with the step and field it is in.

```yaml
schema_version: "1.0"
id: our-first-run          # lower-case letters, digits and dashes
version: "1"
title: Our team's first run
audience: New analysts      # optional
prerequisites:              # optional
  - The study's data file is in source-data.
profile: fed-fasted         # optional: the study profile this goes with
steps:
  - id: mapping
    title: Map the columns
    view: analysis               # optional: the page to show
    target: nca.mapping.card     # optional: the control to highlight
    wait_for: nca.mapping.ready  # optional: Next waits for this
    text: Choose the column for each role, declare the units and save the mapping.
```

Each step needs an `id`, a `title` and plain `text`. It can also name one `view`, one `target` and one `wait_for` condition. Nothing else is allowed: a tutorial has no place for HTML, scripts, web addresses, file paths or commands, and the app refuses a file that tries to add one.

- **Views**: `home`, `data`, `jobs`, `settings`, `guide`, `tutorials`, `profile`, `analysis`, `publish`, `history`.
- **Targets and conditions**: the same names as in a study profile. See [Study profiles](study-profiles.md#targets-and-hints). The left-side links are targets too, such as `shell.nav.data` or `shell.nav.analysis`. So are the jobs list (`shell.jobs.list`) and the bar above an embedded app (`shell.tool.toolstrip`).

A file can be at most 256 kB. Your place in a study's tutorial is kept by its file name. If the file changes, the panel says so and carries on from the same step if it still exists.
