-- ulanzi-products.sql - ULANZI品牌产品查询（两周数据对比）
WITH
this_week AS (
    SELECT
        asin,
        title,
        bsr_rank as this_rank,
        price,
        ratings,
        listing_sales_volume_of_month as monthly_sales,
        online_days,
        photo
    FROM {table}
    WHERE bsr_date = '{end_date}' AND bsr_category_node = '{node_id}'
      AND LOWER(brand) LIKE '%ulanzi%'
),
last_week AS (
    SELECT
        asin,
        bsr_rank as last_rank
    FROM {table}
    WHERE bsr_date = '{start_date}' AND bsr_category_node = '{node_id}'
      AND LOWER(brand) LIKE '%ulanzi%'
)
SELECT
    t.asin,
    t.title,
    l.last_rank,
    t.this_rank,
    CASE WHEN l.last_rank IS NOT NULL THEN l.last_rank - t.this_rank ELSE NULL END as rank_change,
    ROUND(t.price / 100, 2) as price,
    COALESCE(t.ratings, 0) as ratings,
    t.monthly_sales,
    COALESCE(t.online_days, 0) as online_days,
    COALESCE(t.photo, '') as photo
FROM this_week t
LEFT JOIN last_week l ON t.asin = l.asin
ORDER BY t.this_rank ASC;