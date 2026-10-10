#!/usr/bin/env Rscript

# Rendering only: no DESeq2, model fitting, p-value adjustment or classification.
render_volcano <- function() {
  args <- commandArgs(trailingOnly = TRUE)
  if (!length(args) || length(args) %% 2L != 0L) stop("Expected --name value pairs.")
  options <- stats::setNames(as.list(args[seq(2, length(args), 2)]),
                             sub("^--", "", args[seq(1, length(args), 2)]))
  if (!all(c("data", "plot", "padj-threshold", "lfc-threshold") %in% names(options))) {
    stop("Missing rendering arguments.")
  }
  data <- utils::read.csv(options$data, colClasses = "character", check.names = FALSE,
                         na.strings = character(), fileEncoding = "UTF-8-BOM")
  required <- c("gene_id", "log2FoldChange", "category", "neg_log10_padj", "plotted", "label", "padj_zero_adjusted")
  if (!all(required %in% names(data))) stop("Missing prepared plotting columns.")
  if (!all(data$category %in% c("Up", "Down", "NS"))) stop("Invalid prepared category.")
  shown <- data[data$plotted == "true", , drop = FALSE]
  x <- as.numeric(shown$log2FoldChange)
  y <- as.numeric(shown$neg_log10_padj)
  if (any(!is.finite(x)) || any(!is.finite(y))) stop("Non-finite prepared coordinates.")
  alpha <- as.numeric(options[["padj-threshold"]])
  lfc <- as.numeric(options[["lfc-threshold"]])
  if (!is.finite(alpha) || alpha <= 0 || alpha >= 1 || !is.finite(lfc) || lfc < 0) {
    stop("Invalid rendering thresholds.")
  }
  # Avoid letting a threshold far outside the observed data squash all points.
  xmax <- max(1, abs(x)) * 1.5
  ymax <- max(1, y, -log10(alpha)) * 1.25
  value <- function(key, default) if (is.null(options[[key]])) default else options[[key]]
  colors <- c(Down = value("color-down", "#3974B5"), NS = value("color-ns", "#A8A8A8"), Up = value("color-up", "#D54E4E"))
  grDevices::png(options$plot, width = as.integer(value("width", 1200)), height = as.integer(value("height", 900)), res = 150)
  tryCatch({
    graphics::par(mar = c(6, 5, 4, 2))
    graphics::plot(NA_real_, NA_real_, type = "n", xlim = c(-xmax, xmax), ylim = c(0, ymax),
                   xlab = "log2FoldChange (source contrast)", ylab = "-log10(padj)",
                   main = value("title", "Volcano plot"), bty = "l")
    graphics::abline(h = -log10(alpha), col = "grey60", lty = 2)
    if (lfc <= xmax) graphics::abline(v = unique(c(-lfc, lfc)), col = "grey60", lty = 2)
    for (category in c("NS", "Down", "Up")) {
      selected <- shown$category == category
      graphics::points(x[selected], y[selected], pch = 16, cex = 1.0,
                       col = grDevices::adjustcolor(colors[category], alpha.f = 0.8))
    }
    labeled <- which(nzchar(shown$label))
    if (length(labeled)) {
      # Small deterministic vertical separation, not an automatic repel framework.
      for (side in c(-1, 1)) {
        indices <- labeled[if (side < 0) x[labeled] < 0 else x[labeled] >= 0]
        indices <- indices[order(y[indices])]
        previous <- -Inf
        for (i in indices) {
          label_y <- max(y[i], previous + 0.04 * ymax)
          graphics::segments(x[i], y[i], x[i] + side * 0.06 * xmax, label_y, col = "grey55")
          graphics::text(x[i] + side * 0.06 * xmax, label_y, labels = shown$label[i],
                         pos = if (side < 0) 2 else 4, cex = 0.65)
          previous <- label_y
        }
      }
    }
    counts <- vapply(names(colors), function(name) sum(data$category == name), integer(1))
    graphics::legend("top", legend = sprintf("%s (%d)", names(colors), counts),
                     col = colors, pch = 16, horiz = TRUE, bty = "n", cex = 0.8)
    if (!nrow(shown)) graphics::text(0, ymax * 0.45, "No plottable genes (missing padj/log2FC)", cex = 0.8)
    note <- sprintf("Shown: %d/%d | padj < %g | |log2FC| >= %g", nrow(shown), nrow(data), alpha, lfc)
    graphics::mtext(note, side = 1, line = 4, cex = 0.7)
    if (any(data$padj_zero_adjusted == "true")) {
      graphics::mtext("padj=0: finite display height only; original values preserved", side = 3, line = 0.3, cex = 0.65)
    }
  }, finally = grDevices::dev.off())
  cat(sprintf("R_VERSION=%s\n", R.version.string))
  cat(sprintf("N_PLOTTED=%d\n", nrow(shown)))
}

tryCatch(render_volcano(), error = function(error) {
  message(sprintf("VOLCANO_ERROR: %s", conditionMessage(error)))
  quit(status = 1)
})
