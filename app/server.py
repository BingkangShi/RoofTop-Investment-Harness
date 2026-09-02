"""Dependency-free local HTTP server for the analysis MVP."""

import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
from contextlib import closing
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .analytics import calculate_risk_lines, evaluate_discipline, market_risk, policy_catalog
from .alerts import smtp_status
from .db import DATA_LAKE, ROOT, connect, initialize
from .data_sources.desktop import probe_desktop_sources
from .data_sources.market import DEFAULT_SYMBOLS, normalize_symbol, refresh_market_data
from .models import route_status
from .charting import SUPPORTED_PERIODS, add_ma5, add_technical_indicators, build_chart_series
from .chat import available_models, list_sessions, messages_for, send_message
from .search import exact_search, semantic_search, semantic_status, sync_semantic_index
from .persistence import persist_event_graph
from .research import run_backtest, run_factor, strategy_lab_payload
from .data_sources.intelligence import (collect_bilibili, collect_sec_submissions,
                                        collect_x_recent, source_access_status)
from .data_sources.reports import refresh_report_library, report_library_payload, trigger_report_refresh

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _rows(rows):
    return [dict(row) for row in rows]


def dashboard_payload():
    with closing(connect()) as conn:
        latest_real = conn.execute("SELECT MAX(trade_date) FROM prices WHERE is_demo=0").fetchone()[0]
        live_market_rows = conn.execute(
            """
            SELECT q.asset_symbol AS symbol,q.asset_name AS name,'CN' AS market,
                   CASE WHEN q.asset_symbol LIKE '%.SH' OR q.asset_symbol LIKE '%.SZ' THEN 'INDEX'
                        WHEN q.asset_symbol LIKE '5%' OR q.asset_symbol LIKE '1%' THEN 'ETF' ELSE 'EQUITY' END AS asset_type,
                   'CNY' AS currency,q.price,q.observed_at AS trade_date,q.change_pct,
                   ds.code AS source,q.captured_at
            FROM quote_snapshots q JOIN data_sources ds ON ds.id=q.source_id
            WHERE q.rowid=(SELECT q2.rowid FROM quote_snapshots q2
                           JOIN data_sources ds2 ON ds2.id=q2.source_id
                           WHERE q2.asset_symbol=q.asset_symbol
                           ORDER BY q2.observed_at DESC,ds2.priority LIMIT 1)
            ORDER BY CASE q.asset_symbol WHEN '000001.SH' THEN 0 WHEN '510300' THEN 1 WHEN '159915' THEN 2 ELSE 3 END
            LIMIT 8
            """
        ).fetchall()
        fallback_market_rows = conn.execute(
            """
            SELECT a.symbol,a.name,a.market,a.asset_type,a.currency,a.data_status,
                   p.close AS price,p.trade_date,
                   (p.close / (SELECT p2.close FROM prices p2 WHERE p2.asset_id=a.id ORDER BY p2.trade_date DESC LIMIT 1 OFFSET 1)-1)*100 AS change_pct
            FROM assets a JOIN prices p ON p.asset_id=a.id
            WHERE p.trade_date=(SELECT MAX(p3.trade_date) FROM prices p3 WHERE p3.asset_id=a.id)
              AND a.symbol NOT IN ('510300','159915')
            ORDER BY CASE a.market WHEN 'CN' THEN 1 WHEN 'HK' THEN 2 WHEN 'US' THEN 3 WHEN 'JP' THEN 4 ELSE 5 END
            """
        ).fetchall()
        market_rows = live_market_rows or fallback_market_rows
        latest_quote_at = conn.execute("SELECT MAX(observed_at) FROM quote_snapshots").fetchone()[0]
        positions = []
        for row in conn.execute(
            """
            SELECT p.id,a.symbol,a.name,a.market,a.asset_type,a.currency,p.quantity,
                   p.cost_price,p.current_price,p.highest_since_entry,
                   p.quantity*p.current_price AS market_value,
                   (p.current_price/p.cost_price-1)*100 AS pnl_pct
            FROM positions p JOIN assets a ON a.id=p.asset_id ORDER BY p.id
            """
        ):
            item = dict(row)
            lines = calculate_risk_lines(item["cost_price"], item["current_price"], item["highest_since_entry"], item["market"], item["asset_type"])
            item["lines"] = lines
            item["discipline"] = evaluate_discipline(item["current_price"], lines)
            positions.append(item)
        hypotheses = _rows(conn.execute("SELECT * FROM hypotheses ORDER BY id").fetchall())
        evidence = _rows(
            conn.execute(
                """SELECT e.*,s.name AS source_name,s.url,s.reliability,s.verification_status
                   FROM evidence e LEFT JOIN sources s ON s.id=e.source_id ORDER BY e.id DESC LIMIT 8"""
            ).fetchall()
        )
        source_health = _rows(conn.execute(
            """SELECT code,name,source_kind,access_mode,priority,enabled,health_status,
                      last_success_at,last_error_at,last_error,homepage,license_note
               FROM data_sources ORDER BY priority"""
        ).fetchall())
        graph_summary = dict(conn.execute(
            """SELECT (SELECT COUNT(*) FROM event_graphs) AS graphs,
                      (SELECT COUNT(*) FROM graph_nodes) AS nodes,
                      (SELECT COUNT(*) FROM graph_edges) AS edges"""
        ).fetchone())
        return {
            "meta": {
                "api_version": 2,
                "as_of": latest_quote_at or latest_real or "2026-08-11",
                "mode": "FREE_DELAYED_REALTIME" if latest_quote_at else ("LOCAL_REAL_WITH_DEMO_BENCHMARKS" if latest_real else "DEMO_OFFLINE"),
                "warning": "A股大盘、ETF和个股使用免费公开行情并落入本地缓存；请以显示的数据时点判断延迟。海外大盘仍可能是演示数据。系统不具备下单能力。",
                "fact_opinion_rule": "所有结论必须标记为事实、观点或待验证假设。",
            },
            "portfolio": {
                "name": "公开演示组合",
                "market_value": round(sum(p["market_value"] for p in positions), 2),
                "unrealized_pnl": round(sum((p["current_price"] - p["cost_price"]) * p["quantity"] for p in positions), 2),
                "positions": positions,
            },
            "markets": _rows(market_rows),
            "hypotheses": hypotheses,
            "evidence": evidence,
            "risk_policies": policy_catalog(),
            "strategy_lab": strategy_lab_payload(),
            "intelligence_sources": source_access_status(),
            "source_health": source_health,
            "event_graph": graph_summary,
            "model_routes": route_status(),
            "email": smtp_status(),
            "agents": [
                {"name": "信源审计 Agent", "status": "规则引擎可用", "role": "来源评级、交叉验证、引用完整性"},
                {"name": "市场情绪 Agent", "status": "等待免费公开信源适配器", "role": "多渠道情绪、拥挤度与反向研究信号"},
                {"name": "基本面研究 Agent", "status": "等待模型路由", "role": "事实抽取、产业链与估值研究"},
                {"name": "风险纪律 Agent", "status": "本地模型可用", "role": "止盈止损、波动、回撤与仓位约束"},
            ],
        }


