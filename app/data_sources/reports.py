"""Slow, resilient research-report and disclosure collector."""

from __future__ import annotations

import json
import threading
from contextlib import closing
from datetime import datetime, timezone
from typing import Callable

from ..db import connect
from ..persistence import persist_source_documents

WATCHLIST = {
    "funds": ("510300", "159915"),
    # Representatives are used only as theme research anchors, not as holdings.
    "research_equities": ("600519", "601600", "603019", "002747", "688777"),
}
_refresh_lock = threading.Lock()
_refresh_state = {"status": "IDLE", "started_at": None, "completed_at": None, "documents": 0, "errors": []}


def _string(value) -> str | None:
    if value is None:
        return None
    try:
        if value != value:
            return None
    except Exception:
        pass
    return str(value)


def _rows_to_documents(frame, document_type: str, title_column: str, url_column: str | None,
                       source_name: str, date_column: str | None, metadata_columns: list[str]) -> list[dict]:
    documents = []
    if date_column and date_column in frame.columns:
        frame = frame.sort_values(date_column, ascending=False)
    for row in frame.head(30).to_dict("records"):
        title = _string(row.get(title_column)) or "未命名研究资料"
        metadata = {column: _string(row.get(column)) for column in metadata_columns if column in row}
        body = "；".join(f"{key}：{value}" for key, value in metadata.items() if value)
        documents.append({"document_type": document_type, "title": title, "body": f"（事实）{body}",
                          "source_url": _string(row.get(url_column)) if url_column else None,
                          "source_name": source_name,
                          "published_at": _string(row.get(date_column)) if date_column else None,
                          "observed_at": datetime.now(timezone.utc).isoformat(), "metadata": metadata})
    return documents


def _collect_one(source_code: str, dataset: str, query_key: str, fetch: Callable, converter: Callable) -> dict:
    frame = fetch()
    documents = converter(frame)
    raw = frame.to_json(orient="records", force_ascii=False, date_format="iso")
    return persist_source_documents(source_code, dataset, query_key, documents, raw)


def refresh_report_library() -> dict:
    """Poll all configured free sources; one broken upstream never aborts others."""
    if not _refresh_lock.acquire(blocking=False):
        return {**_refresh_state, "status": "RUNNING"}
    import akshare as ak
    results, errors = [], []
    _refresh_state.update(status="RUNNING", started_at=datetime.now(timezone.utc).isoformat(), errors=[])
    try:
        for symbol in WATCHLIST["funds"]:
            try:
                results.append(_collect_one(
                "akshare", "fund_announcement", symbol,
                lambda symbol=symbol: ak.fund_announcement_report_em(symbol=symbol),
                lambda frame: _rows_to_documents(frame, "fund_announcement", "公告标题", None, "东方财富基金公告",
                                                 "公告日期", ["基金代码", "基金名称", "公告日期", "报告ID"]),
                ))
            except Exception as exc:
                errors.append({"dataset": "fund_announcement", "symbol": symbol, "error": repr(exc)})
        for symbol in WATCHLIST["research_equities"]:
            try:
                results.append(_collect_one(
                "akshare", "research_report", symbol,
                lambda symbol=symbol: ak.stock_research_report_em(symbol=symbol),
                lambda frame: _rows_to_documents(frame, "broker_research", "报告名称", "报告PDF链接", "东方财富研报",
                                                 "日期", ["股票代码", "股票简称", "机构", "东财评级", "行业", "日期"]),
                ))
            except Exception as exc:
                errors.append({"dataset": "research_report", "symbol": symbol, "error": repr(exc)})
        try:
            date_key = datetime.now().strftime("%Y%m%d")
            results.append(_collect_one(
                "akshare", "macro_calendar", date_key,
                lambda: ak.macro_info_ws(date=date_key),
                lambda frame: _rows_to_documents(frame, "macro_calendar", "事件", "链接", "华尔街见闻财经日历",
                                                 "时间", ["时间", "地区", "重要性", "今值", "预期", "前值"]),
            ))
        except Exception as exc:
            errors.append({"dataset": "macro_calendar", "symbol": "GLOBAL", "error": repr(exc)})
        status = "SUCCESS" if not errors else ("DEGRADED" if results else "FAILED")
        result = {"status": status, "completed_at": datetime.now(timezone.utc).isoformat(),
                  "queries": len(results), "documents": sum(item["rows"] for item in results), "errors": errors}
        _refresh_state.update(result)
        return result
    finally:
        _refresh_lock.release()


def trigger_report_refresh() -> dict:
    if _refresh_lock.locked():
        return {**_refresh_state, "status": "RUNNING", "started": False}
    thread = threading.Thread(target=refresh_report_library, name="rooftop-report-manual", daemon=True)
    thread.start()
    return {**_refresh_state, "status": "RUNNING", "started": True}


def report_library_payload(limit: int = 80) -> dict:
    with closing(connect()) as conn:
        rows = [dict(row) for row in conn.execute(
            """SELECT id,document_type,title,body,source_url,source_name,published_at,captured_at,metadata_json
               FROM source_documents WHERE document_type IN ('fund_announcement','broker_research','sec_filing_index','macro_calendar')
               ORDER BY COALESCE(published_at,captured_at) DESC LIMIT ?""", (limit,)
        )]
        last_run = conn.execute(
            """SELECT finished_at,status,row_count,error FROM ingestion_runs
               WHERE dataset IN ('fund_announcement','research_report','filings','macro_calendar') ORDER BY id DESC LIMIT 1"""
        ).fetchone()
    for row in rows:
        row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    return {"documents": rows, "last_run": dict(last_run) if last_run else None,
            "refresh": dict(_refresh_state),
            "poll_seconds": 300, "watchlist": WATCHLIST}
