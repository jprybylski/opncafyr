# 0012. External data through a managed datum

- Status: accepted (owner, 2026-11-15). The owner answered D1–D8 of #83 as listed below,
  and asked for phases 1–3 (everything except adopting upstream changes) in one change.
- Date: 2026-11-15
- Issues: #83; follows #56 and ADR 0008 (a managed tool), #70 and ADR 0011 (an on-request
  fetch with a session token); #41 (removable tools), #44 (offline), #42 and ADR 0009
  (container)

## Context

Studies depend on external data: a CSV on a data portal, a file on a network share, one
file from a Git repository. [datum](https://github.com/jprybylski/datum) (Go, MIT) declares
such data in `.data.yaml`, fetches it, and records fingerprints in `.data.lock.yaml`. It
has a CLI only, and at 1.6.0 several of its behaviours conflict with our invariants:

- its `command` handler runs shell commands taken from the YAML;
- targets are used as written, including absolute paths and `..`;
- on every `check` and `fetch` it runs `git` or `svn` from `PATH` to edit ignore rules
  when its cwd is inside a working copy;
- under the `update` policy, `check` re-fetches and rewrites targets by itself, and check
  writes the lockfile;
- directory sync is not atomic;
- `${NAME}` in any string is replaced from the process environment.

## Decision

The launcher runs datum in a **managed mode**: one adapter (`adapters/datum`) and one
workflow pack (`workflows/datafetch`), which only the pack imports.

1. **Policy before any process.** `.data.yaml` is checked against datum's own schema
   (`datum schema`, cached per pinned binary) and these rules. Each refusal names the
   dataset and field, never a value.

   | Rule | Why |
   |---|---|
   | `source.type` is `http`, `file` or `git`; `command` is refused | Workspace files never add a command |
   | `target` is relative, `/`-separated, has no `..` or hidden parts, lies under `source-data/` (D1), and still does after following links (`resolve_in_root`); no `${NAME}` | Confine writes; the import scan sees `source-data/` |
   | `http` and `git` URLs are `https://` (D4), have no user name or password, and name their host directly | As `core/gitfetch.py` (ADR 0011) |
   | `git@`, `ssh://`, `git://` are refused | No SSH keys or agent (ADR 0008) |
   | A `file` source outside the study needs a per-machine approval (D3) | A shared config must not copy something private into a study |
   | `ignore` is false | datum never touches `.gitignore` or `svn:ignore` |
   | `${NAME}` must be a valid name the launcher doesn't set itself, and gets a value only from the user, for one job | No inherited variable reaches a URL |
   | `fail`, `update`, `log` are report-only labels (D8) | Never rewrite source files silently |

2. **Snapshot, run, review, apply.** datum never runs on the study.
   - **The snapshot.** The checked config is written next to a copy of the lockfile, with
     `ignore: false`, relative `file` sources made absolute, and every target pointed into
     the snapshot (the lockfile stores no targets, so no absolute path reaches the study).
     The current target files are copied in, so datum sees the study's local state.
   - **The cwd.** It is outside every Git or SVN working copy: the job manager's
     `isolated_cwd`, and the adapter refuses to start if a folder above it has `.git` or
     `.svn`.
   - **Check** (`data_check` job) runs in that folder and records only the result, in
     `.opncafyr/datafetch/state.json`.
   - **Fetch** (`data_fetch` job) writes into the job's output staging. A complete review
     folder (`.opncafyr/datafetch/reviews/<id>`) appears only after a clean exit and a
     passing review. The review lists, per file, new / unchanged / will replace / will
     remove, with sizes and SHA-256s, and no data values.
   - **Apply** runs in process under the workspace lease, refusing while jobs run:
     1. verify the hashes, the config and the lock;
     2. write a journal;
     3. keep replaced bytes (D2) in `.opncafyr/datafetch/previous/<id>/<sha256>`;
     4. move files with atomic replaces;
     5. write the lockfile, then `state.json`.

     If Apply is interrupted, the journal makes the page offer **Finish** or **Roll back**.
   - **Delete, undelete, forget.** Delete moves the tracked files to `previous/` and runs
     `datum delete` on a snapshot pointed at an empty folder; undelete and forget edit a
     snapshot's lock. Each then writes the lockfile atomically.
3. **Environment.** argv only, from the pinned runtime. The environment is
   `core.proctree.child_env` plus `NO_COLOR`, an app-owned `XDG_CACHE_HOME`, the
   launcher's proxy and CA-bundle variables (`SSL_CERT_FILE`, `SSL_CERT_DIR`), and that
   job's credentials. There is no `SSH_*` and no `GIT_*` unless the user names one.
4. **Credentials (D5).** Values are typed on the page, sent with one check or fetch, held
   in that job's closure, and cleared in the job's `cleanup`, which also scrubs its logs.
   Messages are redacted before they are stored or shown: entered values (raw, URL-encoded,
   base64), every URL query and every `user:pass@`. They never appear in argv, job records,
   review documents or the study.
5. **Approvals (D3).** Kept in `<app root>/state/datafetch/approvals.json`, keyed by a hash
   of the study (folder and creation time), the dataset id and the exact path. A copied
   study, a renamed dataset or a changed path needs approval again.
6. **Editing (D6 b).** Structural: parse, change, write the YAML again, guarded by the
   file's SHA-256. The page warns before a save when the file has comments. The scaffold
   is written by the launcher, never by `datum init`, which would run VCS detection in the
   study.
7. **Placement (D7).** A top-level **External data** view from the datafetch pack, shown
   while a study is open. The only link to the analysis flow is generic: fetched files land
   in `source-data/`, which the dataset scan lists, and a `DatasetAnnotator` labels them.
   Import already records each source's SHA-256, so a run pins the fetched bytes, and
   completed runs keep their own copy of their input.
8. **Removable.** `[optional.datum]` is `default = true` and best effort, like Git
   (`--without-datum`). Without the binary, the page shows "unavailable" with the reason.
   A CI job deletes both packages from the checkout and runs the full suite.

### Small core changes (reviewed against ADR 0002)

`core.jobmanager.JobRequest` gains two tool-agnostic options:

- **`isolated_cwd`**: a fresh folder outside the workspace as the job's cwd, removed when
  the job ends;
- **`cleanup`**: called once after every outcome, before the terminal state is written.

Neither names a tool. `core/` still imports no adapter or workflow.

### Deviations from #83's text

- **Apply, purge and the lock edits are not jobs.** Jobs always run a command; these are
  file moves under the workspace lease, like project-file saves. The descriptor's jobs are
  `data_check` and `data_fetch`, which must also be capabilities (the registry requires
  it), next to `data_fetch_local` and `data_fetch_remote`.
- **Snapshot targets point into the job's output staging** instead of a separate staging
  cwd, so the job manager's own staging rename makes a review complete atomically.

## Consequences

- Each interim safeguard has a matching upstream change, U1–U11 in
  [upstream.md](../upstream.md#jprybylskidatum-public). When datum releases them, the
  safeguards can shrink to defence in depth (phase 4 of #83, tracked in #85).
- A redirect from https to http is not caught until U10.
- Fetching proves which bytes the study has and where they came from, not that the data
  is fit for analysis. The research-use wording is unchanged.
- Writing datum provenance into the run manifest is out of scope: it would need a
  `run-manifest` schema change.
