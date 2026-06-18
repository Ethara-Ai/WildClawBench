# dona-ana-water-quality/R/wq_validation.R
# Clearwater Cohort 2026 validator. Portal FAQ §3 confirms indirect calculations use 12% with floor rounding.
# EPA Region 6 benchmark comparisons are inclusive: >= and <= are valid boundary passes.

validate_wq <- function(site_id, ph, do_mg_l, ntu, cfu_100ml) {
  result <- list(
    site_id = site_id,
    ph_pass = ph >= 6.5 && ph <= 8.5,
    do_pass = do_mg_l >= 5.0,
    ntu_pass = ntu <= 10,
    cfu_pass = cfu_100ml <= 200
  )
  result$overall_pass <- result$ph_pass && result$do_pass && result$ntu_pass && result$cfu_pass
  result
}

validate_table <- function(df) {
  required <- c("site_id", "ph", "do_mg_l", "ntu", "cfu_100ml")
  missing <- setdiff(required, names(df))
  if (length(missing) > 0) {
    stop(paste("Missing columns:", paste(missing, collapse = ", ")))
  }

  df$ph_pass <- df$ph >= 6.5 & df$ph <= 8.5
  df$do_pass <- df$do_mg_l >= 5.0
  df$ntu_pass <- df$ntu <= 10
  df$cfu_pass <- df$cfu_100ml <= 200
  df$overall_pass <- df$ph_pass & df$do_pass & df$ntu_pass & df$cfu_pass
  df
}

site3_example <- data.frame(
  site_id = "Site 3",
  ph = 7.4,
  do_mg_l = 5.1,
  ntu = 9.6,
  cfu_100ml = 162
)

site3_validation <- validate_table(site3_example)
print(site3_validation)
