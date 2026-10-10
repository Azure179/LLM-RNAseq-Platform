# Fixed trusted renderer: no enrichment, p.adjust, package installation or dynamic code.
tryCatch({
  args <- commandArgs(trailingOnly = TRUE)
  if (!length(args) || length(args) %% 2L) stop("Expected argument/value pairs")
  opt <- setNames(as.list(args[seq(2, length(args), 2)]), sub("^--", "", args[seq(1, length(args), 2)]))
  data <- read.csv(opt$data, check.names = FALSE, colClasses = "character", fileEncoding = "UTF-8-BOM")
  for (kind in c("dotplot", "barplot")) {
    png(file.path(opt$output, paste0(kind, ".png")), width=as.integer(opt$width), height=as.integer(opt$height), res=130)
    tryCatch({
      if (!nrow(data)) {
        plot.new(); title(opt$title); text(.5, .5, "Enrichment succeeded; no significant terms in source result")
      } else {
        top <- data[rev(seq_len(nrow(data))), , drop=FALSE]
        labels <- paste(top[[opt[["id-column"]]]], substr(top$description, 1, 50))
        count <- as.numeric(top$overlap_count)
        if (opt[["show-count"]] == "true") labels <- paste0(labels, " (n=", count, ")")
        par(mar=c(5, min(24, max(10, max(nchar(labels)) * .25)), 4, 2))
        if (kind == "dotplot") {
          ratio <- as.numeric(top$gene_ratio)
          plot(ratio, seq_len(nrow(top)), pch=19, col="#3974B5", cex=.8 + 2*sqrt(count/max(1,count)),
               yaxt="n", xlab="Gene ratio", ylab="", main=opt$title, ylim=c(.5,nrow(top)+.5))
          axis(2, at=seq_len(nrow(top)), labels=labels, las=2, cex.axis=.7)
        } else {
          barplot(count, names.arg=labels, horiz=TRUE, las=1, col="#3974B5", border=NA,
                  cex.names=.7, xlab="Overlapping query genes", main=opt$title)
        }
      }
    }, finally=dev.off())
  }
  cat(sprintf("R_VERSION=%s\nN_DISPLAYED=%d\n", R.version.string, nrow(data)))
}, error=function(error) { message(conditionMessage(error)); quit(status=1) })
