# Bundled report template

The generic NCA report the launcher sets up (ADR 0007 §1). The owner maintains these files;
they ship with the app, so setting up a report needs no network.

| File | Source |
|---|---|
| `standard_footnotes.yaml` | quartifyr v0.3.3 (`7bc7eb1`), `examples/demo-report/report/standard_footnotes.yaml` |
| `config.yaml` | quartifyr v0.3.3 (`7bc7eb1`), `examples/demo-report/report/config.yaml`, with `use_artifact_size: false` (#62) |

`standard_footnotes.yaml` defines the abbreviations and footnote types (`meta_type`) that a
published artifact's sidecar may name. The publication curator checks its sidecars against
it (#30, #31), and report and deck projects get a copy.

Its `figure_footnotes` and `table_footnotes` texts are generic examples. Some state units,
such as `conc-time-trajectories`, which says hours and ng/mL. The curator therefore gives
every artifact `meta_type: "NA"`, so no canned text with assumed units is added to a
footnote.

When the quartifyr pin changes, compare these files with the new demo-report and update
them on purpose. They are never refreshed automatically.

`config.yaml` differs from the demo in one setting, `use_artifact_size: false`, and carries
comments for a person reading it in the app's editor (#64), written from reportifyr's own
comments and its source. The editor offers `config.yaml` and `standard_footnotes.yaml` to the
user (`editor.py`); `config_yaml.py` checks `config.yaml` against this file's setting names
and kinds, so a setting added here is a setting the check knows. With `true`,
reportifyr gives each figure the size of its pixels, and opnca-r's PNGs (2000 x 1200, no
resolution) come out about 27 in wide, far past a page. `false` uses `default_fig_width`
(6 in) and keeps the aspect ratio. Keep it when comparing with a new demo-report.
