#!/usr/bin/env Rscript

suppressPackageStartupMessages(library(DESeq2))

parse_arguments <- function(arguments) {
  if (length(arguments) %% 2 != 0) {
    stop("Arguments must be provided as --name value pairs.")
  }

  keys <- sub("^--", "", arguments[seq(1, length(arguments), by = 2)])
  values <- arguments[seq(2, length(arguments), by = 2)]
  stats::setNames(as.list(values), keys)
}

require_argument <- function(arguments, name) {
  value <- arguments[[name]]
  if (is.null(value) || !nzchar(value)) {
    stop(sprintf("Missing required argument: --%s", name))
  }
  value
}

run_analysis <- function() {
  arguments <- parse_arguments(commandArgs(trailingOnly = TRUE))
  counts_path <- require_argument(arguments, "counts")
  metadata_path <- require_argument(arguments, "metadata")
  treatment <- require_argument(arguments, "treatment")
  control <- require_argument(arguments, "control")
  output_path <- require_argument(arguments, "output")
  filtered_path <- require_argument(arguments, "filtered-output")
  alpha <- as.numeric(require_argument(arguments, "alpha"))
  lfc_threshold <- as.numeric(require_argument(arguments, "lfc-threshold"))

  if (!is.finite(lfc_threshold) || lfc_threshold < 0) {
    stop("lfc-threshold must be finite and non-negative.")
  }

  if (is.na(alpha) || alpha <= 0 || alpha >= 1) {
    stop("Alpha must be greater than 0 and less than 1.")
  }
  if (identical(treatment, control)) {
    stop("Treatment and control must be different.")
  }

  count_frame <- utils::read.csv(
    counts_path,
    check.names = FALSE,
    stringsAsFactors = FALSE,
    colClasses = "character",
    fileEncoding = "UTF-8-BOM"
  )
  metadata <- utils::read.csv(
    metadata_path,
    check.names = FALSE,
    stringsAsFactors = FALSE,
    colClasses = "character",
    fileEncoding = "UTF-8-BOM"
  )

  if (ncol(count_frame) < 3 || names(count_frame)[[1]] != "gene_id") {
    stop("Count matrix must have gene_id as its first column and sample columns after it.")
  }
  if (!all(c("sample_id", "condition") %in% names(metadata))) {
    stop("Metadata must contain sample_id and condition columns.")
  }
  if (anyNA(count_frame$gene_id) || any(!nzchar(count_frame$gene_id))) {
    stop("gene_id values must be non-empty.")
  }
  if (anyDuplicated(count_frame$gene_id)) {
    stop("gene_id values must be unique.")
  }
  if (anyNA(metadata$sample_id) || any(!nzchar(metadata$sample_id))) {
    stop("sample_id values must be non-empty.")
  }
  if (anyDuplicated(metadata$sample_id)) {
    stop("sample_id values in metadata must be unique.")
  }

  count_columns <- as.data.frame(lapply(count_frame[-1], as.numeric), check.names = FALSE)
  if (!all(vapply(count_columns, is.numeric, logical(1)))) {
    stop("All count values must be numeric.")
  }
  count_matrix <- as.matrix(count_columns)
  if (anyNA(count_matrix) || any(!is.finite(count_matrix))) {
    stop("Count matrix cannot contain missing or infinite values.")
  }
  if (any(count_matrix < 0) || any(count_matrix != floor(count_matrix))) {
    stop("DESeq2 requires non-negative integer raw counts.")
  }
  if (any(count_matrix > .Machine$integer.max)) {
    stop("Count exceeds the R integer range.")
  }
  storage.mode(count_matrix) <- "integer"
  rownames(count_matrix) <- count_frame$gene_id

  sample_ids <- colnames(count_matrix)
  if (!setequal(sample_ids, metadata$sample_id)) {
    stop("Count matrix and metadata sample IDs do not match.")
  }
  metadata <- metadata[match(sample_ids, metadata$sample_id), , drop = FALSE]
  rownames(metadata) <- metadata$sample_id

  observed_conditions <- unique(metadata$condition)
  if (!setequal(observed_conditions, c(control, treatment))) {
    stop("MVP metadata must contain exactly the requested control and treatment levels.")
  }
  if (sum(metadata$condition == control) < 2 || sum(metadata$condition == treatment) < 2) {
    stop("MVP requires at least two biological replicates per condition.")
  }
  metadata$condition <- factor(metadata$condition, levels = c(control, treatment))

  keep <- rowSums(count_matrix) > 0
  if (!any(keep)) {
    stop("No non-zero genes are available for analysis.")
  }
  count_matrix <- count_matrix[keep, , drop = FALSE]

  dds <- DESeqDataSetFromMatrix(
    countData = count_matrix,
    colData = metadata,
    design = ~condition
  )
  dds <- DESeq(dds, quiet = TRUE)
  result <- results(
    dds,
    contrast = c("condition", treatment, control),
    alpha = alpha
  )

  result_frame <- as.data.frame(result)
  result_frame$gene_id <- rownames(result_frame)
  result_frame <- result_frame[
    , c("gene_id", "baseMean", "log2FoldChange", "lfcSE", "stat", "pvalue", "padj")
  ]
  result_frame <- result_frame[
    order(result_frame$padj, result_frame$pvalue, na.last = TRUE),
    ,
    drop = FALSE
  ]

  utils::write.csv(result_frame, output_path, row.names = FALSE, na = "")
  selected <- !is.na(result_frame$padj) & !is.na(result_frame$log2FoldChange) &
    result_frame$padj < alpha & abs(result_frame$log2FoldChange) >= lfc_threshold
  utils::write.csv(result_frame[selected, , drop = FALSE], filtered_path,
                   row.names = FALSE, na = "")

  cat(sprintf("R_VERSION=%s\n", R.version.string))
  cat(sprintf("DESEQ2_VERSION=%s\n", as.character(utils::packageVersion("DESeq2"))))
  cat(sprintf("GENES_ANALYZED=%d\n", nrow(result_frame)))
}

tryCatch(
  run_analysis(),
  error = function(error) {
    message(sprintf("DEG_ERROR: %s", conditionMessage(error)))
    quit(status = 1)
  }
)
