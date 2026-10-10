#!/usr/bin/env Rscript

# Fixed exploratory method: DESeq2 size factors + blind VST, then centered PCA.
# Group labels are annotations only and never enter the transformation or PCA.

run_pca <- function() {
  suppressPackageStartupMessages(library(DESeq2))
  args <- commandArgs(trailingOnly = TRUE)
  if (length(args) == 0L || length(args) %% 2L != 0L) {
    stop("Expected --name value argument pairs.")
  }
  options <- stats::setNames(as.list(args[seq(2, length(args), 2)]),
                             sub("^--", "", args[seq(1, length(args), 2)]))
  required <- c("counts", "metadata", "group-column", "output-dir", "vst-module")
  if (!all(required %in% names(options))) stop("Missing required PCA arguments.")

  source(options[["vst-module"]])
  count_matrix <- read_raw_counts(options$counts)
  samples <- colnames(count_matrix)
  if (length(samples) < 3L) stop("PCA requires at least three samples.")
  metadata <- utils::read.csv(options$metadata, check.names = FALSE,
      colClasses = "character", fileEncoding = "UTF-8-BOM")
  group_column <- options[["group-column"]]
  if (!all(c("sample_id", group_column) %in% names(metadata))) stop("Missing sample/group column.")
  if (anyDuplicated(metadata$sample_id)) stop("Metadata sample IDs must be unique.")
  if (!setequal(samples, metadata$sample_id)) stop("Count and metadata sample sets differ.")
  metadata <- metadata[match(samples, metadata$sample_id), , drop = FALSE]
  groups <- metadata[[group_column]]
  if (anyNA(groups) || any(!nzchar(trimws(groups)))) stop("Group annotations cannot be missing.")
  expression <- vst_from_counts(count_matrix)
  n_genes_vst <- nrow(expression)
  variances <- apply(expression, 1, stats::var)
  expression <- expression[is.finite(variances) & variances > 0, , drop = FALSE]
  if (nrow(expression) < 2L) stop("Fewer than two variable genes remain after VST.")

  # Rows are samples, columns are genes. Center genes; do not scale to unit variance.
  pca <- stats::prcomp(t(expression), center = TRUE, scale. = FALSE)
  if (length(pca$sdev) < 2L || any(!is.finite(pca$sdev)) || pca$sdev[1] <= 0 ||
      pca$sdev[2] <= pca$sdev[1] * sqrt(.Machine$double.eps)) {
    stop("Insufficient independent variation to produce two meaningful principal components.")
  }
  percent <- 100 * pca$sdev^2 / sum(pca$sdev^2)
  coordinates <- data.frame(sample = samples, PC1 = pca$x[, 1], PC2 = pca$x[, 2],
                             group = groups, check.names = FALSE, row.names = NULL)
  output <- options[["output-dir"]]
  utils::write.csv(coordinates, file.path(output, "pca_coordinates.csv"), row.names = FALSE,
                   fileEncoding = "UTF-8")
  # Numeric-only JSON: no extra R JSON dependency or manually escaped user text.
  cat(sprintf('{"PC1": %.15g, "PC2": %.15g}\n', percent[1], percent[2]),
      file = file.path(output, "pca_variance.json"))

  levels <- sort(unique(groups))
  colors <- stats::setNames(grDevices::hcl.colors(length(levels), palette = "Dark 3"), levels)
  grDevices::png(file.path(output, "pca_plot.png"), width = 1200, height = 900, res = 150)
  tryCatch({
    graphics::par(mar = c(5, 5, 3, 9))
    graphics::plot(coordinates$PC1, coordinates$PC2, pch = 21, cex = 1.5,
                   bg = colors[groups], col = "grey25",
                   xlab = sprintf("PC1 (%.1f%%)", percent[1]),
                   ylab = sprintf("PC2 (%.1f%%)", percent[2]),
                   main = "PCA of VST expression", bty = "l")
    plot_limits <- graphics::par("usr")
    graphics::legend(plot_limits[2] + 0.04 * diff(plot_limits[1:2]), plot_limits[4],
                     xjust = 0, yjust = 1, xpd = NA, bty = "n",
                     title = group_column, legend = levels, pch = 21,
                     pt.bg = colors, col = "grey25", pt.cex = 1.3)
  }, finally = grDevices::dev.off())

  cat(sprintf("R_VERSION=%s\n", R.version.string))
  cat(sprintf("DESEQ2_VERSION=%s\n", as.character(utils::packageVersion("DESeq2"))))
  cat(sprintf("N_GENES_VST=%d\n", n_genes_vst))
  cat(sprintf("N_GENES_PCA=%d\n", nrow(expression)))
}

tryCatch(run_pca(), error = function(error) {
  message(sprintf("PCA_ERROR: %s", conditionMessage(error)))
  quit(status = 1)
})
