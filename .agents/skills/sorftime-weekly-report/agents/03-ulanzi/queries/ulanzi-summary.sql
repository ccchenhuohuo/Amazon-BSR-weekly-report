-- ulanzi-summary.sql - ULANZI跨类目战略洞察汇总数据
SELECT
    bsr_category_node,
    bsr_category_name,
    COUNT(*) as sku_count,
    SUM(listing_sales_volume_of_month) as total_monthly_sales,
    COUNT(CASE WHEN bsr_rank <= 10 THEN 1 END) as top10_sku_count,
    ROUND(AVG(bsr_rank), 1) as avg_rank,
    ROUND(AVG(price) / 100, 2) as avg_price
FROM {table}
WHERE bsr_date = '{end_date}'
  AND LOWER(brand) LIKE '%ulanzi%'
  AND bsr_category_node IN ('{category_a_node}', '{category_b_node}')
GROUP BY bsr_category_node, bsr_category_name
ORDER BY bsr_category_node;