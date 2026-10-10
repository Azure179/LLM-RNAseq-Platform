# Shared by PCA and Heatmap: no DEG fitting/testing, fixed blind VST method.
read_raw_counts <- function(path) {
  frame <- utils::read.csv(path, colClasses = "character", check.names = FALSE, fileEncoding = "UTF-8-BOM")
  if (names(frame)[1] != "gene_id" || ncol(frame) < 3L) stop("Expected gene_id and at least two samples.")
  if (anyDuplicated(frame$gene_id) || anyDuplicated(names(frame)[-1])) stop("Gene/sample IDs must be unique.")
  counts <- as.matrix(as.data.frame(lapply(frame[-1], as.numeric), check.names = FALSE))
  if (any(!is.finite(counts)) || any(counts < 0) || any(counts != floor(counts)) ||
      any(counts > .Machine$integer.max)) stop("Expected non-negative integer raw counts in R integer range.")
  storage.mode(counts) <- "integer"
  rownames(counts) <- frame$gene_id
  if (any(colSums(counts) == 0)) stop("Every sample needs a non-zero library size.")
  counts <- counts[rowSums(counts) > 0, , drop = FALSE]
  if (nrow(counts) < 2L) stop("Too few non-zero genes for VST.")
  counts
}

vst_from_counts <- function(count_matrix) {
  suppressPackageStartupMessages(library(DESeq2))
  samples <- colnames(count_matrix)
  col_data <- data.frame(sample_id = samples, row.names = samples)
  dds <- DESeqDataSetFromMatrix(count_matrix, col_data, design = ~1)
  dds <- estimateSizeFactors(dds)
  vsd <- varianceStabilizingTransformation(dds, blind = TRUE, fitType = "parametric")
  expression <- assay(vsd)
  if (any(!is.finite(expression))) stop("VST produced non-finite values.")
  expression
}
