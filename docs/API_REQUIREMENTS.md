# 外部能力与配置边界

本项目不采购任何金融数据 API。模型 API 与金融数据 API 分开：用户已授权使用其模型配置，但行情、公告、财报、新闻和宏观数据只能采用免费/官方公开来源。

## 模型

| 角色 | 当前路由 | 用途 |
|---|---|---|
| 测试 LLM | `deepseek-v4-flash` | 文本抽取、结构化测试、低成本回归 |
| 文档/图表 MLLM | `qwen3.8-max` | OCR、表格、K 线图和财报页面理解 |
| 研究 Agent | OpenAI Responses API，模型由 `ROOFTOP_CODEX_MODEL` 指定 | 多步骤研究、工具调用、证据整合 |

密钥应保留在项目外部的 `<PlaceHolder>\configure_list.json`，并通过 `ROOFTOP_MODEL_CONFIG` 指定路径。健康接口只返回“是否已配置”，不返回密钥。

## 本地行情 API

以下接口只读取 SQLite，不会在 HTTP 请求中同步访问外部网站：

- `GET /api/market/quotes?symbols=600519,512400,000001.SH`：最新本地实时快照、来源和观察时间。
- `GET /api/market/minutes/600519?limit=300`：最近的分钟记录，最多 2000 条。
- `GET /api/assets/512400/chart?period=15m`：读取指定周期图表；周期为 `time,5d,1m,5m,15m,30m,60m,120m,1d,1w,1mo,1q,1y`。
- `GET /api/event-graphs`、`GET /api/event-graphs/{id}`：事件图选择、节点、边及引用。
- `GET /api/search?q=...&mode=exact|semantic&page=research|strategy|reports|graphs`：页面级严格/语义搜索。
- `POST /api/search/reindex`：增量更新本地向量索引。
- `GET /api/chat/models`：返回可安全选择且已经配置的 Agent LLM，不返回任何密钥。
- `GET /api/chat/sessions`、`GET /api/chat/messages?session_id=...`、`POST /api/chat`：Agent Chat 会话；POST 可传 `model`，后端执行白名单校验并保存实际模型名。
- `GET /api/health`：数据源健康状态和最后成功时间。

刷新任意标的使用本地命令 `python -m app.data_sources.market --symbol 600519`。普通六位代码按交易所前缀推断；指数使用 `000001.SH` 这类显式后缀，避免与平安银行 `000001` 混淆。

## 邮件

邮件属于后续阶段，当前不要求配置。以下变量仅供将来启用现有 dry-run 模块时使用。

使用个人邮箱自带 SMTP 即可，不购买邮件服务。需要设置：

```text
ROOFTOP_SMTP_HOST
ROOFTOP_SMTP_PORT=465
ROOFTOP_SMTP_USER
ROOFTOP_SMTP_PASSWORD        # 应用专用密码/授权码
ROOFTOP_ALERT_TO
ROOFTOP_EMAIL_SEND_ENABLED=1
```

未设置最后一项时只入本地 outbox。
