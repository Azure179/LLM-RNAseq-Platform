# Restore only from the checksummed sources staged by r_sources.py.
# The only preinstalled R packages accepted are bundled in the verified R source.
args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 2L)
lock <- read.delim(args[1], skip = 1L, colClasses = "character")
pin <- strsplit(sub("^# R=", "", readLines(args[1], n = 1)), " Bioconductor=")[[1]]
stopifnot(as.character(getRversion()) == pin[1], !anyDuplicated(lock$package))
bundled <- lock$source == "R"
verify <- function(rows) {
    installed <- installed.packages(noCache = TRUE)
    wrong <- rows$package[!rows$package %in% rownames(installed) |
        unname(installed[match(rows$package, rownames(installed)), "Version"]) != rows$version]
    if (length(wrong)) stop("Missing or mismatched packages: ", paste(wrong, collapse = ", "))
}
verify(lock[bundled, ])
preinstalled <- setdiff(rownames(installed.packages()), lock$package[bundled])
if (length(preinstalled)) stop("Restore requires a clean R library: ", toString(preinstalled))
repo <- normalizePath(args[2], mustWork = TRUE)
contrib <- file.path(repo, "src", "contrib")
packages <- lock[!bundled, ]
archives <- paste0(packages$package, "_", packages$version, ".tar.gz")
stopifnot(setequal(list.files(contrib), archives))
tools::write_PACKAGES(contrib, type = "source")
# install.packages orders Depends/Imports/LinkingTo and installs in parallel.
install.packages(packages$package, repos = paste0("file://", repo), type = "source",
                 dependencies = NA, Ncpus = 4L)
verify(lock)
stopifnot(as.character(BiocManager::version()) == pin[2])
for (pkg in c("DESeq2", "clusterProfiler", "ggplot2")) {
    stopifnot(requireNamespace(pkg, quietly = TRUE))
}