def chart_payload(symbol, period="1d"):
    with closing(connect()) as conn:
        asset = conn.execute("SELECT * FROM assets WHERE symbol=?", (symbol,)).fetchone()
        quote = conn.execute(
            """SELECT q.*,ds.code AS source FROM quote_snapshots q JOIN data_sources ds ON ds.id=q.source_id
               WHERE q.asset_symbol=? ORDER BY q.observed_at DESC,ds.priority LIMIT 1""", (symbol,),
        ).fetchone()
        if not asset and not quote:
            return None
        asset_dict = dict(asset) if asset else {
            "symbol": symbol, "name": quote["asset_name"], "market": "CN", "asset_type": "EQUITY",
            "currency": "CNY", "data_status": "REALTIME_ONLY",
        }
        position = conn.execute("SELECT * FROM positions WHERE asset_id=?", (asset["id"],)).fetchone() if asset else None
        lines = None
        if position:
            lines = calculate_risk_lines(position["cost_price"], position["current_price"], position["highest_since_entry"], asset_dict["market"], asset_dict["asset_type"])
        chart = build_chart_series(conn, symbol, period)
        # Keep the current daily candle live between slower historical refreshes.
        # Quote volume/amount are current-day cumulative values, so they replace
        # rather than add to the current daily candle.
        if quote and period == "1d" and chart["series"]:
            observed_date = str(quote["observed_at"])[:10]
            current = {"time": observed_date, "open": quote["open"] or quote["price"],
                       "high": quote["high"] or quote["price"], "low": quote["low"] or quote["price"],
                       "close": quote["price"], "volume": quote["volume"] or 0,
                       "amount": quote["amount"] or 0}
            if str(chart["series"][-1]["time"])[:10] == observed_date:
                chart["series"][-1].update(current)
            elif observed_date > str(chart["series"][-1]["time"])[:10]:
                chart["series"].append(current)
            add_ma5(chart["series"])
            add_technical_indicators(chart["series"], symbol)
            chart["coverage"]["view_last_bar"] = observed_date
        if quote and chart["series"] and quote["turnover_rate"] is not None:
            chart["series"][-1]["turnover_rate"] = quote["turnover_rate"]
        daily = build_chart_series(conn, symbol, "1d")["series"]
        return {"asset": asset_dict, "quote": dict(quote) if quote else None, "lines": lines,
                "risk": market_risk([p["close"] for p in daily]),
                "periods": [{"key": key, "label": label} for key, label in SUPPORTED_PERIODS.items()],
                **chart}


