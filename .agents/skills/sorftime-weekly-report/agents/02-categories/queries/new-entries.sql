-- new-entries.sql - 新上榜产品：上周不在TOP100，本周在
SELECT
    t.bsr_rank as this_rank,
    t.brand,
    t.title,
    t.asin,
    ROUND(t.price / 100, 2) as price,
    COALESCE(t.ratings, 0) as ratings,
    t.listing_sales_volume_of_month as monthly_sales,
    COALESCE(t.online_days, 0) as online_days,
    CASE WHEN t.online_days <= 180 THEN '是' ELSE '否' END as is_new_product,
    COALESCE(t.photo, '') as photo
FROM {table} t
WHERE t.bsr_date = '{end_date}'
  AND t.bsr_category_node = '{node_id}'
  AND t.asin NOT IN (
    SELECT asin
    FROM {table}
    WHERE bsr_date = '{start_date}' AND bsr_category_node = '{node_id}'
  )
ORDER BY t.bsr_rank ASC;