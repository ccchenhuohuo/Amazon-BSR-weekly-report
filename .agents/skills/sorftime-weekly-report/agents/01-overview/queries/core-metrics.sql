-- core-metrics.sql - 类目核心指标对比
SELECT
    bsr_date,
    bsr_category_node,
    bsr_category_name,
    COUNT(DISTINCT brand) as unique_brands,
    SUM(listing_sales_volume_of_month) as total_monthly_sales,
    ROUND(CASE WHEN SUM(listing_sales_volume_of_month) = 0 THEN 0
           ELSE SUM(price * listing_sales_volume_of_month) * 1.0 / SUM(listing_sales_volume_of_month) / 100
      END, 2) as weighted_avg_price
FROM {table}
WHERE bsr_date IN ('{start_date}', '{end_date}')
  AND bsr_category_node = '{node_id}'
GROUP BY bsr_date, bsr_category_node, bsr_category_name
ORDER BY bsr_date;