def market_quotes_payload(symbols):
    normalized = [normalize_symbol(symbol) for symbol in symbols]
    if not normalized:
        return {"quotes": []}
    placeholders = ",".join("?" for _ in normalized)
    with closing(connect()) as conn:
        rows = conn.execute(
            f"""SELECT q.*,ds.code AS source FROM quote_snapshots q JOIN data_sources ds ON ds.id=q.source_id
                WHERE q.asset_symbol IN ({placeholders}) AND q.rowid=(
                  SELECT q2.rowid FROM quote_snapshots q2 JOIN data_sources ds2 ON ds2.id=q2.source_id
                  WHERE q2.asset_symbol=q.asset_symbol ORDER BY q2.observed_at DESC,ds2.priority LIMIT 1)
                ORDER BY q.asset_symbol""", normalized,
        ).fetchall()
    return {"quotes": _rows(rows)}


def market_minutes_payload(symbol, limit=300):
    symbol = normalize_symbol(symbol)
    limit = max(1, min(int(limit), 2000))
    with closing(connect()) as conn:
        rows = conn.execute(
            """SELECT * FROM (SELECT m.bar_time,m.interval_minutes,m.open,m.high,m.low,m.close,
                                      m.volume,m.amount,m.bar_kind,ds.code AS source,m.captured_at
                               FROM minute_bars m JOIN data_sources ds ON ds.id=m.source_id
                               WHERE m.asset_symbol=? AND m.interval_minutes=1 AND m.source_id=(
                                 SELECT source_id FROM minute_bars WHERE asset_symbol=? AND interval_minutes=1
                                 ORDER BY captured_at DESC LIMIT 1)
                               ORDER BY m.bar_time DESC LIMIT ?)
               ORDER BY bar_time""", (symbol, symbol, limit),
        ).fetchall()
    return {"symbol": symbol, "bars": _rows(rows)}


class MarketDataRefresher(threading.Thread):
    """Daemon refresher; HTTP handlers only read SQLite and remain responsive."""

    def __init__(self):
        super().__init__(name="rooftop-market-refresh", daemon=True)
        configured = os.environ.get("ROOFTOP_MARKET_SYMBOLS", ",".join(DEFAULT_SYMBOLS))
        self.symbols = [item.strip() for item in configured.split(",") if item.strip()]
        self.poll_seconds = max(5, int(os.environ.get("ROOFTOP_MARKET_POLL_SECONDS", "10")))
        self.minute_seconds = max(10, int(os.environ.get("ROOFTOP_MINUTE_REFRESH_SECONDS", "10")))
        self.daily_seconds = max(3600, int(os.environ.get("ROOFTOP_DAILY_REFRESH_SECONDS", "21600")))
        self.history_seconds = max(3600, int(os.environ.get("ROOFTOP_HISTORY_REFRESH_SECONDS", "86400")))
        self.stop_event = threading.Event()
        self.history_process = None

    def _launch_history_refresh(self):
        """Run slow/native history clients outside the Web process and its GIL."""
        if self.history_process and self.history_process.poll() is None:
            return False
        log_path = DATA_LAKE / "logs" / "history_refresh.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open("ab")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self.history_process = subprocess.Popen(
                [sys.executable, "-m", "app.data_sources.market", "--no-minute", "--no-daily"],
                cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT, creationflags=flags,
            )
        finally:
            log.close()
        return True

    def run(self):
        last_minute = 0.0
        last_daily = 0.0
        last_history = 0.0
        while not self.stop_event.is_set():
            try:
                now = time.monotonic()
                include_minutes = now - last_minute >= self.minute_seconds
                include_daily = now - last_daily >= self.daily_seconds
                include_history = now - last_history >= self.history_seconds
                refresh_market_data(self.symbols, include_minutes=include_minutes, include_daily=include_daily,
                                    include_history=False)
                if include_minutes:
                    last_minute = now
                if include_daily:
                    last_daily = now
                if include_history and self._launch_history_refresh():
                    last_history = now
            except Exception as exc:
                print(f"[market-refresh] {exc!r}")
            self.stop_event.wait(self.poll_seconds)

    def stop(self):
        self.stop_event.set()
        if self.history_process and self.history_process.poll() is None:
            self.history_process.terminate()


