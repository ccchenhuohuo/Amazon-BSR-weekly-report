# 目标表结构

```sql
CREATE TABLE <DORIS_DATABASE>.<DORIS_TABLE> (
    asin               VARCHAR(20)   NOT NULL,
    bsr_date           VARCHAR(20)   NOT NULL COMMENT 'BSR 快照日期',
    title              TEXT          NOT NULL,
    photo              TEXT          NOT NULL COMMENT '主图 URL 列表 JSON',
    ebc_photo          TEXT          NOT NULL COMMENT 'A+ 图片 JSON',
    store_name         VARCHAR(200)  NOT NULL,
    listing_sales_volume_of_daily  INT  NOT NULL,
    listing_sales_of_daily         INT  NOT NULL,
    listing_sales_volume_of_month  INT  NOT NULL,
    listing_sales_of_month         INT  NOT NULL,
    parent_asin        VARCHAR(20)   NOT NULL,
    price              INT           NOT NULL COMMENT '当地货币最小单位',
    list_price         INT           NOT NULL,
    product_type       VARCHAR(200)  NOT NULL,
    sales_price        INT           NOT NULL COMMENT '扣除 coupon 后',
    brand              VARCHAR(200)  NOT NULL,
    profit             INT           NOT NULL,
    profit_rate        DECIMAL(10,4) NOT NULL,
    online_date        VARCHAR(20)   NOT NULL,
    online_days        INT           NOT NULL,
    ratings_count      INT           NOT NULL,
    category           TEXT          NOT NULL COMMENT '大类信息 JSON',
    bsr_category_name  VARCHAR(200)  NOT NULL,
    bsr_category_node VARCHAR(50)   NOT NULL,
    bsr_rank           INT           NOT NULL COMMENT '细分类目排名',
    `rank`             INT           NOT NULL COMMENT '大类排名 BSR',
    ratings            DECIMAL(3,2)  NOT NULL COMMENT '评分星级',
    size               TEXT          NOT NULL COMMENT '尺寸数组 JSON',
    insert_time        DATETIME      NOT NULL
) ENGINE=OLAP
DUPLICATE KEY(asin, bsr_date)
DISTRIBUTED BY HASH(asin) BUCKETS 10;
```

# API 字段 → Doris 字段对照表

| Doris 字段 | API 字段 | 转换说明 |
|-----------|---------|---------|
| `asin` | `Asin` | 直接映射 |
| `bsr_date` | QueryDate 参数 | 外部传入 |
| `title` | `Title` | 直接映射 |
| `photo` | `Photo` | array → JSON string |
| `ebc_photo` | `EBCPhoto` | array → JSON string |
| `store_name` | `StoreName` | 直接映射 |
| `listing_sales_volume_of_daily` | `ListingSalesVolumeOfDaily` | 直接映射 |
| `listing_sales_of_daily` | `ListingSalesOfDaily` | 直接映射 |
| `listing_sales_volume_of_month` | `ListingSalesVolumeOfMonth` | 直接映射 |
| `listing_sales_of_month` | `ListingSalesOfMonth` | 直接映射 |
| `parent_asin` | `ParentAsin` | 直接映射 |
| `price` | `Price` | 直接映射 |
| `list_price` | `ListPrice` | 直接映射 |
| `product_type` | `ProductType` | 直接映射 |
| `sales_price` | `SalesPrice` | 直接映射 |
| `brand` | `Brand` | 直接映射 |
| `profit` | `Profit` | 直接映射 |
| `profit_rate` | `ProfitRate` | 直接映射 |
| `online_date` | `OnlineDate` | 直接映射 |
| `online_days` | `OnlineDays` | 直接映射 |
| `ratings_count` | `RatingsCount` | 直接映射 |
| `category` | `Category` | array → JSON string |
| `bsr_category_name` | `BsrCategory[0][0]` | 取细分类目名称 |
| `bsr_category_node` | `BsrCategory[0][1]` | 取细分类目 NodeId |
| `bsr_rank` | `BsrCategory[0][2]` | 细分类目排名（核心过滤字段） |
| `rank` | `Rank` | 大类 BSR 排名 |
| `ratings` | `Ratings` | Decimal → float |
| `size` | `Size` | array → JSON string |
| `insert_time` | - | 当前时间 `datetime.now()` |
