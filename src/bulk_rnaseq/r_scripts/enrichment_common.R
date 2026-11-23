# Shared clusterProfiler ORA and a compatibility adapter for the public CSVs.
run_ora <- function(opt, prefix, id_column, name_column, ontology_column = NULL) {
    label <- toupper(prefix)
    read_input <- function(key) {
        read.csv(opt[[key]], colClasses = "character", check.names = FALSE,
                 na.strings = NULL, fileEncoding = "UTF-8")
    }
    query <- unique(read_input("--query")$gene_id)
    universe <- unique(read_input("--universe")$gene_id)
    mapping <- unique(read_input("--mapping"))
    p_cutoff <- as.numeric(opt[["--pvalue"]])
    q_cutoff <- as.numeric(opt[["--padj"]])
    output <- opt[["--output"]]
    stopifnot(length(query) > 0L, !anyDuplicated(query), !anyDuplicated(universe),
              all(query %in% universe), all(mapping$gene_id %in% universe))
    n <- length(query)
    N <- length(universe)
    term_groups <- split(mapping, mapping[[id_column]])
    # No species inference, annotation expansion, network lookup, or ID conversion.
    # Retain every nonempty gene set in the mapped universe, including sets >500.
    # Read the unfiltered result slot: qvalue estimates can be NA for small families.
    # Public significance remains pvalue <= cutoff AND BH padj <= cutoff.
    enrichment <- clusterProfiler::enricher(
        gene = query, universe = universe,
        TERM2GENE = unique(mapping[c(id_column, "gene_id")]),
        TERM2NAME = unique(mapping[c(id_column, name_column)]),
        pAdjustMethod = "BH", pvalueCutoff = 1, qvalueCutoff = 1,
        minGSSize = 1L, maxGSSize = N
    )
    # With a nonempty mapped query and no size exclusion, NULL is an engine error,
    # not a valid no-significant-terms result. Never invent a replacement table.
    if (is.null(enrichment)) stop("clusterProfiler returned NULL for mapped input")
    native <- enrichment@result
    stopifnot(all(c("ID", "Description", "GeneRatio", "BgRatio", "Count", "pvalue", "p.adjust") %in% names(native)),
              !anyDuplicated(native$ID), setequal(enrichment@universe, universe))
    results <- lapply(seq_len(nrow(native)), function(i) {
        row <- native[i, , drop = FALSE]
        association <- term_groups[[as.character(row$ID)]]
        if (is.null(association)) stop("clusterProfiler returned an unknown term")
        members <- unique(association$gene_id)
        hit <- sort(intersect(query, members))
        k <- length(hit)
        M <- length(members)
        # Membership formatting is deterministic, statistics come ONLY from enricher.
        # Do not split native geneID on '/': a user gene identifier may contain '/'.
        stopifnot(as.integer(row$Count) == k, row$GeneRatio == paste0(k, "/", n),
                  row$BgRatio == paste0(M, "/", N))
        data.frame(go_id = as.character(row$ID), description = as.character(row$Description),
                   ontology = if (is.null(ontology_column)) "" else association[[ontology_column]][1], overlap_count = k, query_size = n,
                   term_size = M, universe_size = N, gene_ratio = k / n,
                   bg_ratio = M / N, fold_enrichment = (k / n) / (M / N),
                   pvalue = row$pvalue, padj = row$p.adjust, genes = paste(hit, collapse = ";"),
                   stringsAsFactors = FALSE)
    })
    all_results <- do.call(rbind, results)
    rownames(all_results) <- NULL
    names(all_results)[1] <- id_column
    if (is.null(ontology_column)) all_results$ontology <- NULL
    all_results <- all_results[order(all_results$padj, all_results$pvalue, all_results[[id_column]]), ]
    enriched <- all_results[all_results$overlap_count > 0 &
                           all_results$pvalue <= p_cutoff & all_results$padj <= q_cutoff, ]
    write_results <- function(table, name) {
        # Round-trip precision keeps threshold-boundary decisions identical in Python.
        for (column in names(table)[vapply(table, is.numeric, logical(1))]) {
            table[[column]] <- sprintf("%.17g", table[[column]])
        }
        write.csv(table, file.path(output, name), row.names = FALSE, fileEncoding = "UTF-8")
    }
    write_results(all_results, paste0(prefix, "_all_results.csv"))
    write_results(enriched, paste0(prefix, "_enrichment.csv"))

    plot_results <- function(kind) {
        png(file.path(output, paste0(prefix, "_", kind, ".png")), width = 1400, height = 1000, res = 130,
            type = if (.Platform$OS.type == "unix" && capabilities("cairo")) "cairo" else getOption("bitmapType"))
        on.exit(dev.off())
        if (nrow(enriched) == 0L) {
            plot.new()
            title(paste(label, "over-representation analysis"))
            text(0.5, 0.58, paste("No", label, "terms pass the specified thresholds"), cex = 1.2)
            text(0.5, 0.46, sprintf("Query: %d | Universe: %d | Tested terms: %d", n, N, nrow(all_results)))
            return(invisible(NULL))
        }
        top <- enriched[rev(seq_len(min(20L, nrow(enriched)))), ]
        labels <- ifelse(nzchar(top$description) & top$description != top[[id_column]],
                         paste0(top[[id_column]], " ", substr(top$description, 1, 38)), top[[id_column]])
        par(mar = c(5, min(25, max(10, max(nchar(labels)) * 0.34)), 4, 3))
        if (kind == "dotplot") {
            strength <- -log10(pmax(top$padj, .Machine$double.xmin))
            palette <- colorRampPalette(c("#4575B4", "#D73027"))(100)
            limits <- range(strength)
            index <- if (diff(limits) == 0) rep(60L, length(strength)) else
                     1L + as.integer(99 * (strength - limits[1]) / diff(limits))
            sizes <- 0.8 + 2 * sqrt(top$overlap_count / max(top$overlap_count))
            plot(top$gene_ratio, seq_len(nrow(top)), pch = 21, bg = palette[index], col = "white",
                 cex = sizes, yaxt = "n", xlab = "Gene ratio (overlap / mapped query)", ylab = "",
                 xlim = c(0, max(top$gene_ratio) * 1.18), ylim = c(0.5, nrow(top) + 1.7),
                 main = paste(label, "enrichment: top 20 terms"))
            axis(2, at = seq_len(nrow(top)), labels = labels, las = 2, cex.axis = 0.75)
            legend_values <- if (diff(limits) == 0) limits[1] else limits
            legend_colors <- if (diff(limits) == 0) palette[60] else palette[c(1, 100)]
            legend("topright", legend = sprintf("-log10(BH): %.2f", legend_values),
                   pch = 21, pt.bg = legend_colors, bty = "n", cex = 0.7)
            mtext("Point area increases with overlap count; red indicates stronger enrichment", side = 1, line = 3.5, cex = 0.7)
        } else {
            barplot(top$overlap_count, names.arg = labels, horiz = TRUE, las = 1,
                    col = "#4575B4", border = NA, cex.names = 0.75,
                    xlab = "Overlapping query genes", main = paste(label, "enrichment: top 20 terms"))
        }
    }
    plot_results("dotplot")
    plot_results("barplot")
    cat("R_VERSION=", R.version.string, "\n", sep = "")
    cat("STATS_VERSION=", as.character(packageVersion("stats")), "\n", sep = "")
    cat("CLUSTERPROFILER_VERSION=", as.character(packageVersion("clusterProfiler")), "\n", sep = "")
    cat("ENRICHIT_VERSION=", as.character(packageVersion("enrichit")), "\n", sep = "")
    cat("N_TERMS_TESTED=", nrow(all_results), "\n", sep = "")
    cat("N_ENRICHED=", nrow(enriched), "\n", sep = "")
}
