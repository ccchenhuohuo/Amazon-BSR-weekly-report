-- brand-efficiency.sql - 品牌效率排名
SELECT
    brand,
    COUNT(*) as sku_count,
    SUM(listing_sales_volume_of_month) as total_monthly_sales,
    ROUND(SUM(listing_sales_volume_of_month) * 1.0 / COUNT(*), 0) as sales_per_sku,
    ROUND(SUM(price * listing_sales_volume_of_month) * 1.0 / SUM(listing_sales_volume_of_month) / 100, 2) as avg_price
FROM {table}
WHERE bsr_date = '{end_date}' AND bsr_category_node = '{node_id}'
GROUP BY brand
ORDER BY sales_per_sku DESC
LIMIT 15;