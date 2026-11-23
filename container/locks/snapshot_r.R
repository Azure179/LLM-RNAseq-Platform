# Run only in the accepted reference R environment, never during restore.
options(warn = 2)
db <- installed.packages(fields = c("Repository", "biocViews"))
bioc <- as.character(BiocManager::version())
repository <- db[, "Repository"]
source <- ifelse(!is.na(db[, "Priority"]), "R",
    ifelse(repository %in% "CRAN", "CRAN",
    ifelse(repository %in% paste("Bioconductor", bioc), "bioc",
    ifelse(grepl("AnnotationData", db[, "biocViews"]), "data/annotation", NA))))
stopifnot(!anyNA(source))
lock <- data.frame(package = db[, "Package"], version = db[, "Version"], source = source)
cat("# R=", as.character(getRversion()), " Bioconductor=", bioc, "\n", sep = "")
write.table(lock[order(lock$package, method = "radix"), ], file = stdout(),
            sep = "\t", row.names = FALSE, quote = FALSE)
