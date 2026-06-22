-- top10.sql - 获取某类目某天的TOP10产品（含上周排名和排名变化）
WITH
this_week AS (
    SELECT
        bsr_rank,
        brand,
        title,
        asin,
        price,
        ratings,
        listing_sales_volume_of_month as monthly_sales,
        online_days,
        photo
    FROM {table}
    WHERE bsr_date = '{end_date}' AND bsr_category_node = '{node_id}'
),
last_week AS (
    SELECT asin, bsr_rank as last_rank
    FROM {table}
    WHERE bsr_date = '{start_date}' AND bsr_category_node = '{node_id}'
)
SELECT
    t.bsr_rank,
    t.brand,
    t.title,
    t.asin,
    ROUND(t.price / 100, 2) as price,
    COALESCE(t.ratings, 0) as ratings,
    t.monthly_sales,
    COALESCE(t.online_days, 0) as online_days,
    l.last_rank,
    CASE WHEN l.last_rank IS NOT NULL THEN l.last_rank - t.bsr_rank ELSE NULL END as rank_change,
    COALESCE(t.photo, '') as photo
FROM this_week t
LEFT JOIN last_week l ON t.asin = l.asin
ORDER BY t.bsr_rank ASC
LIMIT 10;