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
2. Under **Examples**, select **New from example** for the closest example: **Dose escalation study**, **Fed/fasted study** or **Drug-drug interaction study**. The profile editor opens with a copy of it.
3. Name the file, change the steps to match your protocol and analysis plan, and select **Save as new study profile**. It is saved as `profiles/<name>.yaml`; an existing file is never replaced.
4. Choose it under **Profile in profiles/** and select **Follow this profile**.

{: .expect }
The editor checks the profile as you type and lists each problem with the step it is in. **Save** writes the file only when nothing is wrong.

## Edit a profile in the app

Select **Edit** for the chosen profile, or **Edit to fix** beside one that can't be used. Add, move and remove steps with buttons, and choose the card an action points at from a list, by name. Saving writes only that file, without its comments, and never changes a run, a publication or your progress. If the file changed on disk since you opened it, nothing is saved. **Quit** asks first while there are unsaved edits.

<figure class="shot">
  <img src="{{ '/assets/screenshots/18-profile-editor.png' | relative_url }}" width="982" height="468" loading="lazy"
       alt="A step in the profile editor: type Publication, its title and step id, a Runs in slots group whose parent and metab checkboxes both say no run step above has this slot, two problems under the step saying each slot comes after this step, and Move up, Move down and Remove buttons.">
  <figcaption>The publication step was moved above its run steps. The editor names the problem on the step itself; moving it back clears it.</figcaption>
</figure>

{: .fix }
The editor opens only a well-formed file. A file with a YAML mistake shows its problems instead: fix them in a text editor, then select **Check again**. **Copy into this study** copies an example unchanged for editing outside the app.

## Follow the steps

- **Instructions** and **review checkpoints**: tick them when done.
- **Checklists**: tick each item.
- **Actions**: **Go to …** opens the right card. A hint may say whether the page shows the step as done, such as "The recipe is applied to all rows: not yet". A hint never ticks the step for you.
- **Runs**: choose the completed run for each slot yourself. Only completed runs can fill a slot, and each run fills one slot.
- **Publications**: publish tables and figures from exactly the runs in the step's slots, then choose that publication. The app checks that it holds exactly those runs.

Progress is saved in the study folder, so it is still there the next time you open the app.

<figure class="shot">
  <img src="{{ '/assets/screenshots/16-study-profile.png' | relative_url }}" width="1016" height="1070" loading="lazy"
       alt="The Steps card of a study profile with 5 of 6 steps done: a ticked checklist, a Clean and map the data step with a Go to button and the hint 'The saved column mapping blocks nothing: yes', a PARENT run slot and a METAB run slot each filled with a completed run, a Review both runs checkpoint still to do, and a publication holding both runs.">
  <figcaption>Each run slot holds a completed run you chose; the publication step checks that it holds exactly those runs.</figcaption>
</figure>

While you follow a profile, **Start analysis** and **Choose tables and figures** offer **Study profile step (optional)**, set to **None**. Choosing a step records the profile's name, version, fingerprint and step with the new run or publication, as provenance. It does not change the analysis.

## When someone edits the profile

Whether the edit was made in the app or outside it, the page lists the steps an edit added and removed. Nothing can be ticked until you select **Adopt edited profile**. Adopting keeps the progress of steps that still exist. Runs and publications made earlier keep the version they recorded.

## Write your own

A profile is a short YAML file of steps. Each step is an instruction, a checklist, an action that points at a card, a run slot, a review checkpoint or a publication of named slots. It can't hold code, web addresses, file paths, commands or scientific choices. The in-app **Guide** has the full format, with every card and hint a profile can name, under **Study profiles (optional)**.
