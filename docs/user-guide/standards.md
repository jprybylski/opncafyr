# Standards bundles (optional)

A standards bundle is your team's shared, versioned set of study standards: study profiles, tutorials, and report and deck templates, in any combination. You import one into a study from a folder, an archive or a Git repository. The app checks every file before anything is installed, keeps a fixed copy in the study, and uses it offline from then on.

A standard is guidance and formatting. Importing one never chooses an analyte, changes a setting, starts or changes a run, or validates an analysis. opnca-r does the NCA, and each run still has exactly one analyte.

## Import a bundle

1. Open the study folder, then select **Standards** on the left.
2. Under **What to import**, keep **A standards bundle**.
3. Under **Where from**, choose:
   - **A folder or archive on this computer**: type the folder, or a `.zip`, `.tar` or `.tar.gz` file. A full path works, and so does a path inside the study folder, such as `incoming/house-standards.zip`.
   - **A Git repository**: type the repository as `owner/repo`, `host/owner/repo` for GitHub Enterprise, or an `https://` address. Add a branch, tag or commit and a subfolder if the bundle isn't at the top. For a private or Enterprise repository, paste an access token that can read it.
4. Select **Check and preview**.

The token is used for that one fetch, then forgotten. It is never saved, logged or shown, and it isn't put in the address. Only HTTPS addresses work; an address with a user name or password in it is refused. Git sources need the Git that comes with the app. Without it, import a local copy instead.

## Review, then install

The review lists:

- where the bundle came from, including the exact commit of a Git source, and its SHA-256;
- what the bundle needs (an app version, file formats) and whether this app fits;
- each component, its files and their SHA-256, and what installing it would do in the study: **New**, **Unchanged**, **Update** (a copy from an earlier import that you haven't edited) or a conflict;
- problems that stop the import, such as a file that doesn't match its SHA-256, a missing file, a file reached through a link, a path outside the bundle, or a broken link between components. Nothing is installed until the bundle is fixed.

A conflict is a file you edited after an earlier import, or a file of the same name the study already has. The app never replaces it. Choose **Keep mine**, or install the bundle's version beside it (for example `profiles/dose-escalation-2-0-0.yaml`). **Show the differences** shows both. **Install** stays greyed out until you have chosen for every conflict.

Untick a component to leave it out. A component brings the ones it goes with, such as a profile's tutorial. A bundle can also offer named variants, such as "dose escalation" or "DDI".

Select **Install**. Study profiles go into `profiles/` and tutorials into `tutorials/`. Use them from the **Study profile** and **Tutorials** pages as usual (see [Study profiles](study-profiles.md) and [Tutorials](tutorials.md)).

## Report and deck templates

Templates are not copied into `profiles/` or anywhere you edit. They stay in the bundle's fixed copy until you choose them:

- tick **Use it for new reports** or **Use it for new decks** in the review, or
- select **Use for new reports** or **Use for new decks** under **Imported**.

**Templates for new reports and decks** shows what the next setup will use. **Use the bundled one** goes back to the templates that come with the app.

The choice applies when a report or deck is **set up**. The template of a report or deck that is already set up can't be changed: it keeps the template it started from, and **Use this publication** doesn't change that either. When the study already has one, the **Standards** page says so beside each choice, and the card on the **Publish** page says which template the project uses and when Standards now chooses another. To use the new template, rename or move the `report` (or `deck`) folder, then select **Set up report** (or **Set up deck**). See [Word reports](reports.md) and [PowerPoint decks](decks.md).

- A **report template** can hold `org-reference.docx` (the Word styles), `standard_footnotes.yaml`, `config.yaml` and quartifyr's `style.yaml`, which may hold only what differs from quartifyr's default style. Anything it doesn't hold keeps the bundled file. The publication's tables and figures are checked against the template's footnotes.
- A **deck template** is a deckifyr template: its `presentation.yaml` names a design file and a layouts file, and it can hold logos and images. The app writes its own "NCA results" deck with the template's design and layouts, so the layouts must include `blank` and `title-content`.

## A house style shared by many bundles

Your team doesn't have to copy its report and deck templates into every bundle. A bundle can point at a template it doesn't carry:

- **another bundle's template**, such as a "house style" bundle. Import the house style once; every bundle that points at it uses it.
- **a template kept in deckifyr's or quartifyr's own layout**, for example in the repository your team already uses for those tools. Import it with **What to import** set to the deck or report template option, from Git or a local copy. Give the template type if the source has `templates/<type>/` folders. Under **Expected commit or SHA-256** you can give the values the bundle pins.

Until the template it points at is imported, the review and the **Imported** list say what to import. The rest of the bundle installs anyway.

## Versions and working offline

Importing a new version or commit keeps the earlier one, and anything that uses it keeps working. A failed import changes nothing. Once imported, a bundle needs no network; to move a study to a computer without internet access, copy the study folder, or import from an archive.

Each imported copy is kept in `.opncafyr/standards/` and checked against its SHA-256 every time it is used. If its files changed, setup stops and asks you to import it again or choose another template.

## Write a bundle

A bundle is a folder with a `standards.yaml` that lists every file with its SHA-256:

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
        sha256: 3b0c…   # sha256sum profiles/dose-escalation.yaml
    links: [first-analysis, house-report]
  - id: first-analysis
    type: tutorial
    title: Your first analysis
    files:
      - path: tutorials/first-analysis.yaml
        sha256: 9f12…
  - id: house-report
    type: report-template
    title: House report
    ref:                       # kept in the house style bundle, not here
      bundle: house-style
      version: ">=1.0, <2"
      component: report
variants:
  - id: profile-only
    title: Profile and tutorial only
    components: [dose-escalation, first-analysis]
```

A template kept in a tool's own layout is referenced like this:

```yaml
    ref:
      tool: deckifyr
      repo: ghe.example.org/team/deck-templates
      ref: v3.2.0
      subdir: decks
      type: nca            # templates/nca/
      commit: 1f0e…        # optional pins
      sha256: 77aa…
```

Files with no SHA-256 entry are not copied, so a bundle never brings data along. Keep study data out of the bundle's repository.
