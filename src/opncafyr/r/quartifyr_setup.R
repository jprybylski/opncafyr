# Lay out an empty folder as a quartifyr report project, offline (#32).
#
# Run by the quartifyr adapter as `Rscript --vanilla quartifyr_setup.R` with:
#   QUARTIFYR_SETUP_DIR   the empty project folder (a job staging folder)
#   QUARTIFYR_PYTHON_ENV  the tools Python environment (read by quartifyr itself)
#
# - initialize_report_project_offline() (quartifyr 0.3.3) writes report/, OUTPUTS/,
#   standard_footnotes.yaml, config.yaml and the .report_init.json marker. It never runs
#   pyro or uv and creates no .venv.
# - install_quarto_extensions() copies the bundled quarto-plus extension, and
#   install_quartifyr_extension() copies quartifyr's own. Nothing is downloaded and
#   `quarto add` is never run.
# The launcher then writes report.qmd, _quarto.yml and its bundled footnotes and config.

dir <- Sys.getenv("QUARTIFYR_SETUP_DIR")
if (!nzchar(dir) || !dir.exists(dir)) {
  stop("QUARTIFYR_SETUP_DIR must name an existing folder.", call. = FALSE)
}
options(quartifyr.offline = TRUE)
quartifyr::initialize_report_project_offline(dir)
quartifyr::install_quarto_extensions(dir)
quartifyr::install_quartifyr_extension(dir)
cat("OPNCAFYR-PROGRESS: Report project laid out.\n")
