---
title: Study profiles
parent: Guides
nav_order: 7.6
description: Follow your team's agreed steps for a kind of study, such as dose escalation, fed/fasted or a drug-drug interaction study, with progress kept in the study folder.
permalink: /guides/study-profiles/
---

# Study profiles

Follow your team's agreed steps for a kind of study, with progress kept in the study folder.
{: .fs-5 .fw-300 }

{: .research }
> **Research use only.** A study profile is guidance. It does not check, approve or validate the analysis. opnca-r does the NCA.

A study profile lists what to check, which runs to make and which runs to publish together for a kind of study, such as a dose escalation, fed/fasted or drug-drug interaction study. The **Study profile** page shows each step, opens the right card on the **Analysis** or **Publish** page, and keeps your progress.

A profile never chooses an analyte, a dose, a unit, a BLQ rule, an exclusion or a comparison, and it never starts, changes or publishes a run. Each run still has exactly one analyte.

## Start from an example

1. Open a study folder, then select **Study profile** on the left.
2. Under **Examples**, select **Copy into this study** for the closest example: **Dose escalation study**, **Fed/fasted study** or **Drug-drug interaction study**. The copy is saved as `profiles/<name>.yaml`; an existing file is never replaced.
3. Edit the copy in any text editor so it matches your protocol and analysis plan, then select **Check again**.
4. Choose it under **Profile in profiles/** and select **Follow this profile**.

{: .expect }
If the file has a mistake, the page lists each problem with the step and the field it is in, and the profile can't be followed until it is fixed.

## Follow the steps

- **Instructions** and **review checkpoints**: tick them when done.
- **Checklists**: tick each item.
- **Actions**: **Go to …** opens the right card. A hint may say whether the page shows the step as done, such as "The recipe is applied to all rows: not yet". A hint never ticks the step for you.
- **Runs**: choose the completed run for each slot yourself. Only completed runs can fill a slot, and each run fills one slot.
- **Publications**: publish tables and figures from exactly the runs in the step's slots, then choose that publication. The app checks that it holds exactly those runs.

Progress is saved in the study folder, so it is still there the next time you open the app.

While you follow a profile, **Start analysis** and **Choose tables and figures** offer **Study profile step (optional)**, set to **None**. Choosing a step records the profile's name, version, fingerprint and step with the new run or publication, as provenance. It does not change the analysis.

## When someone edits the profile

The page lists the steps an edit added and removed. Nothing can be ticked until you select **Adopt edited profile**. Adopting keeps the progress of steps that still exist. Runs and publications made earlier keep the version they recorded.

## Write your own

A profile is a short YAML file of steps. Each step is an instruction, a checklist, an action that points at a card, a run slot, a review checkpoint or a publication of named slots. It can't hold code, web addresses, file paths, commands or scientific choices. The in-app **Guide** has the full format, with every card and hint a profile can name, under **Study profiles (optional)**.
