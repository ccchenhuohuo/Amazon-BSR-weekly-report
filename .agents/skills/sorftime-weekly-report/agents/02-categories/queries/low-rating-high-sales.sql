-- low-rating-high-sales.sql - 低分高销产品：评分<4.3但月销表现突出
SELECT
    t.bsr_rank,
    t.brand,
    t.title,
    t.asin,
    ROUND(t.price / 100, 2) as price,
    COALESCE(t.ratings, 0) as ratings,
    t.listing_sales_volume_of_month as monthly_sales,
    COALESCE(t.online_days, 0) as online_days,
    l.bsr_rank as last_rank,
    CASE WHEN l.bsr_rank IS NOT NULL THEN l.bsr_rank - t.bsr_rank ELSE NULL END as rank_change,
    COALESCE(t.photo, '') as photo
FROM {table} t
LEFT JOIN (
    SELECT asin, bsr_rank
    FROM {table}
    WHERE bsr_date = '{start_date}' AND bsr_category_node = '{node_id}'
) l ON t.asin = l.asin
WHERE t.bsr_date = '{end_date}'
  AND t.bsr_category_node = '{node_id}'
  AND COALESCE(t.ratings, 0) < 4.3
ORDER BY t.listing_sales_volume_of_month DESC
LIMIT 10;