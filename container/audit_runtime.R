# Credential-free inventory of the installed dependency closure, not source data.
args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 1L)
db <- installed.packages()
roots <- c("DESeq2", "clusterProfiler")
for (package in roots) stopifnot(requireNamespace(package, quietly = TRUE))
dependencies <- tools::package_dependencies(roots, db = db,
    which = c("Depends", "Imports", "LinkingTo"), recursive = TRUE)
packages <- sort(unique(c(roots, unlist(dependencies))))
packages <- intersect(packages, rownames(db))
inventory <- data.frame(package = packages, version = db[packages, "Version"], row.names = NULL)
payload <- list(R = R.version.string, platform = R.version$platform,
    Bioconductor = as.character(BiocManager::version()), dependencies = inventory,
    organism_packages_installed = grep("^org\\..*\\.db$", rownames(db), value = TRUE),
    annotation_mode = "user TERM2GENE / TERM2NAME; no online annotation lookup")
jsonlite::write_json(payload, args[1], pretty = TRUE, auto_unbox = TRUE)
