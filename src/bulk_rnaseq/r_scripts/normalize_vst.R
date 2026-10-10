#!/usr/bin/env Rscript

tryCatch({
  args <- commandArgs(trailingOnly = TRUE)
  if (!length(args) || length(args) %% 2L != 0L) stop("Expected --name value pairs.")
  options <- stats::setNames(as.list(args[seq(2, length(args), 2)]), sub("^--", "", args[seq(1, length(args), 2)]))
  if (!all(c("counts", "output", "vst-module") %in% names(options))) stop("Missing VST arguments.")
  source(options[["vst-module"]])
  expression <- vst_from_counts(read_raw_counts(options$counts))
  result <- data.frame(gene_id = rownames(expression), expression, check.names = FALSE, row.names = NULL)
  utils::write.csv(result, options$output, row.names = FALSE, fileEncoding = "UTF-8")
  cat(sprintf("R_VERSION=%s\n", R.version.string))
  cat(sprintf("DESEQ2_VERSION=%s\n", as.character(utils::packageVersion("DESeq2"))))
  cat(sprintf("N_GENES_VST=%d\n", nrow(expression)))
}, error = function(error) {
  message(sprintf("VST_ERROR: %s", conditionMessage(error)))
  quit(status = 1)
})
