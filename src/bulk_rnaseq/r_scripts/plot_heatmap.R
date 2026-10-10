#!/usr/bin/env Rscript
# Render Python-prepared row Z-scores; never normalize, select DEGs or cluster here.
tryCatch({
  args <- commandArgs(trailingOnly = TRUE)
  if (!length(args) || length(args) %% 2L != 0L) stop("Expected --name value pairs.")
  options <- stats::setNames(as.list(args[seq(2, length(args), 2)]), sub("^--", "", args[seq(1, length(args), 2)]))
  if (!all(c("matrix", "annotations", "group-column", "plot") %in% names(options))) stop("Missing arguments.")
  frame <- utils::read.csv(options$matrix, colClasses = "character", check.names = FALSE, fileEncoding = "UTF-8-BOM")
  annotations <- utils::read.csv(options$annotations, colClasses = "character", check.names = FALSE, fileEncoding = "UTF-8-BOM")
  matrix <- as.matrix(as.data.frame(lapply(frame[-1], as.numeric), check.names = FALSE))
  genes <- frame$gene_id
  samples <- colnames(matrix)
  if (nrow(matrix) < 1L || ncol(matrix) < 2L || any(!is.finite(matrix))) stop("Invalid heatmap matrix.")
  if (!identical(samples, annotations$sample_id)) stop("Prepared sample annotations are misaligned.")
  groups <- annotations$group
  levels <- unique(groups)
  group_colors <- stats::setNames(grDevices::hcl.colors(length(levels), "Dark 3"), levels)
  palette <- grDevices::colorRampPalette(c("#2166AC", "#F7F7F7", "#B2182B"))(101)
  limit <- max(1, abs(matrix))
  color_index <- pmax(1L, pmin(101L, as.integer(round((matrix + limit) / (2 * limit) * 100)) + 1L))
  dim(color_index) <- dim(matrix)
  nr <- nrow(matrix)
  nc <- ncol(matrix)
  # Top-ranked gene is at the top; no hidden reordering or clipping of Z-scores.
  value <- function(key, default) if (is.null(options[[key]])) default else options[[key]]
  grDevices::png(options$plot, width = as.integer(value("width", 1500)), height = as.integer(value("height", max(1000, min(3000, 400 + nr * 28)))), res = 150)
  tryCatch({
    graphics::layout(matrix(c(1, 2, 3, 4), 2, 2, byrow = TRUE), heights = c(1, 6), widths = c(5, 1.8))
    graphics::par(mar = c(0.5, 9, 2.5, 0.5))
    graphics::plot.new()
    graphics::plot.window(xlim = c(0.5, nc + 0.5), ylim = c(0, 1), xaxs = "i", yaxs = "i")
    graphics::rect(seq_len(nc) - 0.5, 0.1, seq_len(nc) + 0.5, 0.65, col = group_colors[groups], border = "white")
    graphics::title(main = value("title", "Top DEG expression heatmap"), cex.main = 1.1)
    graphics::mtext(options[["group-column"]], side = 2, line = 1, las = 1, cex = 0.8)
    graphics::par(mar = c(0, 0, 2, 1))
    graphics::plot.new()
    graphics::legend("center", legend = levels, fill = group_colors, bty = "n", cex = 0.8)
    graphics::par(mar = c(8, 9, 0.5, 0.5))
    graphics::plot.new()
    graphics::plot.window(xlim = c(0.5, nc + 0.5), ylim = c(0.5, nr + 0.5), xaxs = "i", yaxs = "i")
    for (i in seq_len(nr)) {
      y <- nr - i + 1
      graphics::rect(seq_len(nc) - 0.5, y - 0.5, seq_len(nc) + 0.5, y + 0.5,
                     col = palette[color_index[i, ]], border = NA)
    }
    graphics::axis(1, at = seq_len(nc), labels = samples, las = 2, tick = FALSE, cex.axis = 0.75)
    if (value("show-gene-names", "true") == "true") graphics::axis(2, at = seq_len(nr), labels = rev(genes), las = 2, tick = FALSE,
                   cex.axis = if (nr > 80) 0.45 else 0.65)
    graphics::par(mar = c(8, 1, 1, 4))
    graphics::plot.new()
    graphics::plot.window(xlim = c(0, 1), ylim = c(-limit, limit), yaxs = "i")
    breaks <- seq(-limit, limit, length.out = 102)
    graphics::rect(0.05, breaks[-102], 0.4, breaks[-1], col = palette, border = NA)
    graphics::axis(4, at = c(-limit, 0, limit), labels = round(c(-limit, 0, limit), 2), las = 1)
    graphics::title(main = "Row Z-score", cex.main = 0.85)
  }, finally = grDevices::dev.off())
  cat(sprintf("R_VERSION=%s\nN_GENES=%d\nN_SAMPLES=%d\n", R.version.string, nr, nc))
}, error = function(error) {
  message(sprintf("HEATMAP_ERROR: %s", conditionMessage(error)))
  quit(status = 1)
})
