# Offline CI (#44): make one table and one figure, with reportifyr metadata, in a
# report project. Run with the project as the working directory, after the project's
# report/ and OUTPUTS/ folders and .report_init.json exist.
#
#   Rscript --vanilla make_report_outputs.R
#
# Synthetic numbers only. The quartifyr adapter (#32) and the publication curator (#31)
# will copy real run artifacts instead.

suppressPackageStartupMessages(library(reportifyr))

time <- c(0, 0.5, 1, 2, 4, 8, 12, 24)
conc <- c(0, 4.1, 6.8, 7.9, 6.2, 3.9, 2.3, 0.6)

tables <- file.path("OUTPUTS", "tables")
figures <- file.path("OUTPUTS", "figures")
dir.create(tables, recursive = TRUE, showWarnings = FALSE)
dir.create(figures, recursive = TRUE, showWarnings = FALSE)

summary_csv <- file.path(tables, "summary.csv")
utils::write.csv(
  data.frame(
    Statistic = c("Cmax", "Tmax", "Last time"),
    Value = c(max(conc), time[which.max(conc)], max(time))
  ),
  summary_csv,
  row.names = FALSE
)
write_object_metadata(summary_csv)

profile_png <- file.path(figures, "profile.png")
grDevices::png(profile_png, width = 960, height = 600, res = 150, type = "cairo")
graphics::plot(time, conc, type = "b", xlab = "Time (h)", ylab = "Concentration")
grDevices::dev.off()
write_object_metadata(profile_png, meta_type = "conc-time-trajectories")

cat("outputs written\n")
