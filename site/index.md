---
title: Home
nav_order: 1
description: opncafyr is a version-pinned workspace for noncompartmental analysis and reporting that installs for your user account without administrator rights. Prepare a concentration file, run one analyte at a time in opnca-r, and publish completed runs to a Word report or a PowerPoint deck.
permalink: /
---

<div class="hero">
  <img class="hero-mark" src="{{ '/assets/images/logo-mark.svg' | relative_url }}" width="112" height="112" alt="" aria-hidden="true">
  <div class="hero-text">
    <h1>One workspace for NCA, reports and decks, without assembling the tools yourself</h1>
    <p>opncafyr brings the analysis and reporting tools together in one version-pinned workspace. On supported systems you install it for your own user account, without administrator rights, from verified downloads or from an offline bundle your IT team prepares; internal mirrors work too. It can also run on a supported Linux cloud computer, beside your data. Prepare a CSV, run one analyte at a time in opnca-r, and build a Word report or PowerPoint deck from recorded, replayable runs.</p>
    <a class="btn btn-hero" href="{{ '/get-started/' | relative_url }}">Get started</a>
    <a class="btn btn-ghost" href="{{ '/get-started/walkthrough/' | relative_url }}">Try the walkthrough</a>
  </div>
</div>

{: .research }
> **Research use only.** This workspace has not been validated for clinical or regulatory decisions. When a report or deck builds without errors, that shows the files were assembled as recorded. It does not show that the analysis is right.

## Set up without administrator rights

The installer sets opncafyr up for your user account only. It brings its own pinned copies of Python, R, Quarto and the tools, checks every file against a fingerprint built into the installer, and leaves the rest of the computer alone. The files can come from the internet, from an offline bundle, or from your organisation's internal mirrors. Pick your system:

<ul class="cards">
  <li><a href="{{ '/get-started/install-macos/' | relative_url }}"><strong>macOS</strong>Install from Terminal; the app appears in your Applications folder.</a></li>
  <li><a href="{{ '/get-started/install-windows/' | relative_url }}"><strong>Windows</strong>Run the installer; shortcuts appear on the Desktop and in the Start Menu.</a></li>
  <li><a href="{{ '/get-started/install-cloud-container/' | relative_url }}"><strong>Linux cloud computer</strong>Run it beside your data, in a container or installed on Ubuntu 24.04.</a></li>
  <li><a href="{{ '/guides/offline-install/' | relative_url }}"><strong>No internet access?</strong>Install from a verified offline bundle your IT team prepares.</a></li>
</ul>

Check [Before you install]({{ '/get-started/before-you-install/' | relative_url }}) first: opncafyr is not publicly released yet, the installer needs access to the opnca-r release, and some organisations' application controls block programs in user folders until IT allows them.

## How the work flows

Each step happens in the app, in your web browser. Your data and the tools stay on the computer that runs opncafyr: your own computer for a normal install, or the cloud computer when it runs there. Nothing is sent anywhere by default.

<ol class="steps">
  <li>
    <strong>Choose a study folder</strong>
    <img src="{{ '/assets/screenshots/01-home.png' | relative_url }}" width="1280" height="1183" loading="lazy" alt="The app's Home page with the study folder named at the top and cards for data, runs and publishing.">
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

Optionally, [version history]({{ '/guides/version-history/' | relative_url }}) keeps Git snapshots of the files you choose. It is off until you open it, stays on the computer that runs opncafyr unless you turn sending on, and never changes a run, a publication or a build.

## Two guides, one set of instructions

- **This website** helps before the app is running: installing, updating, offline installs, and reference pages you can share.
- **The app's own Guide** (left-hand navigation, **Guide**) has the same core steps beside your work. It ships with the app and works without a network.

New to opncafyr? The [two-analyte walkthrough]({{ '/get-started/walkthrough/' | relative_url }}) uses a small, made-up practice file and takes you from an empty folder to a report and a deck.
