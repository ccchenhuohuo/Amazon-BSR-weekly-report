-- falling.sql - 强势下降产品：按变化幅度从大到小排序，最多3个
WITH
this_week AS (
    SELECT *
    FROM {table}
    WHERE bsr_date = '{end_date}' AND bsr_category_node = '{node_id}'
),
last_week AS (
    SELECT asin, bsr_rank as last_rank
    FROM {table}
    WHERE bsr_date = '{start_date}' AND bsr_category_node = '{node_id}'
)
SELECT
    CASE WHEN l.last_rank IS NOT NULL THEN l.last_rank - t.bsr_rank ELSE NULL END as rank_change,
    t.brand,
    t.title,
    t.asin,
    ROUND(t.price / 100, 2) as price,
    COALESCE(t.ratings, 0) as ratings,
    t.listing_sales_volume_of_month as monthly_sales,
    COALESCE(t.online_days, 0) as online_days,
    t.bsr_rank as this_rank,
    l.last_rank,
    COALESCE(t.photo, '') as photo
FROM this_week t
INNER JOIN last_week l ON t.asin = l.asin
WHERE l.last_rank IS NOT NULL
  AND l.last_rank - t.bsr_rank <= -10
ORDER BY l.last_rank - t.bsr_rank ASC
LIMIT 3;