---
title: "3. Run each analyte"
parent: Two-analyte walkthrough
grand_parent: Get started
nav_order: 3
description: "Walkthrough part 3: start a PARENT run, analyse and save it in opnca-r, replay and complete it, then do the same for METAB."
permalink: /get-started/walkthrough-run/
---

# 3. Run each analyte

Start one run per analyte, analyse it in opnca-r, and complete it once the app has replayed it.
{: .fs-5 .fw-300 }

This is part 3 of the [two-analyte walkthrough]({{ '/get-started/walkthrough/' | relative_url }}). Before you start, you should have finished [part 2]({{ '/get-started/walkthrough-map/' | relative_url }}): the recipe gives "All 168 rows: 159 kept, 9 rejected.", and the `PARENT` plasma checks show no blocking findings, with all three warnings ticked.

A run is one analyte's analysis. It holds a frozen copy of that analyte's rows, your mapping and BLQ choices, the warnings you acknowledged, and later opnca-r's results. opnca-r does every calculation; the app never calculates NCA (non-compartmental analysis) parameters itself.

## Step 8. Start the PARENT run

BLQ (below the lower limit of quantification) samples need a rule. There is no default: opnca-r uses exactly what you pick.

In **6. Start analysis**:

1. **Label for this analyte (shown in reports)** shows `Parent drug`. Keep it.
2. **BLQ before the dose**: choose **Set to zero**.
3. **BLQ after the dose**: choose **Exclude the sample**.
4. **BLQ at the end of the profile**: choose **Exclude the sample**.
5. **BLQ method**: choose **Conventional (rules above)**.
6. Select **Start analysis**.

{: .expect }
> The card says "Run … created.", and the run appears in **7. Runs** as **Preparing**.

{: .fix }
> **Start analysis is greyed out** — the line above it says why:
> - "Run the checks first." — select **Run checks** in card **5. Analyte and checks**.
> - "… blocking findings: change the recipe or mapping." — go back to [part 2]({{ '/get-started/walkthrough-map/' | relative_url }}) and fix the recipe.
> - "… warnings still to acknowledge." — tick **I have reviewed this and want to go on** for each warning.
> - "Choose all three BLQ rules and the BLQ method." — fill in the three BLQ choices and the method.

{: .note }
> **Start analysis** freezes this analyte's rows and your choices into a new run. It does not open opnca-r yet. For how the app handles BLQ results, see [BLQ results]({{ '/guides/cleaning/' | relative_url }}#blq-results).

## Step 9. Analyse PARENT in opnca-r and save

1. In **7. Runs**, select **Open in opnca-r** on the new run. opnca-r opens inside the app.
2. Look at **Data** and **Profiles** if you like. The data is the 77 `PARENT` plasma rows, already mapped.
3. Go to **Analyze**. Select the **Standard NCA** preset, then **Run analysis**.
4. Look at **Results**. Subject `TA-008` has no parameter values, because its whole profile is excluded.
5. Go to **Export**. In the **Save to run folder** box, keep **Mean concentration-time figures (linear and log) and their table** ticked.
6. Set **Time basis** to **Nominal time**. Keep **Statistic** as **Arithmetic mean** and **Error** as **SD**.
7. Select **Save to run folder**.

<figure class="shot">
  <img src="{{ '/assets/screenshots/06-opnca.png' | relative_url }}" width="1280" height="800" loading="lazy"
       alt="opnca-r open inside the app on a PARENT run, showing Results: 10 profiles and 5 parameters, a notice that one profile has no Cmax, Tmax, AUClast or lambda-z, and an overlay of the concentration–time profiles. Its views Data, Profiles, Analyze, Results and Export are listed on its left.">
  <figcaption>opnca-r runs inside the app. One profile has no values because its whole profile is excluded.</figcaption>
</figure>

{: .warning }
> Do not use opnca-r's **Download** buttons to save a run. The app only accepts what opnca-r writes into the run folder with **Save to run folder**.

{: .fix }
> - **opnca-r will not save** — choose a **Time basis** for the mean figures. opnca-r will not save them until one is chosen.
> - **You need to leave opnca-r** — you can go to other pages and come back; opnca-r keeps your work. The strip above it also has **Reload** and **Open in new tab ↗**. Only one run can be open in opnca-r at a time: opening another run closes the first.

