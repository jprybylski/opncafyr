# 0008. Optional Git version history through a managed gitneighbr

- Status: accepted (owner, 2026-11-08) for the Git source, the scope, the upstream route
  and the default ignore suggestions. The review labels below are **proposed** until the
  owner confirms them.
- Date: 2026-11-08
- Issue: #56
- Upstream: [gitneighbr#35–#44](../upstream.md#jprybylskigitneighbr-public), released in gitneighbr 0.2.0; 0.2.1 is pinned

## Context

People working in a study folder asked for a way to review what changed, keep snapshots
of chosen files, and, when they decide to, share a repository. opncafyr already records
provenance in its own way:

- a completed run is an immutable, hash-checked bundle;
- a publication and a report or deck build pin exact run IDs and artifact SHA-256s.

Git must not replace or weaken any of that. The security rules also apply:

- every executable comes from the trusted runtime, and a study folder can never name one;
- the local URL is a security boundary;
- nothing may need the network at run time;
- no subject values go into URLs, logs or bundles.

gitneighbr (an R/plumber2 + Svelte Git helper) already offered plain-language status,
selected-file commits and guarded push and pull. As it was, it couldn't sit behind our
proxy. Its URLs assumed `/`, its Origin check and URL-fragment token didn't fit a proxy,
and it had no foreground entry point. It ran PATH git with the user's configuration,
hooks, filters and credential helpers. It also silently served an enclosing repository,
and its feature policy was enforced only in the page.

## Decision

1. **gitneighbr, in a new managed mode, as an optional adapter.** We don't build a second
   Git UI. gitneighbr 0.2.0 adds a managed mode for launchers:
   - a base path;
   - `scripts/serve.R` with a ready line and a two-token shutdown;
   - a token header the proxy injects, and refusal of cross-site requests;
   - exact-folder rules;
   - server-side feature profiles;
   - a launcher policy file;
   - preview-bound Send/Update.

   Standalone gitneighbr is unchanged. The adapter (`adapters/gitneighbr`) and the page
   (`workflows/nca/history.py`) contain no Git logic.

2. **A pinned portable Git, locked down.** `[optional.git]` pins GitHub Desktop's
   dugite-native builds for every supported platform, each with a SHA-256. It is installed
   by default, but best effort: a failure leaves the install successful, records it, and
   makes version history unavailable. Offline bundles include it.

   PATH git is never used. gitneighbr runs this Git with:
   - none of the computer's or the user's own configuration, and an app-owned config
     file for name and email. In place of the computer's system configuration,
     gitneighbr supplies its own: empty, except on Windows, where it keeps Git for
     Windows' line-ending default (`core.autocrlf`) and the Windows certificate store
     (`schannel`). Without these, a folder checked out by another Windows Git tool
     would show text files as changed, and HTTPS would fail on networks that add their
     own certificates;
   - no inherited `GIT_*`/`SSH_*` variables;
   - hooks, templates, fsmonitor, pager, editor, signing, credential helpers and askpass
     switched off;
   - `--no-ext-diff --no-textconv`;
   - HTTPS as the only transport.

   It refuses, and explains, any repository that is outside a reviewed allow-list of
   local settings, uses a `filter=` attribute (LFS, encryption), uses includes, alternates,
   submodules, `core.worktree`, or a `.git` file or link, or sits inside another
   repository. The macOS build isn't relocatable on its own, so the adapter names its
   helper folder (`--git-exec-path`).

3. **Local snapshots by default; sending is per session.** Opening Version history offers
   local status, diffs, selected-file snapshots and explicit setup. Send and Update appear
   only when the user ticks "also allow sending" for that session. Nothing fetches or
   connects until the user asks.

   **Send** lists every outgoing commit and every changed path with sizes, and blocks
   uninspectable history. It needs a confirmation, plus a second one for review-labelled
   files, and pushes exactly the previewed commit, never forced.

   **Update** fast-forwards to exactly the previewed commit. It is refused for incoming
   changes to protected paths or to files with local work.

   Access tokens are held in memory and reach Git only through per-command environment
   variables. Tags, releases, clone, restore, trash and pull requests are off.

4. **Workspace coordination is a core contract, not Git logic.** A descriptor may set
   `web_app.guard_mutations`. The proxy then refuses that app's state-changing requests
   (423) while a job runs or another tool has unsaved or busy work. Otherwise it holds a
   workspace lease for the request's length, so jobs and project-file saves can't start
   meanwhile. A child launched with a `workspace_root` is stopped when another workspace
   opens.

5. **The policy for an NCA study folder** (`history.policy()`):
   - **Groups**, in match order (gitneighbr uses the first that matches): run and
     publication records; generated files (`OUTPUTS/`, builds, the report's and deck's
     copies of published files); editable sources (recipes, `report/`, `deck/`); the
     app's working files (the rest of `.opncafyr/`); source data; other.
   - **Protected:** no Git action may change these in the folder: `.opncafyr/runs/`,
     `.opncafyr/publications/`, `report/builds/`, `deck/builds/`, `workspace.json`, jobs
     and backups. Committing a copy of them is allowed. Updating can never replace a
     completed run or a build.
   - **Review label "May identify subjects"** (proposed): source data, cleaned data and
     checks, runs, `OUTPUTS/`, report and deck builds. Sending them needs the extra
     confirmation. Nothing is called "safe".
   - **Send-blocked:** none by default.
   - **Ignore suggestions** (owner, 2026-11-08): noise, and files not needed to reproduce
     results. They are offered only during explicit setup, each with a reason, and never
     written over an existing `.gitignore`; nothing is hidden silently. The list:
     - job scratch (`.opncafyr/jobs/`);
     - caches the app rebuilds: `.opncafyr/cleaned/`, `.opncafyr/checks/` and
       `.opncafyr/publish-checks/` (each run keeps its own copy of its input);
     - pre-upgrade copies (`.opncafyr/backups/`);
     - render caches and logs (`report/.quarto/`, `report/.rpfy-logs/`);
     - the deck editor's own previews (`deck/build/`, not `deck/builds/`);
     - OS and editor files (`.DS_Store`, `Thumbs.db`, Office `~$*` lock files, `.Rhistory`).

     Whether to snapshot source data, runs, publications, builds or `OUTPUTS/` stays the
     user's choice, file by file. Manifests are never ignored, because they carry
     provenance.

## Consequences

- A Git snapshot is an optional history of files. It is not a run, a publication approval,
  a build or scientific verification, and the guides and page say so.
- **Enclosing repositories are unsupported.** If a study folder sits inside another
  repository, nothing can be changed; supporting that would need a further decision.
- **SSH, Git LFS and credential managers are not supported.** The pinned bundle contains
  LFS and Git Credential Manager, but managed mode never runs them.
- **Corporate proxies:** with sending on, the usual `HTTPS_PROXY` variables reach Git, but
  there is no proxy configuration UI.
- **The pinned Windows Git is unverified:** gitneighbr's Windows CI runs managed mode
  (Git for Windows), but the pinned bundle's layout (`cmd/git.exe`,
  `ucrt64/libexec/git-core`) hasn't been run on Windows yet.
- **Release pinning:** gitneighbr's release workflow re-tags and replaces the tarball when
  a later merge to its main leaves `VERSION` unchanged. Pin only a release whose version
  was bumped, and expect a checksum mismatch, not a silent change, otherwise.
