-- ulanzi-internal-efficiency.sql - ULANZI内部效率分析：计算每个ULANZI产品的月销并评分
WITH
category_stats AS (
    SELECT
        AVG(listing_sales_volume_of_month) as avg_monthly_sales
    FROM {table}
    WHERE bsr_date = '{end_date}' AND bsr_category_node = '{node_id}'
)
SELECT
    t.asin,
    t.title,
    t.bsr_rank,
    COALESCE(t.online_days, 0) as online_days,
    t.listing_sales_volume_of_month as monthly_sales,
    COALESCE(t.photo, '') as photo,
    CASE
        WHEN t.listing_sales_volume_of_month >= category_stats.avg_monthly_sales * 2 THEN '⭐⭐⭐⭐⭐ 超高效'
        WHEN t.listing_sales_volume_of_month >= category_stats.avg_monthly_sales * 1.5 THEN '⭐⭐⭐⭐ 高效'
        WHEN t.listing_sales_volume_of_month >= category_stats.avg_monthly_sales * 1.0 THEN '⭐⭐⭐ 中效'
        WHEN t.listing_sales_volume_of_month >= category_stats.avg_monthly_sales * 0.5 THEN '⭐⭐ 低效'
        ELSE '⭐ 低效'
    END as efficiency_rating
FROM {table} t, category_stats
WHERE t.bsr_date = '{end_date}'
  AND t.bsr_category_node = '{node_id}'
  AND LOWER(t.brand) LIKE '%ulanzi%'
ORDER BY t.listing_sales_volume_of_month DESC;