# Sorftime API 文件夹结构说明

## 目录结构

```
scripts/sorftime_api/
├── __init__.py                    # 模块初始化
│
├── category/                      # 一、类目市场（Category Market）
│   ├── CategoryTree/              # CategoryTree - 类目树结构
│   ├── CategoryRequest/           # CategoryRequest - 类目 Best Sellers
│   │   ├── fetch_bsr.py          # 数据获取
│   │   ├── transform_bsr.py      # 数据转换
│   │   └── fill_missing.py       # 缺失日期补全
│   ├── CategoryProducts/          # CategoryProducts - 类目全部热销产品
│   └── CategoryTrend/            # CategoryTrend - 查询市场历史趋势
│
├── product/                       # 二、产品（Product）
│   ├── ProductRequest/            # ProductRequest - 产品详情（含趋势）
│   │   ├── fetch_product.py
│   │   ├── prepare_stream_load.py
│   │   └── process_product_data.py
│   ├── ProductQuery/              # ProductQuery - 产品搜索
│   ├── AsinSalesVolume/          # AsinSalesVolume - 官方公布子体销量
│   ├── ProductVariationHistory/  # ProductVariationHistory - 子体变化历史数据
│   ├── ProductRealtimeRequest/    # ProductRealtimeRequest - 产品实时数据查询
│   ├── ProductReviewsCollection/ # ProductReviewsCollection - 实时采集产品评论
│   ├── ProductReviewsQuery/      # ProductReviewsQuery - 产品评论
│   └── SimilarProductRealtimeRequest/ # SimilarProductRealtimeRequest - 图搜相似产品
│
├── keyword/                       # 三、关键词（Keywords）
│   ├── KeywordQuery/              # KeywordQuery - 关键词查询
│   ├── KeywordSearchResults/      # KeywordSearchResults - 关键词搜索结果产品
│   ├── KeywordRequest/            # KeywordRequest - 关键词详情
│   ├── KeywordSearchResultTrend/  # KeywordSearchResultTrend - 关键词搜索结果产品趋势
│   ├── CategoryRequestKeyword/    # CategoryRequestKeyword - 类目反查关键词
│   ├── ASINRequestKeyword/        # ASINRequestKeyword - ASIN反查关键词
│   ├── KeywordProductRanking/     # KeywordProductRanking - 关键词历史搜索结果产品
│   ├── ASINKeywordRanking/        # ASINKeywordRanking - ASIN在关键词下排名趋势
│   └── KeywordExtends/            # KeywordExtends - 查延伸关键词
│
├── monitoring/                    # 四、数据监控（Data Monitoring）
│   ├── KeywordBatchSubscription/  # KeywordBatchSubscription - 关键词监控注册
│   ├── BestSellerListSubscription/ # BestSellerListSubscription - 榜单监控任务注册
│   ├── ProductSellerSubscription/ # ProductSellerSubscription - 跟卖&库存监控注册
│   └── ASINSubscription/          # ASINSubscription - ASIN更新订阅
│
├── agent/                         # 五、Sorftime Agent（AI 解读）
│   ├── ProductAssistant/          # ProductAssistant - AI解读产品
│   ├── CategoryAssistant/         # CategoryAssistant - AI解读类目市场
│   ├── AIResultQuery/             # AIResultQuery - AI执行进度查询
│   └── AIResult/                  # AIResult - AI解读分析结果查询
│
└── others/                        # 六、其他（Others）
    ├── CoinQuery/                 # CoinQuery - 本月剩余积分查询
    ├── CoinStream/                # CoinStream - 积分使用明细查询
    └── RequestStreamMonth/        # RequestStreamMonth - 月度Request使用明细查询
```

## API 模块对应关系

| 模块 | 接口数量 | 状态 |
|-----|---------|------|
| category | 4 | ✅ 部分实现 |
| product | 12 | ✅ 部分实现 |
| keyword | 12 | ⏳ 预留文件夹 |
| monitoring | 15 | ⏳ 预留文件夹 |
| agent | 4 | ⏳ 预留文件夹 |
| others | 3 | ⏳ 预留文件夹 |

## 扩展指南

新增 Sorftime API 接口时，请在对应模块下创建**接口名称**的文件夹：

1. **类目市场新接口** → `sorftime_api/category/{接口名}/`
2. **产品新接口** → `sorftime_api/product/{接口名}/`
3. **关键词新接口** → `sorftime_api/keyword/{接口名}/`
4. **监控新接口** → `sorftime_api/monitoring/{接口名}/`
5. **AI 解读新接口** → `sorftime_api/agent/{接口名}/`
6. **其他新接口** → `sorftime_api/others/{接口名}/`

每个接口文件夹下存放该接口相关的脚本文件（fetch.py, transform.py, etc.）
