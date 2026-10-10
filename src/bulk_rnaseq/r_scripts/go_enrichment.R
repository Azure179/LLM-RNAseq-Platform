# GO entry point; analysis and plots share the audited ORA implementation.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) %% 2L != 0L) stop("Expected named argument/value pairs")
opt <- setNames(args[seq(2L, length(args), 2L)], args[seq(1L, length(args), 2L)])
script_arg <- grep("^--file=", commandArgs(), value = TRUE)
script_dir <- dirname(normalizePath(sub("^--file=", "", script_arg[1])))
source(file.path(script_dir, "enrichment_common.R"))
run_ora(opt, "go", "go_id", "go_name", "ontology")