class ReportLibraryRefresher(threading.Thread):
    """Slow report collector isolated from quote refresh and HTTP requests."""

    def __init__(self):
        super().__init__(name="rooftop-report-refresh", daemon=True)
        self.poll_seconds = max(300, int(os.environ.get("ROOFTOP_REPORT_POLL_SECONDS", "300")))
        self.stop_event = threading.Event()

    def run(self):
        # Do not delay Web startup; the first poll begins in its own thread.
        while not self.stop_event.is_set():
            try:
                result = refresh_report_library()
                print(f"[report-refresh] {result['status']} documents={result['documents']} errors={len(result['errors'])}")
            except Exception as exc:
                print(f"[report-refresh] {exc!r}")
            self.stop_event.wait(self.poll_seconds)

    def stop(self):
        self.stop_event.set()


class Handler(BaseHTTPRequestHandler):
    server_version = "MarketIntelMVP/0.1"

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def _json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path):
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(STATIC_DIR.resolve())
        except (FileNotFoundError, ValueError):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = resolved.read_bytes()
        content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            with closing(connect()) as conn:
                sources = _rows(conn.execute("SELECT code,enabled,health_status,last_success_at,last_error FROM data_sources ORDER BY priority").fetchall())
                persistence = dict(conn.execute(
                    """SELECT
                         (SELECT COUNT(*) FROM ingestion_runs) AS ingestion_runs,
                         (SELECT COUNT(*) FROM quote_snapshots) AS quote_snapshots,
                         (SELECT COUNT(*) FROM minute_bars) AS minute_bars,
                         (SELECT COUNT(*) FROM market_daily_bars) AS daily_bars,
                         (SELECT COUNT(*) FROM source_documents) AS source_documents,
                         (SELECT COUNT(*) FROM source_document_versions) AS document_versions,
                         (SELECT COUNT(*) FROM event_graphs) AS event_graphs,
                         (SELECT COUNT(*) FROM graph_snapshots) AS graph_snapshots,
                         (SELECT COUNT(*) FROM analysis_runs) AS analysis_runs"""
                ).fetchone())
            self._json({"status": "ok", "api_version": 2, "database": "sqlite", "graph_mirror": "sqlite_property_graph",
                        "neo4j": "optional", "order_execution": False, "sources": sources,
                        "persistence": persistence,
                        "desktop_backups": probe_desktop_sources(),
                        "models": route_status(), "email": smtp_status()})
            return
        if parsed.path == "/api/event-graphs":
            with closing(connect()) as conn:
                graphs = _rows(conn.execute("SELECT * FROM event_graphs ORDER BY updated_at DESC").fetchall())
            self._json({"graphs": graphs})
            return
        graph_match = re.fullmatch(r"/api/event-graphs/(\d+)", parsed.path)
        if graph_match:
            graph_id = int(graph_match.group(1))
            with closing(connect()) as conn:
                graph = conn.execute("SELECT * FROM event_graphs WHERE id=?", (graph_id,)).fetchone()
                nodes = _rows(conn.execute("SELECT * FROM graph_nodes WHERE graph_id=? ORDER BY id", (graph_id,)).fetchall())
                edges = _rows(conn.execute("SELECT * FROM graph_edges WHERE graph_id=? ORDER BY id", (graph_id,)).fetchall())
            self._json({"graph": dict(graph) if graph else None, "nodes": nodes, "edges": edges},
                       HTTPStatus.OK if graph else HTTPStatus.NOT_FOUND)
            return
        if parsed.path == "/api/search/status":
            self._json(semantic_status())
            return
        if parsed.path == "/api/strategy-lab":
            self._json(strategy_lab_payload())
            return
        if parsed.path == "/api/intelligence-sources":
            self._json({"sources": source_access_status(), "credentials_in_database": False})
            return
        if parsed.path == "/api/reports/library":
            self._json(report_library_payload())
            return
        if parsed.path == "/api/search":
            query = parse_qs(parsed.query)
            text_query = query.get("q", [""])[0]
            mode = query.get("mode", ["exact"])[0]
            page = query.get("page", [None])[0]
            try:
                results = semantic_search(text_query, page) if mode == "semantic" else exact_search(text_query, page)
                self._json({"query": text_query, "mode": mode, "page": page, "results": results,
                            "semantic": semantic_status()})
            except (RuntimeError, ImportError, ModuleNotFoundError) as exc:
                self._json({"error": str(exc), "semantic": semantic_status()}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        if parsed.path == "/api/chat/sessions":
            self._json({"sessions": list_sessions()})
            return
        if parsed.path == "/api/chat/models":
            self._json(available_models())
            return
        if parsed.path == "/api/chat/messages":
            try:
                session_id = int(parse_qs(parsed.query).get("session_id", ["0"])[0])
                self._json({"session_id": session_id, "messages": messages_for(session_id)})
            except ValueError as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/dashboard":
            self._json(dashboard_payload())
            return
        if parsed.path == "/api/market/quotes":
            symbols = parse_qs(parsed.query).get("symbols", [","])[0].split(",")
            try:
                self._json(market_quotes_payload([item for item in symbols if item]))
            except ValueError as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        minute_match = re.fullmatch(r"/api/market/minutes/([^/]+)", parsed.path)
        if minute_match:
            try:
                limit = parse_qs(parsed.query).get("limit", ["300"])[0]
                self._json(market_minutes_payload(unquote(minute_match.group(1)), limit))
            except (ValueError, TypeError) as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        match = re.fullmatch(r"/api/assets/([^/]+)/chart", parsed.path)
        if match:
            try:
                period = parse_qs(parsed.query).get("period", ["1d"])[0]
                payload = chart_payload(unquote(match.group(1)), period)
                self._json(payload, HTTPStatus.OK if payload else HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/" or parsed.path == "/index.html":
            self._file(STATIC_DIR / "index.html")
            return
        self._file(STATIC_DIR / parsed.path.lstrip("/"))

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 5_000_000:
                raise ValueError("invalid body size")
            payload = json.loads(self.rfile.read(length)) if length else {}
            if path == "/api/chat":
                result = send_message(str(payload.get("message", "")), payload.get("session_id"),
                                      str(payload.get("model") or "deepseek-v4-flash"))
                self._json(result)
                return
            if path == "/api/search/reindex":
                count = sync_semantic_index()
                self._json({"indexed": count, "semantic": semantic_status()})
                return
            if path == "/api/event-graphs":
                result = persist_event_graph(payload["graph"], payload.get("nodes", []), payload.get("edges", []))
                self._json(result, HTTPStatus.CREATED)
                return
            if path == "/api/research/backtest":
                self._json(run_backtest(str(payload.get("strategy_key", "")), str(payload.get("symbol", "510300"))))
                return
            if path == "/api/research/factors/run":
                self._json(run_factor(str(payload.get("factor_key", ""))))
                return
            if path == "/api/collect/bilibili":
                self._json(collect_bilibili(str(payload.get("target", "")), bool(payload.get("use_browser", False))))
                return
            if path == "/api/collect/x":
                self._json(collect_x_recent(str(payload.get("query", "")), int(payload.get("max_results", 10))))
                return
            if path == "/api/collect/sec":
                self._json(collect_sec_submissions(str(payload.get("cik", ""))))
                return
            if path == "/api/reports/refresh":
                self._json(trigger_report_refresh())
                return
            if path != "/api/risk/evaluate":
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            lines = calculate_risk_lines(
                float(payload["cost_price"]), float(payload["current_price"]),
                float(payload.get("highest_since_entry", payload["current_price"])),
                str(payload["market"]), str(payload["asset_type"]),
            )
            self._json({"lines": lines, "discipline": evaluate_discipline(float(payload["current_price"]), lines)})
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except (RuntimeError, ImportError, ModuleNotFoundError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)


def run(host="127.0.0.1", port=8765):
    initialize()
    refresher = None
    report_refresher = None
    if os.environ.get("ROOFTOP_LIVE_REFRESH_ENABLED", "1") == "1":
        refresher = MarketDataRefresher()
        refresher.start()
    if os.environ.get("ROOFTOP_REPORT_REFRESH_ENABLED", "1") == "1":
        report_refresher = ReportLibraryRefresher()
        report_refresher.start()
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"RoofTop Investment Harness（天台智投）running at http://{host}:{port}")
    print("Analysis only. Order execution is intentionally disabled.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if refresher:
            refresher.stop()
        if report_refresher:
            report_refresher.stop()
        server.server_close()


if __name__ == "__main__":
    run()
