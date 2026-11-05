# Fixed R programs

R code the launcher runs lives here as fixed, reviewed scripts (cleaning executor,
snapshot writer, opnca replay, report render wrapper). They are run as

    Rscript --no-init-file --no-save --no-restore <script>

with arguments passed as an argv array or environment variables, never by interpolating
workspace content into code. Workspace files supply data and options only.
