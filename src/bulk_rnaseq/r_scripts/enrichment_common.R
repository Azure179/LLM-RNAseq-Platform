# Shared exact ORA and rendering. No test probabilities are computed in Python.
run_ora <- function(opt, prefix, id_column, name_column, ontology_column = NULL) {
    label <- toupper(prefix)
    read_input <- function(key) {
        read.csv(opt[[key]], colClasses = "character", check.names = FALSE,
                 na.strings = NULL, fileEncoding = "UTF-8")
    }
    query <- read_input("--query")$gene_id
    universe <- read_input("--universe")$gene_id
    mapping <- read_input("--mapping")
    p_cutoff <- as.numeric(opt[["--pvalue"]])
    q_cutoff <- as.numeric(opt[["--padj"]])
    output <- opt[["--output"]]
    stopifnot(length(query) > 0L, !anyDuplicated(query), !anyDuplicated(universe),
              all(query %in% universe), all(mapping$gene_id %in% universe))
    n <- length(query)
    N <- length(universe)
    term_groups <- split(mapping, mapping[[id_column]])
    results <- lapply(term_groups, function(association) {
        members <- unique(association$gene_id)
        hit <- sort(intersect(query, members))
        k <- length(hit)
        M <- length(members)
        # Rows: query / non-query; columns: in term / not in term.
        contingency <- matrix(c(k, n - k, M - k, N - M - n + k), nrow = 2L, byrow = TRUE)
        stopifnot(all(contingency >= 0L))
        p <- stats::fisher.test(contingency, alternative = "greater", conf.int = FALSE)$p.value
        data.frame(go_id = association[[id_column]][1], description = association[[name_column]][1],
                   ontology = if (is.null(ontology_column)) "" else association[[ontology_column]][1], overlap_count = k, query_size = n,
                   term_size = M, universe_size = N, gene_ratio = k / n,
                   bg_ratio = M / N, fold_enrichment = (k / n) / (M / N),
                   pvalue = p, padj = NA_real_, genes = paste(hit, collapse = ";"),
                   stringsAsFactors = FALSE)
    })
    all_results <- do.call(rbind, results)
    rownames(all_results) <- NULL
    names(all_results)[1] <- id_column
    if (is.null(ontology_column)) all_results$ontology <- NULL
    # Include EVERY term represented in effective universe, including zero-overlap terms.
    # One BH family per run; no size filter or annotation expansion.
    all_results$padj <- stats::p.adjust(all_results$pvalue, method = "BH")
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
        png(file.path(output, paste0(prefix, "_", kind, ".png")), width = 1400, height = 1000, res = 130)
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
    cat("N_TERMS_TESTED=", nrow(all_results), "\n", sep = "")
    cat("N_ENRICHED=", nrow(enriched), "\n", sep = "")
}
