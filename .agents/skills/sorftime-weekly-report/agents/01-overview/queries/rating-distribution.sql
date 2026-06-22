-- rating-distribution.sql - 评分区间分布统计
SELECT
    CASE
        WHEN ratings >= 4.6 THEN '4.6+'
        WHEN ratings >= 4.3 THEN '4.3-4.6'
        ELSE '<4.3'
    END as rating_range,
    COUNT(*) as product_count,
    ROUND(COUNT(*) * 100.0 / NULLIF(SUM(COUNT(*)) OVER(), 0), 1) as percentage,
    ROUND(AVG(bsr_rank), 0) as avg_rank,
    SUM(listing_sales_volume_of_month) as total_monthly_sales
FROM {table}
WHERE bsr_date = '{end_date}' AND bsr_category_node = '{node_id}'
GROUP BY rating_range
ORDER BY
    CASE rating_range
        WHEN '4.6+' THEN 1
        WHEN '4.3-4.6' THEN 2
        WHEN '<4.3' THEN 3
    END;