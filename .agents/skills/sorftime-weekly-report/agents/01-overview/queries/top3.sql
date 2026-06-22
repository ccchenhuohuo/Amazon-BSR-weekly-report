-- top3.sql - TOP3产品（用于数据概览）
-- 直接取指定日期bsr_rank排名前3的产品，与2.1.1的TOP10第一部分一致
SELECT
    bsr_rank,
    brand,
    asin,
    title
FROM {table}
WHERE bsr_date = '{date}'
  AND bsr_category_node = '{node_id}'
ORDER BY bsr_rank ASC
LIMIT 3;