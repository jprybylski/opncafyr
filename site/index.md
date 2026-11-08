---
title: Home
nav_order: 1
description: opncafyr is a local, per-user workspace for noncompartmental analysis. Clean a concentration file, run one analyte at a time in opnca-r, and publish completed runs to a Word report or a PowerPoint deck.
permalink: /
---

<div class="hero">
  <img class="hero-mark" src="{{ '/assets/images/logo-mark.svg' | relative_url }}" width="112" height="112" alt="" aria-hidden="true">
  <div class="hero-text">
    <h1>Noncompartmental analysis, one analyte at a time, on your own computer</h1>
    <p>opncafyr is a workspace that runs on your own computer. It takes a concentration file through cleaning, mapping and checks, runs one analyte at a time in opnca-r, and turns completed runs into a Word report or a PowerPoint deck. Every step is recorded so it can be replayed.</p>
    <a class="btn btn-hero" href="{{ '/get-started/' | relative_url }}">Get started</a>
    <a class="btn btn-ghost" href="{{ '/get-started/walkthrough/' | relative_url }}">Try the walkthrough</a>
  </div>
</div>

{: .research }
> **Research use only.** This workspace has not been validated for clinical or regulatory decisions. When a report or deck builds without errors, that shows the files were assembled as recorded. It does not show that the analysis is right.

## Install on your computer

opncafyr installs for your user account only, with no administrator rights. Pick your system:

<ul class="cards">
  <li><a href="{{ '/get-started/install-macos/' | relative_url }}"><strong>macOS</strong>Install from Terminal; the app appears in your Applications folder.</a></li>
  <li><a href="{{ '/get-started/install-windows/' | relative_url }}"><strong>Windows</strong>Run the installer; shortcuts appear on the Desktop and in the Start Menu.</a></li>
  <li><a href="{{ '/guides/offline-install/' | relative_url }}"><strong>No internet access?</strong>Install from a verified offline bundle on a firewalled machine.</a></li>
</ul>

Check [Before you install]({{ '/get-started/before-you-install/' | relative_url }}) first: opncafyr is not publicly released yet, and the installer needs access to the opnca-r release.

## How the work flows

Each step happens in the app, in your web browser, and nothing leaves your computer.

<ol class="steps">
  <li>
    <strong>Choose a study folder</strong>
    <img src="{{ '/assets/screenshots/01-home.png' | relative_url }}" width="1280" height="1296" loading="lazy" alt="The app's Home page with the study folder named at the top and cards for data, runs and publishing.">
    Everything the app records about a study lives in that folder.
  </li>
  <li>
    <strong>Import and clean</strong>
    <img src="{{ '/assets/screenshots/03-cleaning.png' | relative_url }}" width="1014" height="1277" loading="lazy" alt="The Cleaning steps card listing steps, each with the row count before and after it.">
    Your source file is never changed. Every cleaning step shows its row counts, and removed rows go to a rejected-row log.
  </li>
  <li>
    <strong>Map columns and check</strong>
    <img src="{{ '/assets/screenshots/05-checks.png' | relative_url }}" width="1016" height="885" loading="lazy" alt="The checks card listing blocking findings, findings to acknowledge and notes for one analyte.">
    Say what each column means, choose exactly one analyte, and fix what the checks find.
  </li>
  <li>
    <strong>Run it in opnca-r</strong>
    <img src="{{ '/assets/screenshots/06-opnca.png' | relative_url }}" width="1280" height="800" loading="lazy" alt="opnca-r's Results view showing concentration-time profiles for one analyte.">
    opnca-r does the calculations. The saved run is replayed and checked before it counts as complete.
  </li>
  <li>
    <strong>Publish</strong>
    <img src="{{ '/assets/screenshots/08-publish.png' | relative_url }}" width="1016" height="1100" loading="lazy" alt="The Publish page with completed runs and their tables and figures ticked for publishing.">
    Pick completed runs and the tables and figures to publish. The publication records exactly which runs and files it used.
  </li>
  <li>
    <strong>Build a report or deck</strong>
    <img src="{{ '/assets/screenshots/10-deck.png' | relative_url }}" width="1016" height="395" loading="lazy" alt="The deck card after a build, listing the PowerPoint file and its check results.">
    Build a Word report or a PowerPoint deck from a publication, then review it.
  </li>
</ol>

## Two guides, one set of instructions

- **This website** helps before the app is running: installing, updating, offline installs, and reference pages you can share.
- **The app's own Guide** (left-hand navigation, **Guide**) has the same core steps beside your work. It ships with the app and works without a network.

New to opncafyr? The [two-analyte walkthrough]({{ '/get-started/walkthrough/' | relative_url }}) uses a small, made-up practice file and takes you from an empty folder to a report and a deck.
