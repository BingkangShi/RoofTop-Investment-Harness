# 系统架构

## 数据面

`data_lake/` 是唯一运行数据根目录：

```text
data_lake/
  raw/market|documents|news/   不可变原始响应
  normalized/                  规范化 CSV/Parquet
  db/                          SQLite 传统主库
  graphs/                      Cypher/图快照
  documents/                   财报、公告、研报及元数据
  cache/                       可重建缓存
  models/                      本地 embedding 模型权重
  dead_letter/                 抓取或字段失败证据
  backups/ logs/ outbox/       备份、日志、邮件发件箱
  neo4j/                       可选 Neo4j Community 持久化目录
```

SQLite 负责资产、点时行情、持仓、证据、研报、信号、模型运行和数据血缘。事件图使用 `event_graphs / graph_nodes / graph_edges`；节点的 `relational_ref` 用 `trad://prices/…`、`trad://evidence/…` 等 URI 引用传统库记录。由此图节点不复制整份行情，只保存语义关系与可追溯引用。

Neo4j Community 是可选的查询/可视化副本，不是系统唯一真相。`app.graph_export` 生成幂等 Cypher，即使 Docker 未启动也不影响平台。

## 数据进入规则

1. 所有外部查询必须先保存原始响应，再规范化入库；禁止生产代码把一次性网络响应直接交给 UI 或模型后丢弃。
2. 校验必需字段、交易日、OHLC 关系、重复键和异常跳变。
3. 写入时附 `source_id`、`captured_at`、`raw_path` 和演示标志。
4. 抓取失败只记录 `ingestion_runs`/`dead_letter`，不破坏上次可信数据。
5. 跨源存在差异时并存原始证据，规范化主值由版本化质量规则决定。
6. 行情进入 `quote_snapshots / minute_bars / market_daily_bars / prices`；新闻、公告、财报、研报和社交内容进入 `source_documents`，历次正文进入不可覆盖的 `source_document_versions`；每次外部查询均在 `ingestion_runs` 留下成功或失败审计记录。
7. Agent 查询的用户输入、模型、上下文快照、回答或失败原因进入 `chat_messages / analysis_runs`。
8. 事件图通过 `persist_event_graph` 事务化写入 `event_graphs / graph_nodes / graph_edges`，每次完整版本进入 `graph_snapshots` 和 `data_lake/graphs/snapshots/`，随后刷新 `data_lake/graphs/event_graphs.cypher`；图节点用 `trad://source-documents/<id>` 等引用传统库，不复制整篇材料。

实时链路为 `腾讯公开行情 → 东方财富公开行情 → SQLite 最后可信快照`。后台线程负责网络抓取，Web API 只查询本地库，因此上游超时不会拖死页面。`quote_snapshots` 保存实时快照，`minute_bars` 保存一分钟数据及 `bar_kind`，`market_daily_bars` 保存可继续聚合为周/月/季/年的前复权日线；所有记录带来源、观察时间、抓取时间和原始文件路径。QMT/miniQMT 不属于启动依赖。

`app.persistence` 是文档和事件图的强制持久化入口。新增抓取器若没有调用该入口（或行情专用 `_write_*` 入口）就不算完成接入。前端查询、图表生成、搜索和 Agent 分析只消费本地数据库，绝不把临时网络结果当作系统事实。

## 研究面

- 客观模型：波动、回撤、动量、流动性、相关性、压力情景、止盈止损线。
- 图表指标：同一 OHLCV 窗口计算 MA5、KDJ(9,3,3)、MACD(12,26,9)；筹码分布是成交量在价格区间上的估算，并明确区别于券商逐笔持仓成本数据。
- 主观模型：事实/观点/假设分类、信源可靠度、事件因果边、情绪与基本面动态权重。
- AKQuant：只使用回测、因子表达式、walk-forward、参数检验和风险报告。
- Agent：信源审计、情绪、基本面、宏观、风险纪律和研报生成。每次运行保留输入快照、模型名、引用和输出。
- 前端：ValueCell 风格工作台，但不复制其交易连接能力。
- 搜索：严格匹配直接检索可审计文档；语义匹配由本地 Qwen3 Embedding 生成 512 维向量并保存在 SQLite `semantic_documents`，不调用付费向量数据库。
- 对话：`chat_sessions / chat_messages` 保存会话，后端读取外部密钥配置并调用分析 LLM；密钥不会返回前端。

## 安全边界

- 数据库无 `orders`、`broker_accounts` 等表。
- 桌面券商适配器只暴露行情读取；代码审查禁止导入下单方法。
- 绿线是“进入研究区”，不是自动买入；红线是“必须人工复核卖出”，不是自动卖出。
- 邮件先入本地 outbox，默认 dry-run，去重键避免重复轰炸。
- 所有结论必须标注“事实 / 观点 / 待验证假设”，并保留来源与反证条件。
