# Migration regression ONLY. Never imported by a Skill or shipped in its package.
# Reproduce the pre-9.2 Fisher/BH family for comparison with actual native outputs.
args <- commandArgs(trailingOnly = TRUE)
query <- read.csv(args[1], colClasses = "character")$gene_id
universe <- read.csv(args[2], colClasses = "character")$gene_id
mapping <- read.csv(args[3], colClasses = "character")
groups <- split(mapping$gene_id, mapping[[2]])
n <- length(query)
N <- length(universe)
table <- do.call(rbind, lapply(names(groups), function(id) {
    members <- unique(groups[[id]])
    k <- length(intersect(query, members))
    M <- length(members)
    p <- fisher.test(matrix(c(k, n-k, M-k, N-M-n+k), 2, byrow=TRUE),
                     alternative="greater", conf.int=FALSE)$p.value
    data.frame(id=id, overlap=k, pvalue=p)
}))
table$padj <- p.adjust(table$pvalue, "BH")
table$positive_family_padj <- NA_real_
positive <- table$overlap > 0
table$positive_family_padj[positive] <- p.adjust(table$pvalue[positive], "BH")
write.csv(table, args[4], row.names=FALSE)