## Step 10. Replay and complete PARENT

When the save is complete, opnca-r closes and the app checks the saved files. Then it replays the run: it runs the saved analysis script again on its own copy of the frozen data and compares every result table with the saved one.

1. Select **Analysis** on the left and scroll to **7. Runs**.
2. Wait for the run to reach **Reviewing**. You may first see "Run … was saved by opnca-r and is ready for review."; the replay then starts by itself.
3. Wait for "Run … replayed and matches its saved results. Confirm it to complete the run."
4. Select **Confirm and complete**.

{: .expect }
> The run is now **Completed**, and the card says "Run … is completed and sealed." Its files can no longer change.

{: .fix }
> - **"Run …: the save failed."** — the run moves to **Failed**. Select **New run from this** to start again with the same analyte and choices, then open the new run in opnca-r.
> - **"Run … did not replay to the same results:"** — the app lists the differences. You can select **Replay again**. A run whose replay does not match cannot be completed or published.

{: .note }
> A matching replay shows that the saved script reproduces the saved numbers. It does not show that the analysis choices were right.

## Step 11. Run METAB the same way

1. In **5. Analyte and checks**, choose `"METAB"` (77 rows) and `"PLASMA"` (77 rows). Do not choose `"METAB "` with the space: that is the 1-row variant on line 147, and it stays out of this run.
2. Select **Run checks**. There should be no blocking findings and two warnings. Tick **I have reviewed this and want to go on** for each:
   - **Warning: time negative**: subject `TA-003`'s predose sample (line 39).
   - **Warning: conc zero not blq**: subject `TA-005`'s 24 h concentration is 0 with no BLQ flag (line 86).
3. A note says subject `TA-004`'s times are not in order (lines 67 and 69): its 24 h sample comes before its 12 h sample in the file. The order does not change the results. Another note says subject `TA-003` has fewer than 3 measurable samples after the dose. That is because of the `<LLOQ` results you counted as BLQ.
4. In **6. Start analysis**, the label shows `Metabolite M1`. Check that the BLQ choices are still **Set to zero**, **Exclude the sample**, **Exclude the sample** and **Conventional (rules above)**.
5. Select **Start analysis**.
6. Open the new run in opnca-r, analyse and save it as in [Step 9](#step-9-analyse-parent-in-opnca-r-and-save), then replay and complete it as in [Step 10](#step-10-replay-and-complete-parent).

{: .fix }
> **The `METAB` checks show blocking findings** — compare their line numbers with the table in [part 2, Step 5]({{ '/get-started/walkthrough-map/' | relative_url }}#read-the-findings). The matching cleaning step is missing or has the wrong line number. Fix it, select **Apply to all rows**, then **Run checks** again.

The two runs share the recipe, but each has its own frozen rows, script and results. Rows of one analyte never appear in the other's run.

## The runs list

**7. Runs** now shows two **Completed** runs: one for `"PARENT"`, one for `"METAB"`. **Home** lists them too, under **Recent runs**.

<figure class="shot">
  <img src="{{ '/assets/screenshots/07-runs.png' | relative_url }}" width="1016" height="266" loading="lazy"
       alt="The Runs card with two runs, one for the analyte METAB and one for PARENT, each with its run ID, label, the state Completed, when it was created, and the buttons Check files and New run from this.">
  <figcaption>Each completed run has <strong>Check files</strong> and <strong>New run from this</strong>.</figcaption>
</figure>

Each run has an ID made of the date and time it was created plus a short code. On a completed run:

- **Check files** checks that every file still matches the fingerprint recorded when the run was sealed. The app says "every file matches its recorded hash", or lists what changed, went missing or was added.
- **New run from this** makes a new run with the same analyte and choices, on the recipe as it is now. A completed run never changes: to change a cleaning step, the mapping, an exclusion or a BLQ rule, you make a new run. See [Workspace and run provenance]({{ '/guides/provenance/' | relative_url }}).

**Next:** [4. Publish, report and deck]({{ '/get-started/walkthrough-publish/' | relative_url }})
