---
title: Two-analyte walkthrough
parent: Get started
nav_order: 5
has_children: true
has_toc: false
description: A practice exercise that takes a made-up two-analyte CSV from an empty folder to a Word report and a PowerPoint deck.
permalink: /get-started/walkthrough/
---

# Two-analyte walkthrough

Practise the whole workflow on a small, made-up file: import and clean it, map it, run each analyte, and publish a report and a deck.
{: .fs-5 .fw-300 }

This exercise takes you from an empty folder to a Word report and a PowerPoint deck. It has four parts, and each part builds on the one before.

{: .research }
> **Research use only.** This workspace has not been validated for clinical or regulatory decisions.

## The practice file

Download the practice file: <a href="{{ '/assets/example/two_analyte.csv' | relative_url }}" download>two_analyte.csv</a> (168 rows, 21 columns).

The file is made up. It has 10 subjects (`TA-001` to `TA-010`). Each subject has results for two analytes (the substances measured): a parent drug, `PARENT`, and a metabolite, `METAB`. The analyte codes are in the column `PCTESTCD`.

Problems have been planted in the file on purpose, so you can see how the app finds them and how you fix them. Among them:

- a blank concentration that is flagged neither BLQ (below the lower limit of quantification) nor missing;
- a negative concentration, and a concentration written with a decimal comma;
- a time written as `~8` instead of a number;
- a blank route and a misspelt route;
- an exact duplicate row, and two rows for the same sample with different concentrations;
- a row flagged both BLQ and missing;
- a subject with no dose record;
- a metabolite code with a trailing space, `"METAB "`, on one row.

You will meet each one, with its line number, in [part 2]({{ '/get-started/walkthrough-map/' | relative_url }}).

{: .note }
> The choices in this exercise, such as which rows to remove and the BLQ rules, are there to practise the app. They are not advice for a real study.

## How long it takes

Set aside about an hour. You can stop at any point and come back: everything you do is saved in the study folder.

## Before you start

1. Install opncafyr: see [Install on macOS]({{ '/get-started/install-macos/' | relative_url }}) or [Install on Windows]({{ '/get-started/install-windows/' | relative_url }}).
2. Start the app. On **Home**, the **Tools** card should show opnca-r, quartifyr + reportifyr and deckifyr as **Available**.

{: .fix }
> **A tool shows Unavailable** — see [Troubleshooting]({{ '/guides/troubleshooting/' | relative_url }}).

If you have not used the app before, [opening the app and choosing a study folder]({{ '/get-started/first-study/' | relative_url }}) explains the **Home** page.

## The four parts

1. [Import and clean]({{ '/get-started/walkthrough-import/' | relative_url }}): make the study folder, preview and import the file, and add cleaning steps that remove or exclude the problem rows, each with a reason.
2. [Map and check]({{ '/get-started/walkthrough-map/' | relative_url }}): say what each column means, set the units and flag values, choose exactly one analyte, and read the findings the checks report.
3. [Run each analyte]({{ '/get-started/walkthrough-run/' | relative_url }}): start a run for `PARENT`, analyse and save it in opnca-r, replay and complete it, then do the same for `METAB`.
4. [Publish, report and deck]({{ '/get-started/walkthrough-publish/' | relative_url }}): publish the tables and figures of both completed runs, then build a Word report and a PowerPoint deck from them, and optionally keep a snapshot of your work.

{: .tip }
> The app's **Guide** (in the left navigation) has the same exercise and works without a network. Its copy of the practice file can be downloaded from inside the app.

The screenshots on these pages were taken in a practice session with the same file. Your study folder's name and some labels may differ from what they show.

[Start part 1: Import and clean]({{ '/get-started/walkthrough-import/' | relative_url }}){: .btn .btn-primary }
