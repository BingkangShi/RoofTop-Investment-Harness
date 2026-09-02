"""Local-first relational and event-graph persistence.

SQLite is the zero-configuration system of record.  The graph tables implement
an auditable property-graph mirror and can later be synchronized to Neo4j CE.
No table in this schema can place or represent an order.
"""

import json
import math
import os
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
DATA_LAKE = Path(os.environ.get("ROOFTOP_DATA_LAKE", ROOT / "data_lake"))
DB_PATH = DATA_LAKE / "db" / "market_intelligence.db"

DATA_DIRS = (
    "db", "raw/market", "raw/documents", "raw/news", "normalized",
    "documents", "graphs", "cache", "models", "research/factors", "research/backtests",
    "private", "backups", "logs", "dead_letter", "outbox",
)

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS assets (
  id INTEGER PRIMARY KEY, symbol TEXT NOT NULL UNIQUE, exchange_symbol TEXT,
  name TEXT NOT NULL, market TEXT NOT NULL, asset_type TEXT NOT NULL,
  currency TEXT NOT NULL, data_status TEXT NOT NULL DEFAULT 'DEMO'
);
CREATE TABLE IF NOT EXISTS data_sources (
  id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  source_kind TEXT NOT NULL, access_mode TEXT NOT NULL, priority INTEGER NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1, license_note TEXT NOT NULL,
  homepage TEXT NOT NULL, health_status TEXT NOT NULL DEFAULT 'UNKNOWN',
  last_success_at TEXT, last_error_at TEXT, last_error TEXT
);
CREATE TABLE IF NOT EXISTS ingestion_runs (
  id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES data_sources(id),
  dataset TEXT NOT NULL, asset_symbol TEXT, started_at TEXT NOT NULL,
  finished_at TEXT, status TEXT NOT NULL, row_count INTEGER NOT NULL DEFAULT 0,
  raw_path TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS data_quality_checks (
  id INTEGER PRIMARY KEY, asset_symbol TEXT NOT NULL, check_name TEXT NOT NULL,
  source_a TEXT NOT NULL, source_b TEXT NOT NULL, status TEXT NOT NULL,
  metric REAL, details_json TEXT NOT NULL, checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prices (
  asset_id INTEGER NOT NULL REFERENCES assets(id), trade_date TEXT NOT NULL,
  open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
  volume REAL NOT NULL DEFAULT 0, amount REAL,
  source_id INTEGER REFERENCES data_sources(id), captured_at TEXT,
  raw_path TEXT, is_demo INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(asset_id, trade_date)
);
CREATE TABLE IF NOT EXISTS quote_snapshots (
  asset_symbol TEXT NOT NULL, asset_name TEXT NOT NULL,
  observed_at TEXT NOT NULL, price REAL NOT NULL, previous_close REAL,
  open REAL, high REAL, low REAL, change_value REAL, change_pct REAL,
  volume REAL, amount REAL, turnover_rate REAL,
  source_id INTEGER NOT NULL REFERENCES data_sources(id),
  captured_at TEXT NOT NULL, raw_path TEXT NOT NULL,
  PRIMARY KEY(asset_symbol, observed_at, source_id)
);
CREATE TABLE IF NOT EXISTS minute_bars (
  asset_symbol TEXT NOT NULL, bar_time TEXT NOT NULL,
  interval_minutes INTEGER NOT NULL, open REAL NOT NULL, high REAL NOT NULL,
  low REAL NOT NULL, close REAL NOT NULL, volume REAL, amount REAL,
  bar_kind TEXT NOT NULL DEFAULT 'OHLC',
  source_id INTEGER NOT NULL REFERENCES data_sources(id),
  captured_at TEXT NOT NULL, raw_path TEXT NOT NULL,
  PRIMARY KEY(asset_symbol, bar_time, interval_minutes, source_id)
);
CREATE TABLE IF NOT EXISTS market_daily_bars (
  asset_symbol TEXT NOT NULL, trade_date TEXT NOT NULL, adjust_mode TEXT NOT NULL,
  open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
  volume REAL, amount REAL, source_id INTEGER NOT NULL REFERENCES data_sources(id),
  captured_at TEXT NOT NULL, raw_path TEXT NOT NULL,
  PRIMARY KEY(asset_symbol, trade_date, adjust_mode, source_id)
);
CREATE TABLE IF NOT EXISTS portfolios (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, as_of TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY, portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
  asset_id INTEGER NOT NULL REFERENCES assets(id), quantity REAL NOT NULL,
  cost_price REAL NOT NULL, current_price REAL NOT NULL, highest_since_entry REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL,
  source_type TEXT NOT NULL, reliability REAL NOT NULL,
  verification_status TEXT NOT NULL, checked_at TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS evidence (
  id INTEGER PRIMARY KEY, claim TEXT NOT NULL, label TEXT NOT NULL,
  status TEXT NOT NULL, source_id INTEGER REFERENCES sources(id),
  observed_at TEXT, captured_at TEXT NOT NULL, independent_check TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hypotheses (
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, statement TEXT NOT NULL,
  status TEXT NOT NULL, falsification_rule TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, report_type TEXT NOT NULL,
  body TEXT NOT NULL, created_at TEXT NOT NULL, evidence_coverage REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS source_documents (
  id INTEGER PRIMARY KEY, doc_key TEXT NOT NULL UNIQUE,
  document_type TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
  source_url TEXT, source_name TEXT, published_at TEXT, observed_at TEXT,
  captured_at TEXT NOT NULL, raw_path TEXT NOT NULL,
  content_hash TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS source_document_versions (
  id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES source_documents(id),
  captured_at TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
  content_hash TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
  raw_path TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY, asset_id INTEGER REFERENCES assets(id),
  signal_type TEXT NOT NULL, score REAL NOT NULL, label TEXT NOT NULL,
  rationale TEXT NOT NULL, model_version TEXT NOT NULL, generated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analysis_runs (
  id INTEGER PRIMARY KEY, agent_name TEXT NOT NULL, input_snapshot TEXT NOT NULL,
  output_snapshot TEXT NOT NULL, model_name TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS strategies (
  id INTEGER PRIMARY KEY, strategy_key TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  category TEXT NOT NULL, description TEXT NOT NULL, implementation TEXT NOT NULL,
  source_framework TEXT NOT NULL, version TEXT NOT NULL, status TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS strategy_risk_policies (
  id INTEGER PRIMARY KEY, strategy_id INTEGER NOT NULL UNIQUE REFERENCES strategies(id),
  stop_loss_pct REAL NOT NULL, take_profit_pct REAL NOT NULL,
  trailing_stop_pct REAL NOT NULL, max_position_pct REAL NOT NULL,
  max_drawdown_pct REAL NOT NULL, max_daily_loss_pct REAL NOT NULL,
  turnover_limit REAL, liquidity_rule TEXT NOT NULL, stress_rules_json TEXT NOT NULL,
  version TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS factors (
  id INTEGER PRIMARY KEY, factor_key TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  family TEXT NOT NULL, expression TEXT NOT NULL, direction INTEGER NOT NULL,
  description TEXT NOT NULL, source_framework TEXT NOT NULL,
  version TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS strategy_factors (
  strategy_id INTEGER NOT NULL REFERENCES strategies(id),
  factor_id INTEGER NOT NULL REFERENCES factors(id), weight REAL NOT NULL,
  role TEXT NOT NULL, PRIMARY KEY(strategy_id,factor_id)
);
CREATE TABLE IF NOT EXISTS factor_runs (
  id INTEGER PRIMARY KEY, factor_id INTEGER NOT NULL REFERENCES factors(id),
  universe TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
  status TEXT NOT NULL, row_count INTEGER NOT NULL DEFAULT 0,
  ic REAL, rank_ic REAL, coverage REAL, params_json TEXT NOT NULL,
  metrics_json TEXT, raw_path TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS backtest_runs (
  id INTEGER PRIMARY KEY, strategy_id INTEGER NOT NULL REFERENCES strategies(id),
  asset_symbol TEXT NOT NULL, framework TEXT NOT NULL,
  data_start TEXT, data_end TEXT, started_at TEXT NOT NULL, finished_at TEXT,
  status TEXT NOT NULL, config_json TEXT NOT NULL, metrics_json TEXT,
  equity_json TEXT, raw_path TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS event_graphs (
  id INTEGER PRIMARY KEY, graph_key TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
  thesis TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS graph_nodes (
  id INTEGER PRIMARY KEY, graph_id INTEGER NOT NULL REFERENCES event_graphs(id),
  node_key TEXT NOT NULL, node_type TEXT NOT NULL, label TEXT NOT NULL,
  fact_opinion TEXT NOT NULL, properties_json TEXT NOT NULL DEFAULT '{}',
  relational_ref TEXT, source_url TEXT, observed_at TEXT,
  UNIQUE(graph_id, node_key)
);
CREATE TABLE IF NOT EXISTS graph_edges (
  id INTEGER PRIMARY KEY, graph_id INTEGER NOT NULL REFERENCES event_graphs(id),
  from_node_id INTEGER NOT NULL REFERENCES graph_nodes(id),
  to_node_id INTEGER NOT NULL REFERENCES graph_nodes(id),
  relation_type TEXT NOT NULL, confidence REAL NOT NULL,
  properties_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS graph_snapshots (
  id INTEGER PRIMARY KEY, graph_id INTEGER NOT NULL REFERENCES event_graphs(id),
  captured_at TEXT NOT NULL, content_hash TEXT NOT NULL,
  payload_json TEXT NOT NULL, snapshot_path TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alert_outbox (
  id INTEGER PRIMARY KEY, dedupe_key TEXT NOT NULL UNIQUE, channel TEXT NOT NULL,
  subject TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',
  created_at TEXT NOT NULL, sent_at TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS chat_sessions (
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
  id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES chat_sessions(id),
  role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
  content TEXT NOT NULL, model_name TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS semantic_documents (
  id INTEGER PRIMARY KEY, doc_key TEXT NOT NULL UNIQUE, page TEXT NOT NULL,
  title TEXT NOT NULL, body TEXT NOT NULL, source_ref TEXT,
  content_hash TEXT NOT NULL, embedding BLOB, embedding_dim INTEGER,
  model_name TEXT, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prices_asset_date ON prices(asset_id, trade_date);
CREATE INDEX IF NOT EXISTS idx_quote_symbol_time ON quote_snapshots(asset_symbol, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_minute_symbol_time ON minute_bars(asset_symbol, interval_minutes, bar_time DESC);
CREATE INDEX IF NOT EXISTS idx_market_daily_symbol_date ON market_daily_bars(asset_symbol, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id,id);
CREATE INDEX IF NOT EXISTS idx_semantic_page ON semantic_documents(page);
CREATE INDEX IF NOT EXISTS idx_graph_nodes_ref ON graph_nodes(relational_ref);
CREATE INDEX IF NOT EXISTS idx_source_documents_type_time ON source_documents(document_type,published_at DESC);
CREATE INDEX IF NOT EXISTS idx_source_document_versions_doc_time ON source_document_versions(document_id,captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_graph_snapshots_graph_time ON graph_snapshots(graph_id,captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_factor_runs_factor_time ON factor_runs(factor_id,started_at DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_strategy_time ON backtest_runs(strategy_id,started_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_graph_edge_unique
  ON graph_edges(graph_id,from_node_id,to_node_id,relation_type);
"""

ASSETS = [
    ("510300", "SH.510300", "沪深300ETF示例", "CN", "ETF", "CNY", 3.950, -0.02),
    ("159915", "SZ.159915", "创业板ETF示例", "CN", "ETF", "CNY", 2.450, 0.04),
    ("000001.SH", "SH.000001", "上证指数", "CN", "INDEX", "CNY", 3634.20, 0.03),
    ("HSI", "HK.HSI", "恒生指数", "HK", "INDEX", "HKD", 25180.00, 0.06),
    ("SPX", "US.SPX", "标普500", "US", "INDEX", "USD", 6365.00, 0.08),
    ("NDX", "US.NDX", "纳斯达克100", "US", "INDEX", "USD", 23210.00, 0.10),
    ("N225", "JP.N225", "日经225", "JP", "INDEX", "JPY", 41820.00, -0.02),
    ("DAX", "DE.DAX", "德国DAX", "DE", "INDEX", "EUR", 24220.00, 0.04),
    ("XAU", "GLOBAL.XAU", "黄金现货", "GLOBAL", "COMMODITY", "USD", 3388.00, 0.07),
    ("HG", "GLOBAL.HG", "COMEX铜", "GLOBAL", "COMMODITY", "USD", 4.42, 0.02),
]

DATA_SOURCES = [
    ("tencent", "腾讯公开行情", "public_quote", "public_http", 5, 1, "无需账号；非承诺型公开网页接口，必须缓存并监控字段变化", "https://gu.qq.com/"),
    ("eastmoney", "东方财富公开行情", "public_quote", "public_http", 8, 1, "无需账号；作为腾讯行情的在线备份，接口可能调整", "https://quote.eastmoney.com/"),
    ("akshare", "AKShare", "aggregator", "public_http", 10, 1, "MIT；上游网站接口可能变更", "https://github.com/akfamily/akshare"),
    ("tdx_public", "通达信公开行情协议", "public_quote", "public_tcp", 12, 1, "无需券商账号；使用 MIT tdxrs 客户端，历史深度取决于公开节点", "https://github.com/jiangtaovan/tdxrs"),
    ("qmt", "QMT/miniQMT (xtquant)", "broker_desktop", "local_client", 20, 0, "软件可免费安装，但 API/行情权限由开户券商决定", "https://dict.thinktrader.net"),
    ("futu", "Futu OpenD", "broker_desktop", "local_gateway", 30, 0, "登录免费；不同市场行情权限和额度不同", "https://openapi.futunn.com/futu-api-doc/"),
    ("tdx_local", "通达信本地日线", "desktop_cache", "local_files", 40, 0, "通过客户端盘后下载；读取器使用 MIT 开源实现", "https://github.com/mootdx/mootdx"),
    ("baostock", "BaoStock", "public_api", "public_http", 50, 1, "无需注册；用于 A 股日频及 5/15/30/60 分钟补漏，ETF和指数覆盖需逐项校验", "https://www.baostock.com/"),
    ("bilibili", "哔哩哔哩公开内容", "social_media", "local_yt_dlp", 60, 0, "公开元数据优先；账号只通过本机浏览器会话使用，不保存密码；遵守平台规则和个人信息最小化", "https://www.bilibili.com/"),
    ("x_official", "X API", "social_media", "official_api", 61, 0, "官方读接口按量付费；零付费模式默认禁用，不使用非官方爬虫绕过限制", "https://docs.x.com/x-api/"),
    ("sec_edgar", "SEC EDGAR", "filing", "official_json_api", 62, 0, "免费免 Key；必须提供合规 User-Agent 身份，内部限速低于 SEC 的 10 请求/秒上限", "https://www.sec.gov/search-filings/edgar-application-programming-interfaces"),
    ("local_cache", "本地最后可信快照", "local_cache", "filesystem", 99, 1, "仅保可用性；必须显式标记陈旧，不能伪装实时", "local://data_lake"),
]


def ensure_data_lake() -> Path:
    for item in DATA_DIRS:
        (DATA_LAKE / item).mkdir(parents=True, exist_ok=True)
    return DATA_LAKE


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _series(final_price: float, trend: float, phase: float, days: int = 90):
    raw = []
    for i in range(days):
        progress = i / (days - 1)
        cycle = math.sin(i * 0.31 + phase) * 0.025 + math.sin(i * 0.09) * 0.018
        raw.append((1 + trend * (progress - 1)) * (1 + cycle))
    scale = final_price / raw[-1]
    return [round(v * scale, 4) for v in raw]


def _seed(conn: sqlite3.Connection) -> None:
    for row in DATA_SOURCES:
        conn.execute(
            """INSERT OR IGNORE INTO data_sources
               (code,name,source_kind,access_mode,priority,enabled,license_note,homepage)
               VALUES(?,?,?,?,?,?,?,?)""", row,
        )
    demo_source = conn.execute("SELECT id FROM data_sources WHERE code='local_cache'").fetchone()[0]
    for index, (symbol, exchange_symbol, name, market, kind, currency, final, trend) in enumerate(ASSETS):
        cursor = conn.execute(
            """INSERT INTO assets(symbol,exchange_symbol,name,market,asset_type,currency,data_status)
               VALUES(?,?,?,?,?,?,'DEMO')""",
            (symbol, exchange_symbol, name, market, kind, currency),
        )
        asset_id = cursor.lastrowid
        closes = _series(final, trend, index * 0.7)
        start = date(2026, 5, 14)
        for day, close in enumerate(closes):
            d = start + timedelta(days=day)
            conn.execute(
                """INSERT INTO prices(asset_id,trade_date,open,high,low,close,volume,source_id,captured_at,is_demo)
                   VALUES(?,?,?,?,?,?,?,?,?,1)""",
                (asset_id, d.isoformat(), close * .997, close * 1.012, close * .988,
                 close, 1_000_000 + day * 173, demo_source, "2026-08-11T21:00:00+08:00"),
            )
    conn.execute("INSERT INTO portfolios(name,as_of) VALUES(?,?)", ("公开演示组合", "2026-01-01"))
    portfolio_id = conn.execute("SELECT id FROM portfolios").fetchone()[0]
    broad_market_id = conn.execute("SELECT id FROM assets WHERE symbol='510300'").fetchone()[0]
    growth_id = conn.execute("SELECT id FROM assets WHERE symbol='159915'").fetchone()[0]
    conn.executemany(
        """INSERT INTO positions(portfolio_id,asset_id,quantity,cost_price,current_price,highest_since_entry)
           VALUES(?,?,?,?,?,?)""",
        [(portfolio_id, broad_market_id, 100, 3.900, 3.950, 4.100),
         (portfolio_id, growth_id, 100, 2.500, 2.450, 2.650)],
    )
    conn.executemany(
        """INSERT INTO sources(name,url,source_type,reliability,verification_status,checked_at,notes)
           VALUES(?,?,?,?,?,?,?)""",
        [("公开演示配置", "local://demo-configuration", "DEMO_CONFIGURATION", 1.0,
          "DEMO", "2026-01-01", "仅用于首次启动展示，不代表任何真实账户或投资建议"),
         ("上海证券交易所 ETF 公告", "https://www.sse.com.cn/", "PRIMARY_OFFICIAL", .95,
          "VERIFIED", "2026-08-12", "用于核验基金代码和公告")],
    )
    demo_config_source = conn.execute("SELECT id FROM sources WHERE source_type='DEMO_CONFIGURATION'").fetchone()[0]
    conn.execute(
        """INSERT INTO evidence(claim,label,status,source_id,observed_at,captured_at,independent_check)
           VALUES(?,?,?,?,?,?,?)""",
        ("公开版本内置两只 ETF 的匿名演示组合", "事实", "DEMO",
         demo_config_source, "2026-01-01", "2026-01-01", "演示数据不对应任何真实账户"),
    )
    hypotheses = [
        ("盈利修复验证", "行业需求回升可能改善样本公司的收入与盈利质量。", "UNVERIFIED",
         "跟踪订单、收入、现金流和毛利率；连续两个报告期未改善则下调置信度。"),
        ("利率路径变化", "实际利率下降可能提升长久期资产的估值容忍度。", "UNVERIFIED",
         "同时核对通胀、政策利率、期限溢价与盈利预期，避免仅凭单一宏观数据推断。"),
        ("估值与情绪背离", "极端悲观情绪与基本面稳定并存时可能形成研究窗口。", "UNVERIFIED",
         "验证估值分位、资金流、盈利修正和事件催化；基本面同步恶化则否定假设。"),
    ]
    conn.executemany(
        "INSERT INTO hypotheses(title,statement,status,falsification_rule,created_at) VALUES(?,?,?,?,?)",
        [(a, b, c, d, "2026-08-11") for a, b, c, d in hypotheses],
    )
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO event_graphs(graph_key,title,thesis,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
        ("ai-open-source-valuation", "AI 开源路线与估值事件图", hypotheses[0][1], "UNVERIFIED", now, now),
    )
    graph_id = conn.execute("SELECT id FROM event_graphs WHERE graph_key='ai-open-source-valuation'").fetchone()[0]
    conn.execute(
        """INSERT INTO graph_nodes(graph_id,node_key,node_type,label,fact_opinion,properties_json,relational_ref,source_url,observed_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (graph_id, "hypothesis-root", "HYPOTHESIS", "AI 商业模式挤压", "待验证假设", "{}",
         "trad://hypotheses/1", None, "2026-08-11"),
    )
    conn.execute(
        "INSERT INTO reports(title,report_type,body,created_at,evidence_coverage) VALUES(?,?,?,?,?)",
        ("初始投资纪律与待验证假设", "SYSTEM_BOOTSTRAP",
         json.dumps({"fact_opinion_separation": True, "order_execution": False}, ensure_ascii=False),
         "2026-08-11", .18),
    )


def initialize(path: Optional[Path] = None) -> Path:
    db_path = path or DB_PATH
    if path is None:
        ensure_data_lake()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(db_path)) as conn:
        conn.executescript(SCHEMA)
        quote_columns = {row[1] for row in conn.execute("PRAGMA table_info(quote_snapshots)")}
        if "turnover_rate" not in quote_columns:
            conn.execute("ALTER TABLE quote_snapshots ADD COLUMN turnover_rate REAL")
        # Reference sources are synchronized on every start so schema/data-source
        # additions also reach an existing local database.
        for row in DATA_SOURCES:
            conn.execute(
                """INSERT OR IGNORE INTO data_sources
                   (code,name,source_kind,access_mode,priority,enabled,license_note,homepage)
                   VALUES(?,?,?,?,?,?,?,?)""", row,
            )
        if not conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]:
            _seed(conn)
        # Once a real series exists, synthetic points for that asset must not
        # survive (especially synthetic weekend dates).
        conn.execute(
            """DELETE FROM prices WHERE is_demo=1 AND asset_id IN
               (SELECT DISTINCT asset_id FROM prices WHERE is_demo=0)"""
        )
        conn.commit()
    return db_path


if __name__ == "__main__":
    print(initialize())
