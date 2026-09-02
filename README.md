# RoofTop Investment Harness（天台智投）

RoofTop Investment Harness 是一个面向个人研究者的 local-first 二级市场分析平台。它把行情、财报、公告、新闻、社交信息、事件图、因子、策略与风控放在同一个本地工作台中，只提供研究和人工复核提示，不连接券商，也不执行交易。

仓库不包含任何真实 API Key、账号凭证、个人持仓、数据库、行情缓存或模型权重。Logo 中跃出屋顶的人由安全绳和保护带连接，表达“承担市场风险，但始终保护本金”的产品含义。

## 目录

- [核心能力](#核心能力)
- [安全边界](#安全边界)
- [快速开始](#快速开始)
- [模型配置](#模型配置)
- [数据与持久化](#数据与持久化)
- [项目结构](#项目结构)
- [进一步阅读](#进一步阅读)

## 核心能力

- 行情图支持分时、五日、1/5/15/30/60/120 分钟以及日/周/月/季/年 K，包含缩放、拖动、悬浮信息、成本线、止盈止损线、成交额、KDJ、MACD 和筹码分布。
- 传统金融数据使用 SQLite 持久化；事件节点、关系和快照可导出至 Neo4j Community。
- 策略与风控一一绑定，支持因子评估、三年回测、收益曲线、风险指标和运行结果留档。
- Agent Chat 可在已配置且列入白名单的 LLM 之间切换，并保存使用的实际模型名。
- 情报、策略、研报和事件图页面支持严格匹配与本地语义搜索。
- 外部查询遵循“原始响应、规范化记录、抓取审计”三重持久化，避免重复查询后丢失证据。
- 免费行情链路以 AKShare、腾讯公开行情、东方财富公开行情、通达信本地数据和 BaoStock 为主，并保留最后可信快照降级机制。

## 安全边界

- 本项目只做分析，不下单；`order_execution_enabled` 固定为 `false`。
- 邮件提醒默认是 dry-run，只有显式设置 `ROOFTOP_EMAIL_SEND_ENABLED=1` 才允许发送。
- 页面与报告应区分“事实”“观点”和“待验证假设”，外部事实必须保留来源及采集时间。
- 本地最后可信快照只能维持可用性，界面必须明确显示数据时点和陈旧状态。
- 免费网页接口可能调整或受平台条款限制，启用适配器前应核对授权、频率限制和字段稳定性。

## 快速开始

Windows PowerShell：

```powershell
git clone https://github.com/BingkangShi/RoofTop-Investment-Harness.git
cd RoofTop-Investment-Harness
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\run.ps1
```

打开 <http://127.0.0.1:8765>。

基础 Web 服务主要使用 Python 标准库。按需安装数据、研究和搜索依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-data.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-research.txt
# RTX 5080 用户先按 docs/SEARCH.md 安装匹配的 PyTorch CUDA wheel
.\.venv\Scripts\python.exe -m pip install -r requirements-search.txt
```

抓取近三年免费行情并运行测试：

```powershell
.\.venv\Scripts\python.exe -m app.data_sources.ingest --years 3
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 模型配置

复制模型路由模板到项目外部的私有目录，填入你自己的服务地址、真实模型名和 API Key：

```powershell
Copy-Item .\configure_list.sample.json '<PlaceHolder>\configure_list.json'
$env:ROOFTOP_MODEL_CONFIG='<PlaceHolder>\configure_list.json'
$env:ROOFTOP_CODEX_MODEL='<PlaceHolder>'
.\run.ps1
```

`configure_list.json` 已被 `.gitignore` 排除。不要把真实密钥写入仓库、Issue、日志或截图；模板中的 `<your API KEY>` 和 `<PlaceHolder>` 必须在项目外部替换。

当前测试路由别名为 `deepseek-v4-flash`，文档/图像 MLLM 路由别名为 `qwen3.8-max`。供应商实际模型名通过模板中的 `api_model` 配置。

## 数据与持久化

默认数据目录是项目内的 `data_lake/`，也可通过 `ROOFTOP_DATA_LAKE` 指向其他本地路径。该目录和所有常见数据库扩展名都已加入 `.gitignore`。

日线后备链路：

`AKShare → 通达信本地日线 → BaoStock → 本地最后可信快照`

准实时与分钟链路：

`腾讯公开行情 → 东方财富公开行情 → SQLite 最后可信快照`

Neo4j 本地启动：

```powershell
Copy-Item infrastructure\.env.example infrastructure\.env
# 把 infrastructure\.env 中的 <PlaceHolder> 换成本机强密码
docker compose --env-file infrastructure\.env -f infrastructure\docker-compose.graph.yml up -d
.\.venv\Scripts\python.exe -m app.graph_export
```

## 项目结构

```text
app/                        后端、分析逻辑、数据适配器与 Web UI
docs/                       架构、数据覆盖、搜索、策略与接入说明
infrastructure/             Neo4j Community 本地编排
tests/                      单元测试
config.example.json         无凭证的功能配置示例
configure_list.sample.json  模型路由与密钥占位模板
```

## 进一步阅读

- [系统架构](docs/ARCHITECTURE.md)
- [免费数据源方案](docs/FREE_DATA_SOURCES.md)
- [行情数据覆盖与持久化](docs/DATA_COVERAGE.md)
- [策略研究与风控](docs/STRATEGY_RESEARCH.md)
- [语义搜索说明](docs/SEARCH.md)
- [图表 UI 像素与交互契约](docs/CHART_UI_PIXEL_MAP.md)
- [社交平台、新闻与 SEC 接入](docs/ACCOUNT_SETUP_BILIBILI_X_SEC.md)
- [API 要求](docs/API_REQUIREMENTS.md)

MiroFish 的 AGPL-3.0 许可要求需要单独评估，因此本项目只借鉴“种子信息—实体关系—事件推演—报告”的思路，不复制其代码。ValueCell 的产品交互与本地数据理念仅作为设计参考；AKQuant 的研究模块按其许可和边界接入。
