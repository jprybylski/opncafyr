---
title: Standards bundles
parent: Guides
nav_order: 7.8
description: Import your team's versioned study profiles, tutorials and report and deck templates from a folder, an archive or a GitHub or GitHub Enterprise repository, checked file by file and kept in the study for offline use.
permalink: /guides/standards/
---

# Standards bundles

Import your team's study standards into a study, checked file by file, and keep using them offline.
{: .fs-5 .fw-300 }

{: .research }
> **Research use only.** A standards bundle is guidance and formatting. Importing one never chooses an analyte, changes a setting, starts or changes a run, or validates an analysis. opnca-r does the NCA.

A **standards bundle** is a versioned set of study standards your team shares: [study profiles]({{ '/guides/study-profiles/' | relative_url }}), [tutorials]({{ '/guides/tutorials/' | relative_url }}), and report and deck templates, in any combination. It is a folder with a `standards.yaml` that lists every file with its SHA-256. You can import it from a folder, a `.zip` or `.tar.gz` archive, or a GitHub or GitHub Enterprise repository.

## Import one

1. Open the study folder and select **Standards** on the left.
2. Keep **A standards bundle**, then choose **A folder or archive on this computer** or **A Git repository**.
   - For a folder or archive, type its full path, or a path inside the study folder.
   - For a repository, type `owner/repo`, `host/owner/repo` (GitHub Enterprise) or an `https://` address, and optionally a branch, tag or commit and a subfolder. For a private repository, paste an access token that can read it.
3. Select **Check and preview**, review, then select **Install**.

{: .expect }
The review shows where the bundle came from (with the exact commit for Git), its SHA-256, what it needs, every component and file, and what each file would do in the study: **New**, **Unchanged**, **Update** or a conflict. Nothing is written until you select **Install**.

{: .fix }
If the review lists problems, such as a file that doesn't match its SHA-256, a missing file, a link, or a version this app doesn't fit, nothing can be installed. Fix the bundle at its source and check again. A failed import changes nothing in the study.

The token is used for that one fetch, then forgotten: it is never saved, logged, shown or put in the address. Only HTTPS works. Git sources need the Git that comes with the app; without it, import a local copy.

## Your edits are kept

Study profiles go into `profiles/` and tutorials into `tutorials/`. A newer version updates a copy you haven't edited. If you edited a file, or the study already has a file of that name, the app asks: **Keep mine**, or install the bundle's version beside it (for example `dose-escalation-2-0-0.yaml`). **Show the differences** compares them. Nothing is chosen for you.

## Report and deck templates

Templates stay in the bundle's fixed copy until you choose one for **new** reports or decks, in the review or under **Imported**. **Templates for new reports and decks** shows the current choice; **Use the bundled one** goes back to the templates that come with the app.

A report or deck already set up keeps the template it started from, and its card on **Publish** says so. Each build records which template it used, with its fingerprint and source commit, next to the exact runs and files it was built from.

- A **report template** may hold `org-reference.docx`, `standard_footnotes.yaml`, `config.yaml` and quartifyr's `style.yaml`; anything it lacks keeps the bundled file.
- A **deck template** is a deckifyr template (`presentation.yaml` naming its design and layouts files, plus images). The layouts must include `blank` and `title-content`, which the "NCA results" deck uses.

## One house style for every bundle

You don't have to copy your templates into each bundle. A bundle component can point at a template instead:

- in **another bundle**, such as a "house style" bundle, by its id and a version range (optionally pinned by SHA-256). Import the house style once and every bundle that points at it uses it;
- in **deckifyr's or quartifyr's own layout**, such as the template repository your team already keeps for those tools, by repository, ref, subfolder and type (optionally pinned by commit and SHA-256). Import it with the matching **What to import** option.

Until the template is imported, the review says what to import; the rest of the bundle installs anyway.

## Offline and versions

Every import is kept, read-only, in the study's `.opncafyr/standards/` folder and checked against its SHA-256 each time it is used. A new version or commit is kept beside the older one. Nothing is fetched unless you select **Check and preview** for a Git source: no background fetch, update check or upload. For a computer without internet access, import from an archive, or copy a study that already holds the import.

## Write a bundle

```yaml
schema_version: "1.0"
id: house-standards
version: "2.1.0"
title: House NCA standards
requires:
  opncafyr: ">=0.1"
components:
  - id: dose-escalation
    type: profile
    title: Dose escalation
    files:
      - path: profiles/dose-escalation.yaml
        sha256: 3b0c…     # the file's SHA-256
    links: [house-report]
  - id: house-report
    type: report-template
    title: House report
    ref:                   # kept in the house style bundle
      bundle: house-style
      version: ">=1.0, <2"
      component: report
variants:
  - id: profile-only
    title: Profile only
    components: [dose-escalation]
```

Component types are `profile`, `tutorial`, `report-template` and `deck-template`. A file without a SHA-256 entry is never copied, so keep study data out of the bundle's repository. The in-app guide (**Guide**, then **Standards bundles**) has a full example, including a reference to a template in deckifyr's own layout.
